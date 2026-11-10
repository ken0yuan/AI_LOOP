#!/usr/bin/env python3
"""Replace fixed PyTorch sources from OBS and pull torch-npu's actual mainline."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from preparation_state import locked, load_basic, task_config, publish, record_failure, sha256, write_json, relative_path, relative_metadata, WHEEL_FIELDS, reset_environment

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'data'


def command(argv, log, *, check=True):
    log=Path(log);log.parent.mkdir(parents=True,exist_ok=True)
    result=subprocess.run([str(x) for x in argv],capture_output=True,text=True,
                          encoding='utf-8',errors='replace',env=dict(os.environ,GIT_TERMINAL_PROMPT='0'),timeout=900)
    with log.open('a',encoding='utf-8') as out:
        out.write('$ '+shlex.join([str(x) for x in argv])+'\n'+result.stdout+result.stderr+'\n')
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip()[-2000:] or result.stdout.strip()[-2000:])
    return result


def fixed_target(data_root, name):
    data=Path(data_root).resolve()
    cache=data/'source_code'
    if cache.is_symlink():
        raise ValueError('source_code must not be a symlink')
    cache.mkdir(parents=True,exist_ok=True)
    target=cache/name
    if target.is_symlink() or target.resolve()!=cache.resolve()/name:
        raise ValueError('Fixed source target must stay inside data/source_code: '+str(target))
    if target.exists() and not target.is_dir():
        raise ValueError('Source target is not a directory: '+str(target))
    return cache,target


def source_object(selection, config):
    info=selection.get('pytorch_source')
    if info:
        return info
    identity=next(iter(selection['identities'].values()))
    short=identity['pytorch_short']
    return {'url':config['obs_prefix'].rstrip('/')+'/pytorch-source-'+short+'.tar.gz','commit':short}


def replace_pytorch(config, selection, downloader, log, *, data_root):
    cache,target=fixed_target(data_root,'pytorch')
    source=source_object(selection,config)
    url=source['url'];short=source['commit']
    if not re.fullmatch(r'[0-9a-f]{7,40}',short):
        raise ValueError('Invalid PyTorch source commit identity')
    with locked(cache/'.step0-pytorch.lock'):
        before=downloader.stat(url)
        if not before or not before.get('etag'):
            raise ValueError('Matching PyTorch source archive is unavailable: '+url)
        if source.get('etag') and before['etag']!=source['etag']:
            raise ValueError('PyTorch source changed since listing; retry')
        archive=cache/'pytorch-source.tar.gz'
        receipt_path=cache/'pytorch-source.json'
        receipt=json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        cached=(archive.is_file() and receipt.get('url')==url and receipt.get('etag')==before['etag']
                and receipt.get('sha256')==sha256(archive))
        candidate=archive if cached else cache/'.pytorch-source.tar.gz.part'
        if not cached:
            downloader.download(url,candidate)
        after=downloader.stat(url)
        if not after or after.get('etag')!=before['etag']:
            raise ValueError('PyTorch source changed during download; retry')
        # Stage and validate before touching the fixed source directory.
        with tempfile.TemporaryDirectory(prefix='.pytorch-source-',dir=cache) as temporary:
            staging=Path(temporary)
            with tarfile.open(candidate,'r:gz') as tar:
                for member in tar.getmembers():
                    parts=PurePosixPath(member.name)
                    if parts.is_absolute() or '..' in parts.parts or '\\' in member.name:
                        raise ValueError('Unsafe PyTorch source archive member: '+member.name)
                    if parts.parts and parts.parts[0]!='pytorch':
                        raise ValueError('PyTorch source archive must have a pytorch/ root')
                tar.extractall(staging,filter='data')
            fresh=staging/'pytorch'
            if (fresh.is_symlink() or not (fresh/'torch').is_dir() or not (fresh/'test').is_dir()
                    or not (fresh/'setup.py').is_file()):
                raise ValueError('Downloaded archive is not a PyTorch source tree')
            fixed_target(data_root,'pytorch')
            if not cached:
                os.replace(candidate,archive)
            # This exact fixed directory is the replacement target authorized by the caller.
            if target.exists():
                shutil.rmtree(target)
            os.replace(fresh,target)
        result={'path':str(target),'commit':short,'identity_basis':'OBS object name','url':url,
                'etag':before['etag'],'last_modified':source.get('last_modified'),
                'archive':str(archive),'sha256':sha256(archive),'type':'obs-archive'}
        write_json(receipt_path,relative_metadata(result, ROOT))
        with Path(log).open('a',encoding='utf-8') as out:
            out.write('Replaced fixed PyTorch source: '+relative_path(target, ROOT)+' from '+url+'\n')
        return result


def refresh_torchnpu(remote, log, *, data_root, fork_remote=None):
    if fork_remote and fork_remote == remote:
        raise ValueError("The fork origin must differ from the primary upstream")
    cache,repo=fixed_target(data_root,'torch_npu')
    def git(*args,check=True):
        return command(['git','-C',repo,*args],log,check=check)
    # Share the repository lock with the version switcher.
    with locked(cache/'.torchnpu-source.lock'):
        exists=(repo/'.git').exists()
        if exists:
            if Path(git('rev-parse','--show-toplevel').stdout.strip()).resolve()!=repo.resolve():
                raise ValueError('torch_npu is not the repository root')
            if git('status','--porcelain').stdout.strip():
                raise ValueError('torch-npu has local changes; preserving them without pull')
            names=set(git('remote').stdout.splitlines())
            if 'upstream' in names:
                if git('remote','get-url','upstream').stdout.strip()!=remote:
                    raise ValueError('Existing torch-npu upstream differs; preserving it')
            elif 'origin' in names and git('remote','get-url','origin').stdout.strip()==remote:
                git('remote','rename','origin','upstream')
            else:
                # origin, when present, belongs to the fork and is preserved.
                git('remote','add','upstream',remote)
        elif repo.exists() and any(repo.iterdir()):
            raise ValueError('torch_npu directory is nonempty and is not a Git repository')
        head=command(['git','ls-remote','--symref',remote,'HEAD'],log).stdout
        match=re.search(r'^ref: refs/heads/(.+)\s+HEAD$',head,re.M)
        if not match:
            raise ValueError('Cannot resolve the actual torch-npu mainline branch')
        branch=match[1]
        command(['git','check-ref-format','--branch',branch],log)
        if not exists:
            command(['git','clone','--origin','upstream','--single-branch','--branch',branch,'--',remote,repo],log)
        if fork_remote:
            existing=git('remote','get-url','origin',check=False)
            if existing.returncode==0:
                if existing.stdout.strip()!=fork_remote:
                    raise ValueError('Existing torch-npu fork origin differs; preserving it')
            else:
                git('remote','add','origin',fork_remote)
        if exists:
            git('fetch','upstream','+refs/heads/'+branch+':refs/remotes/upstream/'+branch)
            remote_ref='refs/remotes/upstream/'+branch
            local_ref='refs/heads/'+branch
            if git('show-ref','--verify','--quiet',local_ref,check=False).returncode==0:
                if git('merge-base','--is-ancestor',local_ref,remote_ref,check=False).returncode:
                    raise ValueError('Local mainline diverged; refusing to reset local commits')
                git('checkout',branch)
                git('merge','--ff-only',remote_ref)
            else:
                git('checkout','-b',branch,'--track',remote_ref)
        return {'path':str(repo),'commit':git('rev-parse','HEAD').stdout.strip(),
                'main_branch':branch,'repo':remote,'type':'git-mainline'}


def prepare_sources(config, selection, downloader, log, *, data_root):
    Path(log).parent.mkdir(parents=True,exist_ok=True)
    pytorch=replace_pytorch(config,selection,downloader,log,data_root=data_root)
    npu=refresh_torchnpu(config['torch_npu_repo'],log,data_root=data_root,
                         fork_remote=config.get('torch_npu_fork_repo'))
    return {'pytorch':pytorch,'torch_npu':npu}


def ensure_torchnpu_commit(commit, log, *, data_root):
    """Finish downloading a wheel's source during Step 0, without changing HEAD."""
    if not commit:
        return
    if not re.fullmatch(r'[0-9a-fA-F]{7,40}', commit):
        raise ValueError('Invalid torch-npu wheel commit')
    cache, repo = fixed_target(data_root, 'torch_npu')
    with locked(cache/'.torchnpu-source.lock'):
        if not (repo/'.git').exists():
            raise ValueError('torch-npu repository is missing; prepare Step 0 sources first')
        def git(*args, check=True):
            return command(['git','-C',repo,*args],log,check=check)
        if git('cat-file','-e',commit+'^{commit}',check=False).returncode:
            git('fetch','--no-tags','upstream',commit)
        git('rev-parse','--verify',commit+'^{commit}')


def compatible(left, right):
    return left.lower().startswith(right.lower()) or right.lower().startswith(left.lower())


def switch_torchnpu_version(repo, version, commit, ref, packaged_commit, log):
    """Resolve and checkout locally; all source downloads belong to Step 0."""
    if commit and not re.fullmatch(r'[0-9a-fA-F]{7,40}', commit):
        raise ValueError('torch_npu_commit must be a Git commit ID')
    if commit and packaged_commit and not compatible(commit, packaged_commit):
        raise ValueError('torch_npu_commit does not match the supplied wheel')
    if ref and (ref.startswith('-') or '\n' in ref or '\r' in ref):
        raise ValueError('Invalid torch_npu_ref')
    if not repo.is_dir() or not (repo/'.git').exists():
        raise ValueError('torch-npu repository is missing; run Step 0 source preparation first')

    def git(*args, check=True):
        return command(['git', '-C', repo, *args], log, check=check)

    top = Path(git('rev-parse', '--show-toplevel').stdout.strip()).resolve()
    if top != repo.resolve():
        raise ValueError('Source directory is not the torch-npu repository root')
    if git('status', '--porcelain').stdout.strip():
        raise ValueError('Source has uncommitted/untracked changes; preserving them without checkout')
    target = ref or commit or packaged_commit
    if target is None:
        resolved = set()
        for tag in ('refs/tags/v'+version, 'refs/tags/'+version):
            result = git('rev-parse', '--verify', '--end-of-options', tag+'^{commit}', check=False)
            if result.returncode == 0:
                resolved.add(result.stdout.strip())
        if len(resolved) != 1:
            raise ValueError('No unambiguous local source version; finish source and wheel preparation first')
        selected = resolved.pop()
    else:
        result = git('rev-parse', '--verify', '--end-of-options', target+'^{commit}', check=False)
        if result.returncode:
            raise ValueError('Task source commit is missing locally; finish source and wheel preparation first')
        selected = result.stdout.strip()
    for expected in (commit, packaged_commit):
        if expected and not compatible(selected, expected):
            raise ValueError('Selected source does not match the task/wheel commit')
    git('cat-file', '-e', selected+':torch_npu/__init__.py')
    git('checkout', 'master')
    git('checkout', '--detach', selected)
    return {'status':'SOURCE_READY', 'path':relative_path(repo, ROOT),
            'version':version, 'commit':selected}


def complete_wheel_sources(wheel, log, *, data_root, result_path=None, ref=None):
    """Acquire and switch to the selected wheel source before publishing ready inputs."""
    result_path = Path(result_path) if result_path is not None else None
    if result_path is not None:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(result_path, {'status':'SOURCE_SWITCHING'})
    try:
        ensure_torchnpu_commit(wheel['commit'], log, data_root=data_root)
        cache, repo = fixed_target(data_root, 'torch_npu')
        with locked(cache/'.torchnpu-source.lock'):
            result = switch_torchnpu_version(repo, wheel['version'], wheel['commit'],
                                             ref, wheel['commit'], Path(log))
        result['completed_at'] = datetime.now(timezone.utc).isoformat()
        if result_path is not None:
            write_json(result_path, result)
        return result
    except Exception as error:
        if result_path is not None:
            write_json(result_path, {'status':'FAILED', 'error':str(error)})
        raise


def complete_sources(path, basic, *, data_root=None, downloader=None, selection=None):
    if downloader is None or selection is None:
        raise ValueError('Source replacement requires the selected wheel bundle and OBS downloader')
    config=task_config(basic)
    sources=prepare_sources(config,selection,downloader,path.parent/'log/fetch-latest-sources.log',data_root=data_root or DATA)
    updates={'pytorch_source':sources['pytorch']['path'],'pytorch_source_commit':sources['pytorch']['commit'],
             'torch_npu_latest_source':sources['torch_npu']['path'],'torch_npu_main_commit':sources['torch_npu']['commit']}
    updates.update({field:None for field in WHEEL_FIELDS if field in basic})
    if 'pytorch_main_commit' in basic:
        updates['pytorch_main_commit']=None
    return publish(path,basic,updates,{'sources':sources,'sources_completed_at':datetime.now(timezone.utc).isoformat()},root=ROOT)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--basic-information',required=True,type=Path)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(argv)
    try:
        import fetch_nightly_wheels as framework
        path,basic=load_basic(args.basic_information,DATA)
        with locked(path.parent/'log/.step0-entry.lock'):
            path,basic=load_basic(path,DATA)
            if not args.dry_run:
                basic=reset_environment(basic)
                write_json(path,basic)
            downloader,selection=framework.resolve(task_config(basic))
            if args.dry_run:
                print(json.dumps(selection,ensure_ascii=False,indent=2));return 0
            try:
                updated=complete_sources(path,basic,data_root=DATA,downloader=downloader,selection=selection)
            except Exception as error:
                record_failure(path,'sources',error);raise
        print(updated['preparation_status']+': '+relative_path(path, ROOT));return 0
    except Exception as error:
        print('SOURCE_FETCH_FAILED: '+str(error),file=sys.stderr);return 1


if __name__=='__main__':
    sys.exit(main())
