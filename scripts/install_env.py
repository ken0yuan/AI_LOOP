#!/usr/bin/env python3
"""Prepare/reuse one venv per exact Python version from an upstream run directory."""
from __future__ import annotations
import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
from email.parser import Parser
import fcntl
import glob
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from preparation_state import relative_path, locked, reset_environment, write_json
from pip._internal.network.session import PipSession
from pip._internal.req.req_file import parse_requirements
from pip._vendor.packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def set_environment_state(path, status, **fields):
    basic = read_json(path)
    if not isinstance(basic, dict):
        raise ValueError('basic_information.json must be a JSON object')
    basic.pop('api_names', None)
    basic = reset_environment(basic, status)
    basic.update(fields)
    write_json(path, basic)


def run(argv, *, env=None, cwd=None, log=None, timeout=900):
    result = subprocess.run([str(x) for x in argv], env=env, cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if log:
        with log.open('a', encoding='utf-8') as f:
            f.write('$ ' + shlex.join([str(x) for x in argv]) + '\n' + result.stdout + '\n')
    if result.returncode:
        raise RuntimeError(f'Command failed ({result.returncode}): {result.stdout[-3000:]}')
    return result.stdout.strip()


def find_python(version, explicit=None, existing=None):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('python_version must be the full x.y.z version')
    minor = '.'.join(version.split('.')[:2])
    candidates = ([str(existing)] if existing and existing.is_file() else [])
    if explicit:
        candidates.append(explicit)
    else:
        candidates += [sys.executable, shutil.which('python' + minor)]
        candidates += glob.glob('/home/workspace-ycy/tools/python/cpython-' + version + '-*/bin/python' + minor)
        candidates += glob.glob('/usr/local/python' + version + '/bin/python*')
    for candidate in dict.fromkeys(c for c in candidates if c):
        try:
            actual = run([candidate, '-I', '-B', '-c', 'import platform;print(platform.python_version())'], timeout=15)
            if actual == version:
                return str(Path(candidate).absolute())
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            continue
    raise ValueError(f'No installed Python {version}; provide --python /path/to/python. No venv was deleted.')


def cache_path(value, folder):
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path if path.parts and path.parts[0] == 'data' else DATA / folder / path
    if not path.exists():
        raise ValueError(f'Missing upstream cached artifact: {path}')
    return path.absolute()


def wheel_metadata(path, name, expected):
    with zipfile.ZipFile(path) as wheel:
        items = [x for x in wheel.namelist() if x.endswith('.dist-info/METADATA')]
        if len(items) != 1:
            raise ValueError(f'Invalid wheel metadata: {path}')
        meta = Parser().parsestr(wheel.read(items[0]).decode('utf-8'))
    if canonicalize_name(meta['Name']) != canonicalize_name(name):
        raise ValueError(f'Wheel is not {name}: {path}')
    normalize = lambda v: v.lower().removesuffix('+gitunknown')
    if normalize(meta['Version']) != normalize(expected):
        raise ValueError(f'{name} version mismatch: task={expected}, wheel={meta["Version"]}')
    return meta['Version']


def effective_requirements(upstream, wheels):
    # Validate pip syntax while preserving the task file, options, markers and includes.
    with PipSession() as session:
        for _ in parse_requirements(str(upstream), session=session):
            pass
    lines = ['# Task requirements supplied by Step 0.', '-r '+str(upstream), '',
             '# Exact framework wheels supplied by this task:']
    lines += [name+' @ '+path.resolve().as_uri() for name, path in wheels.items()]
    return '\n'.join(lines)+'\n'


def prepare_requirements(run_dir, wheels):
    path = run_dir/'requirements.txt'
    if not path.is_file():
        raise ValueError(f'Task requirements.txt missing: {path}')
    return effective_requirements(path.resolve(), wheels)


def task_inputs(run_dir):
    """The installer owns its prerequisite checks, before touching an environment."""
    basic = run_dir / 'basic_information.json'
    if not basic.is_file():
        raise ValueError('Environment installation requires basic_information.json in the task directory')
    task = read_json(basic)
    if not isinstance(task, dict):
        raise ValueError('basic_information.json must be a JSON object')
    if task.get('preparation_status') not in (None, 'INPUTS_READY'):
        raise ValueError('basic_information.json is incomplete; finish source and wheel preparation first')
    if not (run_dir / 'requirements.txt').is_file():
        raise ValueError('Environment installation requires an independent task requirements.txt')
    # Existing CI/PR inputs without Step 0 status are checked by their actual fields/files.
    if task.get('preparation_status') is not None:
        record = run_dir / 'log/step0-preparation.json'
        if not record.is_file():
            raise ValueError('Environment installation requires the Step 0 preparation record')
        preparation = read_json(record)
        if not isinstance(preparation, dict) or preparation.get('status') != 'INPUTS_READY':
            raise ValueError('Step 0 preparation record is incomplete; expected INPUTS_READY')
    required = ('python_version', 'pytorch_source', 'pytorch_version', 'pytorch_wheel',
                'torch_npu_version', 'torch_npu_wheel')
    missing = [key for key in required if not task.get(key)]
    if missing:
        raise ValueError('Environment prerequisite fields are missing: ' + ', '.join(missing))
    if task.get('preparation_status') is not None:
        source_record = run_dir/'log/step0-source.json'
        if not source_record.is_file():
            raise ValueError('TorchNPU source checkout record is missing; fetch source and wheel inputs first')
        source = read_json(source_record)
        if not isinstance(source, dict) or source.get('status') != 'SOURCE_READY':
            raise ValueError('TorchNPU source checkout is incomplete; expected SOURCE_READY')
    return task


def _install_task(args, run_dir):
    basic_path = run_dir/'basic_information.json'
    try:
        task = task_inputs(run_dir)
        version = task['python_version']
        if not re.fullmatch(r'\d+\.\d+\.\d+', version):
            raise ValueError('basic_information.python_version must be a full x.y.z version')
        source = cache_path(task['pytorch_source'], 'source_code')
        wheels = {'torch': cache_path(task['pytorch_wheel'], 'wheels'),
                  'torch-npu': cache_path(task['torch_npu_wheel'], 'wheels')}
        wheel_metadata(wheels['torch'], 'torch', task['pytorch_version'])
        wheel_metadata(wheels['torch-npu'], 'torch-npu', task['torch_npu_version'])
        venv = DATA / 'venv' / ('python-' + version)
        exe = venv / 'bin/python'
        interpreter = find_python(version, args.python, exe)
        merged = prepare_requirements(run_dir, wheels)
        if args.dry_run:
            print(json.dumps({'python_version': version, 'interpreter': relative_path(interpreter, ROOT),
                              'venv': relative_path(venv, ROOT), 'reuse': exe.is_file(), 'source_code': relative_path(source, ROOT)}, indent=2))
            print(merged)
            return 0
        set_environment_state(basic_path, 'INSTALLING')
        log_dir = run_dir / 'log'
        log_dir.mkdir(exist_ok=True)
        log = log_dir / 'install-env.log'
        effective = log_dir / 'requirements-effective.txt'
        effective.write_text(merged, encoding='utf-8')
        temp = log_dir / '.install-tmp'
        temp.mkdir(exist_ok=True)
        process_env = os.environ.copy()
        process_env.pop('PYTHONPATH', None)
        process_env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
                           TORCH_DEVICE_BACKEND_AUTOLOAD='0', PIP_DISABLE_PIP_VERSION_CHECK='1',
                           PIP_NO_CACHE_DIR='1', TMPDIR=str(temp))
        cann = Path('/usr/local/Ascend/cann/set_env.sh')
        if cann.is_file():
            raw = subprocess.run(['bash', '-c', 'source "$1" >/dev/null && env -0',
                                  'install-env', str(cann)], env=process_env, capture_output=True, check=True)
            process_env.update(x.split('=', 1) for x in raw.stdout.decode().split('\0') if '=' in x)
            process_env.pop('PYTHONPATH', None)
        venv.mkdir(parents=True, exist_ok=True)
        with (venv / '.install.lock').open('a', encoding='utf-8') as lock:
            print('Waiting for Python ' + version + ' environment lock...', flush=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
            reused = exe.is_file()
            if reused:
                actual = run([exe, '-I', '-B', '-c', 'import platform;print(platform.python_version())'])
                if actual != version:
                    raise ValueError(f'Existing venv uses {actual}, expected {version}; preserving it unchanged')
                print('Reuse venv: ' + relative_path(venv, ROOT), flush=True)
            else:
                print('Create venv: ' + relative_path(venv, ROOT), flush=True)
                run([interpreter, '-m', 'venv', venv], env=process_env, log=log)
            print('Install task requirements and selected framework wheels...', flush=True)
            run([exe, '-m', 'pip', 'install', '--no-cache-dir', '-r', effective],
                cwd=run_dir, env=process_env, log=log)
            # CI builds can share a version: always load the exact wheels supplied by this task.
            run([exe, '-m', 'pip', 'install', '--no-cache-dir', '--no-index', '--no-deps',
                 '--force-reinstall', *wheels.values()], cwd=run_dir, env=process_env, log=log)
            probe = '''import json,platform,torch,torch_npu
print(json.dumps(dict(python_version=platform.python_version(),pytorch_version=torch.__version__,
 torch_npu_version=torch_npu.__version__,pytorch_file=torch.__file__,torch_npu_file=torch_npu.__file__)))'''
            observed = json.loads(run([exe, '-B', '-c', probe], cwd=run_dir, env=process_env, log=log).splitlines()[-1])
            if observed['python_version'] != version:
                raise ValueError('Imported Python version does not match the task')
            normalize = lambda v: v.lower().removesuffix('+gitunknown')
            for key in ('pytorch_version', 'torch_npu_version'):
                if normalize(observed[key]) != normalize(task[key]):
                    raise ValueError(f'Imported {key} does not match task metadata')
            for key in ('pytorch_file', 'torch_npu_file'):
                if not Path(observed[key]).resolve().is_relative_to(venv.resolve()):
                    raise ValueError('Framework import escaped the selected venv')
            env_lines = ['#!/usr/bin/env bash']
            if cann.is_file():
                env_lines.append('source ' + shlex.quote(str(cann)))
            if process_env.get('LD_LIBRARY_PATH'):
                env_lines.append('export LD_LIBRARY_PATH=' + shlex.quote(process_env['LD_LIBRARY_PATH']))
            env_lines += ['unset PYTHONPATH', 'export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 TORCH_DEVICE_BACKEND_AUTOLOAD=0',
                          'source ' + shlex.quote(str(venv / 'bin/activate'))]
            (venv / 'env.sh').write_text('\n'.join(env_lines) + '\n', encoding='utf-8')
            set_environment_state(basic_path, 'ENV_READY',
                python=relative_path(exe, ROOT), venv=relative_path(venv, ROOT),
                activate=relative_path(venv/'env.sh', ROOT), environment_reused=reused,
                environment_completed_at=datetime.now(timezone.utc).isoformat())
        if temp.is_dir() and not any(temp.iterdir()):
            temp.rmdir()
        print('ENV_READY: ' + relative_path(basic_path, ROOT), flush=True)
        return 0
    except Exception as error:
        if not args.dry_run and basic_path.is_file():
            try:
                set_environment_state(basic_path, 'FAILED', environment_error=str(error))
            except Exception as record_error:
                print('Cannot record environment failure: '+str(record_error), file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--python', help='Existing interpreter matching the full upstream Python version')
    parser.add_argument('--dry-run', action='store_true', help='Validate and print the plan without installing')
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    try:
        if not run_dir.is_dir() or not run_dir.is_relative_to((DATA/'run_data').resolve()):
            raise ValueError('run-dir must be an existing task directory under data/run_data')
        context = nullcontext() if args.dry_run else locked(run_dir/'log/.step0-entry.lock')
        with context:
            return _install_task(args, run_dir)
    except Exception as error:
        print('ENV_INSTALL_FAILED: '+str(error), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
