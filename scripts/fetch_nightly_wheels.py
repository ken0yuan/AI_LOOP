#!/usr/bin/env python3
"""Standalone AI_LOOP nightly wheel discovery, pairing, download and validation.

No dependency on npu-test-infra. Authentication stays in the existing obsutil
configuration; this module neither loads nor prints credentials.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from email.parser import Parser
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import zipfile

try:
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.tags import compatible_tags, cpython_tags
    from packaging.utils import canonicalize_name, parse_wheel_filename
    from packaging.version import Version
except ImportError:
    from pip._vendor.packaging.markers import default_environment
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.specifiers import SpecifierSet
    from pip._vendor.packaging.tags import compatible_tags, cpython_tags
    from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename
    from pip._vendor.packaging.version import Version

from obs_downloader import make_downloader
from wheel_artifacts import wheel_identity, select_bundle
import preparation_state as task_state
import fetch_latest_sources as source_fetch

ROOT = Path(__file__).resolve().parents[1]


def load_provider(config):
    if any(key in config for key in ('infrastructure_root', 'infrastructure_settings')):
        raise ValueError('Legacy infrastructure_* settings are unsupported; configure AI_LOOP obs directly')
    obs = config.get('obs')
    if not obs or obs.get('downloader', {}).get('type') != 'obsutil':
        raise ValueError('AI_LOOP defaults need an explicit obs.downloader of type obsutil')
    prefix = config.get('obs_prefix', '')
    if not prefix.startswith('obs://' + obs.get('bucket', '') + '/'):
        raise ValueError('obs.bucket and obs_prefix must refer to the same bucket')
    downloader = make_downloader(obs)
    return downloader, wheel_identity, select_bundle


def write_json(path, value):
    """Replace a JSON file atomically; a partial run never publishes a ready handoff."""
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.' + path.name, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)



@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield



def target_profile(config):
    if not re.fullmatch(r'3\.\d+\.\d+', config['python_version']):
        raise ValueError('python_version must be a full CPython 3.x.y version')
    keys = ('chip_type', 'os', 'architecture')
    matches = [p for p in config['wheel_profiles']
               if all(p.get(k) == config.get(k) for k in keys)]
    if len(matches) != 1:
        raise ValueError('No unique wheel profile for chip_type + os + architecture')
    return matches[0]



def select_latest_pair(listing, config, identity, select_bundle):
    """Use local dynamic-name pairing and reject incomplete candidates."""
    profile = target_profile(config)
    py = ''.join(config['python_version'].split('.')[:2])
    selected_listing = {}
    for url, info in listing.items():
        item = identity(url)
        if (url.startswith(config['obs_prefix']) and item
                and item.get('python_tag') == py
                and item.get('architecture') == config['architecture']
                and all(t in url.rsplit('/', 1)[-1] for t in profile['wheel_name_contains'])):
            selected_listing[url] = info
    commits = sorted({identity(url)['pytorch_short'] for url in selected_listing})
    candidates = []
    for commit in commits:
        bundle = select_bundle(selected_listing, config['obs_prefix'].rstrip('/'), commit,
                               {'obs': {'wheel_name_contains': []}})
        main = bundle['wheels']
        if not main:
            continue
        main_identity = identity(main)
        if main_identity['format'] == 'split-pytorch' and not bundle['wheels_npu']:
            continue
        urls = [u for u in (main, bundle['wheels_npu']) if u]
        times = []
        for url in urls:
            timestamp = datetime.fromisoformat(selected_listing[url]['last_modified'].replace('Z', '+00:00'))
            if timestamp.tzinfo is None or not selected_listing[url].get('etag'):
                raise ValueError('OBS objects need a timezone-aware upload time and ETag')
            times.append(timestamp.timestamp())
        candidates.append((max(times), tuple(urls), bundle))
    if not candidates:
        raise ValueError('No complete paired wheels match Python + chip/OS profile + architecture')
    _, urls, bundle = max(candidates, key=lambda item: (item[0], item[1]))
    return {'bundle': bundle, 'objects': {u: selected_listing[u] for u in urls},
            'identities': {u: identity(u) for u in urls}}



def supported_tags(config):
    profile = target_profile(config)
    arch = config['architecture']
    version = tuple(map(int, config['python_version'].split('.')[:2]))
    glibc = tuple(map(int, profile['glibc'].split('.')))
    if glibc[0] != 2:
        raise ValueError('Profile needs supported glibc 2.x')
    platforms = [f'manylinux_2_{n}_{arch}' for n in range(glibc[1], 4, -1)]
    if glibc[1] >= 17:
        platforms.append('manylinux2014_' + arch)
    if arch == 'x86_64':
        platforms += ['manylinux2010_x86_64', 'manylinux1_x86_64']
    platforms.append('linux_' + arch)
    return set(cpython_tags(version, platforms=platforms)) | set(compatible_tags(version, platforms=platforms))



def wheel_info(path, config):
    name, filename_version, _, tags = parse_wheel_filename(path.name)
    if not tags.intersection(supported_tags(config)):
        raise ValueError('Wheel tags do not match target: ' + path.name)
    with zipfile.ZipFile(path) as wheel:
        entries = [n for n in wheel.namelist() if n.endswith('.dist-info/METADATA')]
        if len(entries) != 1:
            raise ValueError('Wheel must contain exactly one METADATA')
        meta = Parser().parsestr(wheel.read(entries[0]).decode('utf-8'))
        if canonicalize_name(meta['Name']) != canonicalize_name(name):
            raise ValueError('Wheel filename/metadata name mismatch')
        if Version(meta['Version']) != filename_version:
            raise ValueError('Wheel filename/metadata version mismatch')
        if meta['Requires-Python'] and not SpecifierSet(meta['Requires-Python']).contains(config['python_version']):
            raise ValueError('Requires-Python does not match target')
        module = 'torch' if canonicalize_name(name) == 'torch' else 'torch_npu'
        version_file = module + '/version.py'
        commit = None
        if version_file in wheel.namelist():
            tree = ast.parse(wheel.read(version_file).decode('utf-8'))
            for node in tree.body:
                if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                        and any(isinstance(t, ast.Name) and t.id == 'git_version' for t in node.targets)
                        and isinstance(node.value.value, str)
                        and re.fullmatch('[0-9a-fA-F]{40}', node.value.value)):
                    commit = node.value.value.lower()
    return {'name': canonicalize_name(meta['Name']), 'version': meta['Version'],
            'requires_dist': meta.get_all('Requires-Dist', []), 'commit': commit,
            'path': str(path), 'sha256': sha256(path)}



def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()



def download_wheels(selection, downloader, config, *, data_root=None):
    data_root = Path(data_root) if data_root is not None else ROOT / 'data'
    folder=data_root/'wheels'
    if folder.is_symlink():
        raise ValueError('Wheel output directory must not be a symlink')
    folder.mkdir(parents=True,exist_ok=True)
    found={}
    staged={}
    # Neither downloads, locks nor receipts are stored in the wheel output directory.
    with locked(data_root/'.wheel-download.lock'), tempfile.TemporaryDirectory(prefix='.wheel-download-',dir=data_root) as temporary:
        staging=Path(temporary)
        for index,(url,info) in enumerate(selection['objects'].items()):
            archive=staging/(str(index)+'.tar.gz')
            before=downloader.stat(url)
            if not before or before.get('etag')!=info['etag']:
                raise ValueError('OBS object changed since listing; retry preparation')
            downloader.download(url,archive)
            after=downloader.stat(url)
            if not after or after.get('etag')!=info['etag']:
                raise ValueError('OBS object changed during download; retry preparation')
            with tarfile.open(archive,'r:gz') as tar:
                for member in tar.getmembers():
                    basename=Path(member.name).name
                    if not basename.endswith('.whl'):
                        continue
                    if not member.isfile() or basename in staged:
                        raise ValueError('Duplicate or non-regular wheel in selected bundle')
                    dest=staging/basename
                    with tar.extractfile(member) as source,dest.open('xb') as output:
                        import shutil
                        shutil.copyfileobj(source,output)
                    metadata=wheel_info(dest,config)
                    name=metadata['name']
                    staged[basename]=(dest,metadata)
                    if name in ('torch','torch-npu'):
                        if name in found:
                            raise ValueError('Duplicate framework wheel in selected bundle')
                        identity=selection['identities'][url]
                        short=identity.get('pytorch_short' if name=='torch' else 'torch_npu_short')
                        if short and metadata['commit'] and not metadata['commit'].startswith(short):
                            raise ValueError('Packaged Git commit does not match OBS bundle identity')
                        found[name]=metadata
        if set(found)!={'torch','torch-npu'}:
            raise ValueError('Selected bundle does not contain exactly torch and torch-npu')
        environment=default_environment()
        environment.update(python_version='.'.join(config['python_version'].split('.')[:2]),
                           python_full_version=config['python_version'],sys_platform='linux',
                           os_name='posix',platform_system='Linux',platform_machine=config['architecture'])
        for raw in found['torch-npu']['requires_dist']:
            requirement=Requirement(raw)
            if (canonicalize_name(requirement.name)=='torch'
                    and (not requirement.marker or requirement.marker.evaluate(environment))
                    and not requirement.specifier.contains(Version(found['torch']['version']),prereleases=True)):
                raise ValueError('torch-npu wheel requires incompatible torch: '+raw)
        for basename in staged:
            target=folder/basename
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise ValueError('Wheel destination is not a regular file: '+str(target))
        for basename,(source,metadata) in staged.items():
            target=folder/basename
            os.replace(source,target)
            metadata['path']=str(target.resolve())
            print('Wheel saved: '+task_state.relative_path(target, ROOT),flush=True)
    return found


def resolve(config):
    target_profile(config)
    downloader, identity, selector = load_provider(config)
    listing = downloader.list(config['obs_prefix'])
    # A complete framework input also needs the matching PyTorch source archive.
    filtered={url:meta for url,meta in listing.items() if not identity(url)
              or config['obs_prefix'].rstrip('/')+'/pytorch-source-'+identity(url)['pytorch_short']+'.tar.gz' in listing}
    selection=select_latest_pair(filtered,config,identity,selector)
    short=next(iter(selection['identities'].values()))['pytorch_short']
    source_url=config['obs_prefix'].rstrip('/')+'/pytorch-source-'+short+'.tar.gz'
    selection['pytorch_source']={'url':source_url,'commit':short,**listing[source_url]}
    return downloader,selection


def fetch_for_task(args):
    if args.defaults is not None or any(getattr(args,k) is not None for k in task_state.ENVIRONMENT):
        raise ValueError('Task mode reads target/configuration from basic_information.json; omit defaults and target overrides')
    path, basic = task_state.load_basic(args.basic_information, args.data_root)
    context = nullcontext() if args.dry_run else task_state.locked(path.parent/'log/.step0-entry.lock')
    with context:
        path, basic = task_state.load_basic(path, args.data_root)
        phase='selection'
        try:
            if not args.dry_run:
                basic = task_state.reset_environment(basic)
                task_state.write_json(path, basic)
            config = task_state.task_config(basic)
            downloader, selection = resolve(config)
            if args.dry_run:
                print(json.dumps({'basic_information':task_state.relative_path(path, ROOT),'selection':selection,'sources':{k:config[k] for k in ('pytorch_repo','torch_npu_repo')}},ensure_ascii=False,indent=2))
                return 0
            phase='sources'
            basic=dict(basic)
            for field in task_state.WHEEL_FIELDS:
                if field in basic: basic[field]=None
            basic=source_fetch.complete_sources(path,basic,data_root=args.data_root,downloader=downloader,selection=selection)
            phase='wheels'
            wheels = download_wheels(selection,downloader,config,data_root=args.data_root)
            phase='source-switch'
            checkout = source_fetch.complete_wheel_sources(wheels['torch-npu'],
                path.parent/'log/fetch-latest-sources.log',data_root=args.data_root,
                result_path=path.parent/'log/step0-source.json',ref=basic.get('torch_npu_ref'))
            sources = json.loads((path.parent/'log/step0-preparation.json').read_text())['sources']
            sources['torch_npu'].update(checkout, type='git-wheel')
            sources['torch_npu'].pop('main_branch', None)
            updates = {'pytorch_version':wheels['torch']['version'], 'pytorch_wheel':wheels['torch']['path'],
                       'torch_npu_version':wheels['torch-npu']['version'], 'torch_npu_wheel':wheels['torch-npu']['path'],
                       'pytorch_wheel_commit':wheels['torch']['commit'], 'torch_npu_wheel_commit':wheels['torch-npu']['commit']}
            updates['torch_npu_commit']=checkout['commit']
            updated=task_state.publish(path,basic,updates,{'selection':selection,'wheels':wheels,'sources':sources,
                        'wheel_source':'obs-latest-complete-bundle','wheels_completed_at':datetime.now(timezone.utc).isoformat()},root=ROOT)
            print(updated['preparation_status']+': '+task_state.relative_path(path, ROOT))
            return 0
        except Exception as error:
            if not args.dry_run:
                task_state.record_failure(path,phase,error)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--defaults', type=Path)
    parser.add_argument('--basic-information', type=Path, help='Complete source and wheel fields in this task JSON')
    parser.add_argument('--data-root', type=Path, default=ROOT / 'data')
    for field in ('python-version', 'chip-type', 'os', 'architecture'):
        parser.add_argument('--' + field)
    parser.add_argument('--dry-run', action='store_true', help='Only list actual objects and resolve a complete bundle')
    args = parser.parse_args(argv)
    try:
        if args.basic_information is not None:
            return fetch_for_task(args)
        config = json.loads((args.defaults or ROOT/'api-defaults.json').read_text(encoding='utf-8'))
        for key in ('python_version', 'chip_type', 'os', 'architecture'):
            if getattr(args, key) is not None:
                config[key] = getattr(args, key)
        downloader, selection = resolve(config)
        record = {'source': 'obs-latest-complete-bundle',
                  'target': {k: config[k] for k in ('python_version', 'chip_type', 'os', 'architecture')},
                  'selection': selection}
        if args.dry_run:
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 0
        with locked(args.data_root / '.fetch-framework-inputs.lock'):
            log=args.data_root/'source_code/fetch-framework-inputs.log'
            record['sources']=source_fetch.prepare_sources(config,selection,downloader,log,data_root=args.data_root)
            record['wheels'] = download_wheels(selection, downloader, config, data_root=args.data_root)
            checkout = source_fetch.complete_wheel_sources(record['wheels']['torch-npu'],
                log,data_root=args.data_root)
            record['sources']['torch_npu'].update(checkout, type='git-wheel')
            record['sources']['torch_npu'].pop('main_branch', None)
            record['completed_at'] = datetime.now(timezone.utc).isoformat()
            record['status'] = 'WHEELS_READY'
        print('WHEELS_READY: '+task_state.relative_path(args.data_root/'wheels', ROOT))
        return 0
    except Exception as error:
        print('WHEEL_FETCH_FAILED: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
