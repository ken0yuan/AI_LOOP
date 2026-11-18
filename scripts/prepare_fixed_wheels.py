"""Acquire exact-release framework wheels and prepare their reusable NPU environment."""
import argparse
import ast
import json
import os
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from pip._vendor import requests
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.version import Version
from pip._vendor.packaging.utils import parse_wheel_filename, InvalidWheelFilename

import fetch_latest_sources as sources
import fetch_nightly_wheels as framework
import install_env as installer
import preparation_state as state

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'data'
RELEASE_INDEX = 'https://repo.huaweicloud.com/repository/pypi/simple/'
CPU_INDEX = 'https://mirror.nju.edu.cn/pytorch/whl/cpu/'
NIGHTLY_INDEX = 'https://mirrors.huaweicloud.com/ascend/repos/pypi/nightly/'


class IndexLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and 'href' in attrs and 'data-yanked' not in attrs:
            self.hrefs.append(attrs['href'])


def mirror_links(index, package):
    url = urljoin(index, package+'/')
    response = requests.get(url, timeout=(12, 60))
    if response.status_code == 404:
        return []
    response.raise_for_status()
    page = IndexLinks()
    page.feed(response.text)
    if not page.hrefs and '<html' not in response.text.lower():
        raise RuntimeError('Invalid package index response: '+url)
    return [urljoin(response.url, h) for h in page.hrefs]


def package_candidates(links, package, release, tags, prerelease):
    hits = []
    for url in links:
        filename = unquote(urlsplit(url).path.rsplit('/', 1)[-1])
        if '/' in filename or chr(92) in filename:
            continue
        try:
            name, version, _, platforms = parse_wheel_filename(filename)
        except InvalidWheelFilename:
            continue
        if name != package or not tags.intersection(platforms) or version.is_prerelease != prerelease:
            continue
        match = (SpecifierSet('=='+str(release)).contains(version) and version.local in (None, 'cpu')
                 if name == 'torch' else Version(version.base_version) == Version(release.base_version))
        if match:
            hits.append((version, filename, url))
    return sorted(hits, reverse=True)


def inspect_candidate(path, package, config, release):
    if package == 'torch':
        # Some unlabelled PyPI wheels contain CUDA, even on aarch64.
        with zipfile.ZipFile(path) as wheel:
            tree = ast.parse(wheel.read('torch/version.py').decode())
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id in ('cuda','hip') for t in targets):
                    if not isinstance(node.value, ast.Constant) or node.value.value is not None:
                        return None
    info = framework.wheel_info(path, config)
    if not info['commit']:
        raise ValueError(f'{path.name} has no usable build commit')
    for dependency in info['requires_dist']:
        req = Requirement(dependency)
        if (package == 'torch-npu' and req.name.lower() == 'torch'
                and (not req.marker or req.marker.evaluate({'python_version':'.'.join(config['python_version'].split('.')[:2]),
                                                           'python_full_version':config['python_version'],
                                                           'platform_machine':config['architecture']}))
                and not req.specifier.contains(release, prereleases=True)):
            return None
    return info


def choose_wheel(package, release, config, directory):
    tags = framework.supported_tags(config)
    local = [p.as_uri() for p in directory.glob('*.whl') if p.is_file() and not p.is_symlink()]
    channels = [(False, CPU_INDEX if package == 'torch' else RELEASE_INDEX)]
    if package == 'torch-npu':
        channels.append((True, NIGHTLY_INDEX))
    for pre, index in channels:
        for _, name, url in package_candidates(local, package, release, tags, pre):
            info = inspect_candidate(directory/name, package, config, release)
            if info:
                return info
        for _, name, url in package_candidates(mirror_links(index, package), package, release, tags, pre):
            if (directory/name).is_file():  # already inspected
                continue
            parts = urlsplit(url)
            if parts.scheme != 'https' or parts.hostname != urlsplit(index).hostname:
                # Some mirror indexes also list upstream-only files.
                continue
            with tempfile.TemporaryDirectory(dir=directory.parent, prefix='.fixed-wheel-') as staging:
                path = Path(staging)/name
                with requests.get(url, stream=True, timeout=(15, 120)) as response:
                    response.raise_for_status()
                    with path.open('xb') as out:
                        shutil.copyfileobj(response.raw, out)
                info = inspect_candidate(path, package, config, release)
                if not info:
                    continue
                expected = parse_qs(parts.fragment).get('sha256', [])
                if expected and info['sha256'] not in expected:
                    raise ValueError('Downloaded wheel checksum mismatch: '+name)
                target = directory/name
                os.replace(path, target)
                info['path'] = str(target)
                return info
    raise ValueError(f'No compatible {package} wheel for torch {release} and this Python/platform')


PROBE = """
import importlib.metadata as md, json, platform, torch
if torch.version.cuda or torch.version.hip:
    raise ValueError('NPU environment requires a CPU PyTorch build')
import torch_npu
from pathlib import Path
import sys
result = {'python': platform.python_version(), 'packages': {}}
for name, mod in [('torch',torch),('torch-npu',torch_npu)]:
    if not Path(mod.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
        raise ValueError('Import outside venv: '+name)
    dist = md.distribution(name)
    if mod.__version__.lower().removesuffix('+gitunknown') not in (dist.version.lower(), dist.version.lower()+'+cpu'):
        raise ValueError('Imported framework version differs from metadata: '+name)
    result['packages'][name] = {'version':dist.version,'commit':mod.version.git_version,
        'direct':json.loads(dist.read_text('direct_url.json') or '{}').get('url'),
        'tags':dist.read_text('WHEEL')}
print(json.dumps(result))
"""


def inspect_venv(exe, env):
    observed = subprocess.run([exe, '-I', '-B', '-c', PROBE], env=env, text=True,
                              capture_output=True, timeout=120)
    if observed.returncode:
        raise RuntimeError('Framework import failed: '+observed.stderr[:1600]+' '+observed.stderr[-400:])
    return json.loads(observed.stdout.splitlines()[-1])


def installed_wheels(observed, receipt, directory):
    # The paths identify the original wheels. They need not still exist in the cache.
    wheels = {}
    for name, pkg in observed.items():
        if not re.fullmatch('[a-fA-F0-9]{40}', pkg['commit'] or ''):
            raise ValueError('Missing installed build commit for '+name)
        old = receipt.get(name, {})
        if old.get('version') == pkg['version'] and old.get('commit') == pkg['commit']:
            filename = Path(old['path']).name
        else:
            filename = unquote(urlsplit(pkg['direct'] or '').path.rsplit('/', 1)[-1])
            if not filename.endswith('.whl'):
                # Reconstruct the cache name from installed wheel metadata for index-installed venvs.
                from email.parser import Parser
                meta = Parser().parsestr(pkg['tags'])
                tag_list = [tag.split('-') for tag in meta.get_all('Tag', [])]
                if not tag_list:
                    raise ValueError('Missing installed wheel tags for '+name)
                tag = '-'.join('.'.join(sorted(set(column))) for column in zip(*tag_list))
                build = meta.get('Build')
                filename = '-'.join([name.replace('-','_'), pkg['version'], *([build] if build else []), tag])+'.whl'
        wheels[name] = {'name':name,'version':pkg['version'],'commit':pkg['commit'],
                        'path':str(directory/filename)}
    return wheels


def check_dependencies(exe, requirements, env):
    # Let pip evaluate nested files, constraints, markers and extras against this interpreter offline.
    cmd = [exe, '-m', 'pip', 'install', '--dry-run', '--no-index', '--report', '-',
           '--quiet', '-r', requirements]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    return p.returncode == 0 and not json.loads(p.stdout)['install']


def release_directory_version(release):
    public = release.public
    if len(release.release) == 2:
        base = release.base_version
        return base + '.0' + public[len(base):]
    return public


def fixed_pytorch_source(data, version):
    public = release_directory_version(Version(version))
    source = data/'source_code'/('pytorch-' + public)
    if not source.is_dir():
        raise ValueError(f'Missing PyTorch source directory: {source}')
    return source


def ensure_environment(run_dir, config, release):
    public = release_directory_version(release)
    venv = DATA/'venv'/('torch-'+public)/('python-'+config['python_version'])
    exe = venv/'bin/python'
    requirements = run_dir/'requirements.txt'
    wheel_dir = DATA/'wheels'
    log = run_dir/'log/install-env.log'
    env, cann = installer.installation_environment()
    for key in ('PIP_EXTRA_INDEX_URL','PIP_FIND_LINKS','PIP_NO_INDEX'):
        env.pop(key, None)
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_INDEX_URL=RELEASE_INDEX)
    receipt_path = venv/'.step0-wheels.json'
    receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
    observed = None
    if exe.is_file():
        # A failed previous installation can leave a partial venv.
        if installer.run([exe,'-I','-c','import platform;print(platform.python_version())']) != config['python_version']:
            raise ValueError('Python version mismatch in '+str(venv))
        try:
            observed = inspect_venv(exe, env)
        except RuntimeError:
            observed = None
    reuse = observed is not None and SpecifierSet('=='+str(release)).contains(observed['packages']['torch']['version'])
    if reuse:
        wheels = installed_wheels(observed['packages'], receipt, wheel_dir)
        if not check_dependencies(exe, requirements, env):
            limits = run_dir/'log/installed-framework-constraints.txt'
            limits.write_text(''.join(n+'=='+w['version']+'\n' for n,w in wheels.items()))
            installer.run([exe,'-m','pip','install','-c',limits,'-r',requirements],env=env,log=log)
            if not check_dependencies(exe, requirements, env):
                raise RuntimeError('Task requirements remain unsatisfied')
            after = inspect_venv(exe, env)
            if after['packages'] != observed['packages']:
                raise RuntimeError('Dependency repair changed an installed framework package')
    else:
        with state.locked(DATA/'.wheel-download.lock'):
            wheel_dir.mkdir(parents=True,exist_ok=True)
            if wheel_dir.is_symlink():
                raise ValueError('Wheel output directory must not be a symlink')
            wheels = {n:choose_wheel(n,release,config,wheel_dir) for n in ('torch','torch-npu')}
        if not exe.is_file():
            python = installer.find_python(config['python_version'])
            installer.run([python,'-m','venv',venv],env=env,log=log)
        effective = run_dir/'log/requirements-effective.txt'
        effective.write_text(installer.effective_requirements(requirements,{n:Path(w['path']) for n,w in wheels.items()}))
        installer.run([exe,'-m','pip','install','-r',effective],env=env,log=log,timeout=1800)
        observed = inspect_venv(exe, env)
        for name, info in wheels.items():
            pkg = observed['packages'][name]
            if Version(pkg['version']) != Version(info['version']) or pkg['commit'] != info['commit']:
                raise ValueError('Imported framework differs from selected wheel: '+name)
    if not reuse or not receipt:
        installer.run([exe,'-I','-c','import torch,torch_npu; assert (torch.ones(1,device="npu")+1).item()==2'],
                      env=env,log=log)
    state.write_json(receipt_path,{n:dict(w,path=state.relative_path(w['path'],ROOT)) for n,w in wheels.items()})
    return venv, env, cann, wheels


def prepare(path):
    path, basic = state.load_basic(path, DATA)
    with state.locked(path.parent/'log/.step0-entry.lock'):
        path, basic = state.load_basic(path, DATA)
        release = state.fixed_torch_release(path, basic)
        if release is None:
            raise ValueError('Fixed preparation requires an active exact torch release in requirements.txt')
        config = state.task_config(basic)
        phase = 'fixed-environment'
        try:
            state.write_json(path,state.reset_environment(dict(basic,preparation_status='BASIC_READY')))
            # Lock across aliases of a release and across the whole handoff for this interpreter.
            lock_name = '.fixed-'+'.'.join(map(str,(*release.release,0,0)[:3]))+'-py'+config['python_version']+'.lock'
            with state.locked(DATA/'venv'/lock_name):
                venv, env, cann, wheels = ensure_environment(path.parent,config,release)
                phase = 'fixed-sources'
                with state.locked(DATA/'.fetch-framework-inputs.lock'):
                    log = path.parent/'log/fetch-latest-sources.log'
                    pytorch = fixed_pytorch_source(DATA, wheels['torch']['version'])
                    npu = sources.refresh_torchnpu(config['torch_npu_repo'],log,data_root=DATA,
                                                   fork_remote=config.get('torch_npu_fork_repo'))
                    basic.update(pytorch_source=str(pytorch), pytorch_source_commit=None,
                                 torch_npu_latest_source=npu['path'],torch_npu_main_commit=npu['commit'])
                    updated = framework.publish_wheel_inputs(path,basic,{},wheels,DATA,
                                                            'fixed-release',sources={'pytorch':{'path':str(pytorch)},'torch_npu':npu})
                    if updated['preparation_status'] != 'INPUTS_READY':
                        raise RuntimeError('Incomplete framework inputs')
                    installer.finish_environment(path,venv,env,cann)
        except Exception as exc:
            state.record_failure(path,phase,exc)
            raise
    print('INPUTS_READY / ENV_READY: '+state.relative_path(path,ROOT))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--basic-information', required=True, type=Path)
    try:
        prepare(parser.parse_args().basic_information)
    except Exception as error:
        sys.exit('FIXED_PREPARATION_FAILED: '+str(error))
