#!/usr/bin/env python3
"""Initialize the API/environment portion of a task's basic_information.json."""
import argparse
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from preparation_state import locked, write_json, sha256, target_profile, config_snapshot, ENVIRONMENT, relative_path

try:
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
except ImportError:
    from pip._vendor.packaging.markers import default_environment
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'


def merge_requirements(defaults, required, config):
    """Produce one task requirements file; explicit active constraints override defaults."""
    if not required:
        return defaults
    requested = [Requirement(value) for value in required]
    environment = default_environment()
    environment.update(python_version='.'.join(config['python_version'].split('.')[:2]),
                       python_full_version=config['python_version'], sys_platform='linux',
                       os_name='posix', platform_system='Linux', platform_machine=config['architecture'])
    overrides = {canonicalize_name(item.name) for item in requested
                 if item.marker is None or item.marker.evaluate(environment)}
    lines = []
    for raw in defaults.decode('utf-8').splitlines():
        line = raw.split(' #', 1)[0].strip()
        if line and not line.startswith(('#', '-')):
            item = Requirement(line)
            if canonicalize_name(item.name) in overrides:
                continue
        lines.append(raw)
    lines.extend(['', '# Required dependencies for this task:'])
    lines.extend(dict.fromkeys(str(item) for item in requested))
    return ('\n'.join(lines)+'\n').encode('utf-8')


def collect_runtime_environment():
    """Read the real driver and active CANN installation before publishing inputs."""
    result = subprocess.run(['npu-smi', 'info'], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', check=True, timeout=30)
    match = re.search(r'\bnpu-smi[^\n]*\bVersion:\s*([^\s|]+)', result.stdout)
    if not match:
        raise ValueError('npu-smi info did not report the driver Version')
    roots = [Path(value) for value in (os.environ.get('ASCEND_HOME_PATH'),
             os.environ.get('ASCEND_TOOLKIT_HOME'), '/usr/local/Ascend/cann',
             '/usr/local/Ascend/ascend-toolkit/latest') if value]
    architecture = {'aarch64':'arm64-linux', 'x86_64':'x86_64-linux'}.get(platform.machine())
    for root in dict.fromkeys(roots):
        candidates = [root/'version.cfg', root/'ascend_toolkit_install.info']
        if architecture:
            candidates.append(root/architecture/'ascend_toolkit_install.info')
        for path in candidates:
            if not path.is_file():
                continue
            version = re.search(r'^version\s*=\s*([^\s]+)', path.read_text(encoding='utf-8'), re.M | re.I)
            if version:
                return {'driver_version':match[1], 'cann_version':version[1],
                        'npu_smi_output':result.stdout, 'cann_version_file':path}
    raise ValueError('Cannot read the active CANN toolkit version; no task inputs were published')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', required=True)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--defaults', type=Path, default=ROOT/'api-defaults.json')
    parser.add_argument('--requirement', action='append', default=[],
                        help='Required task dependency, e.g. pytest==8.3.0; may be repeated')
    for field in ('python-version','chip-type','os','architecture'):
        parser.add_argument('--'+field)
    parser.add_argument('--dry-run', action='store_true', help='Show initial JSON without creating files or accessing the network')
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+', args.api, re.ASCII):
            raise ValueError('--api must be one dotted API name')
        run_dir = args.run_dir.resolve()
        parent = (DATA/'run_data').resolve()
        if run_dir == parent or not run_dir.is_relative_to(parent):
            raise ValueError('run-dir must be a task directory under AI_LOOP/data/run_data')
        match = re.fullmatch(re.escape('adapt-'+args.api+'-')+r'(\d{6})', run_dir.name)
        if not match:
            raise ValueError('API run-id must be adapt-<API>-<YYMMDD>')
        datetime.strptime(match[1], '%y%m%d')
        config = json.loads(args.defaults.read_text(encoding='utf-8'))
        for key in ENVIRONMENT:
            if getattr(args,key) is not None:
                config[key] = getattr(args,key)
        target_profile(config)
        snapshot = config_snapshot(config)
        transport = snapshot['obs']['downloader']
        if transport.get('bin') and Path(transport['bin']).is_absolute():
            transport['bin'] = relative_path(transport['bin'], ROOT)
        basic_path = run_dir/'basic_information.json'
        requirements = run_dir/'requirements.txt'
        if basic_path.exists() or requirements.exists():
            raise ValueError('Run already has business inputs; choose a new run-dir')
        requirements_source = ROOT/'requirements-default.txt'
        requirements_content = merge_requirements(requirements_source.read_bytes(), args.requirement, config)
        runtime = collect_runtime_environment()
        record_path = run_dir/'log/step0-preparation.json'
        basic = {'run_id':run_dir.name,
                 'problem-description':f'API `{args.api}` needs adaptation',
                 'cann_version':runtime['cann_version'], 'driver_version':runtime['driver_version'],
                 **{k:config[k] for k in ENVIRONMENT},
                 'preparation_status':'BASIC_READY', 'environment_status':'NOT_READY', 'preparation_config':snapshot,
                 'preparation_record':relative_path(record_path, ROOT)}
        if args.dry_run:
            print(json.dumps(basic,ensure_ascii=False,indent=2))
            return 0
        with locked(run_dir/'log/.step0-entry.lock'):
            if basic_path.exists() or requirements.exists():
                raise ValueError('Concurrent preparation already published inputs')
            requirements.write_bytes(requirements_content)
            npu_log = run_dir/'log/npu-smi-info.log'
            npu_log.write_text(runtime['npu_smi_output'], encoding='utf-8')
            write_json(record_path, {'status':'BASIC_READY', 'api':args.api,
                       'npu_smi_log':relative_path(npu_log, ROOT),
                       'cann_version_file':relative_path(runtime['cann_version_file'], ROOT),
                       'defaults_path':relative_path(args.defaults, ROOT), 'defaults_sha256':sha256(args.defaults),
                       'requirements_defaults_path':relative_path(requirements_source, ROOT),
                       'required_dependencies':args.requirement,
                       'requirements_sha256':hashlib.sha256(requirements_content).hexdigest(),
                       'initialized_at':datetime.now(timezone.utc).isoformat()})
            write_json(basic_path,basic)
        print('BASIC_READY: '+relative_path(basic_path, ROOT))
        return 0
    except Exception as error:
        print('PREPARATION_FAILED: '+str(error),file=sys.stderr)
        return 1


if __name__=='__main__':
    sys.exit(main())
