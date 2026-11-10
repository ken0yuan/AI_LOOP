"""The basic_information.json contract shared by Step 0 producers."""
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

ENVIRONMENT = ('python_version', 'chip_type', 'os', 'architecture')
SOURCE_FIELDS = ('pytorch_source', 'torch_npu_latest_source', 'pytorch_source_commit', 'torch_npu_main_commit')
WHEEL_FIELDS = ('pytorch_version', 'pytorch_wheel', 'torch_npu_version', 'torch_npu_wheel')


# Includes obsolete version-result fields so older task files are cleaned on rewrite.
ENVIRONMENT_RESULT_FIELDS = ('python', 'venv', 'activate', 'installed_pytorch_version',
    'installed_torch_npu_version', 'environment_reused', 'environment_completed_at', 'environment_error')


def reset_environment(basic, status='NOT_READY'):
    updated = dict(basic)
    for key in ENVIRONMENT_RESULT_FIELDS:
        updated.pop(key, None)
    updated['environment_status'] = status
    return updated


def relative_path(path, root):
    """Serialize a filesystem path relative to the AI_LOOP project root."""
    return Path(os.path.relpath(Path(path).absolute(), Path(root).absolute())).as_posix()


def relative_metadata(value, root):
    """Keep project paths portable while leaving URLs and identities intact."""
    path_keys = {'path', 'archive', 'preparation_record', 'defaults_path',
                 'requirements_defaults_path', 'pytorch_source', 'torch_npu_latest_source',
                 'pytorch_wheel', 'torch_npu_wheel', 'torch_npu_source', 'basic_information', 'bin',
                 'npu_smi_log', 'cann_version_file'}
    if isinstance(value, dict):
        return {key: relative_path(item, root) if key in path_keys and isinstance(item, str)
                and Path(item).is_absolute() else relative_metadata(item, root)
                for key, item in value.items()}
    if isinstance(value, list):
        return [relative_metadata(item, root) for item in value]
    return value


def write_json(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.'+path.name, delete=False) as out:
        tmp = Path(out.name)
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write('\n')
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def target_profile(config):
    if not re.fullmatch(r'3\.\d+\.\d+', config['python_version']):
        raise ValueError('python_version must be a full CPython 3.x.y version')
    matches = [p for p in config['wheel_profiles'] if all(p.get(k) == config.get(k) for k in ENVIRONMENT[1:])]
    if len(matches) != 1:
        raise ValueError('No unique wheel profile for chip_type + os + architecture')
    return matches[0]


def config_snapshot(config):
    # Keep transport settings, never authentication material.
    if any(k in config for k in ('infrastructure_root', 'infrastructure_settings')):
        raise ValueError('Legacy infrastructure_* settings are unsupported; configure AI_LOOP obs directly')
    obs = config['obs']
    transport = obs.get('downloader') or {}
    return {'pytorch_repo': config['pytorch_repo'], 'torch_npu_repo': config['torch_npu_repo'],
            'torch_npu_fork_repo': config.get('torch_npu_fork_repo'),
            'obs_prefix': config['obs_prefix'], 'wheel_profiles': copy.deepcopy(config['wheel_profiles']),
            'obs': {'bucket': obs['bucket'], 'downloader': {k: transport[k] for k in
                    ('type', 'bin', 'regional_proxy_endpoint') if k in transport}}}


def load_basic(path, data_root):
    path = Path(path).resolve()
    parent = (Path(data_root) / 'run_data').resolve()
    if path.name != 'basic_information.json' or path.parent == parent or not path.parent.is_relative_to(parent):
        raise ValueError('basic_information.json must belong to a task under data/run_data')
    basic = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(basic, dict):
        raise ValueError('basic_information.json must be a JSON object')
    for key in ENVIRONMENT:
        if not basic.get(key):
            raise ValueError('Missing target field in basic_information.json: ' + key)
    if not isinstance(basic.get('preparation_config'), dict):
        raise ValueError('basic_information.json has no preparation_config; initialize it with prepare_api_inputs.py')
    return path, basic


def task_config(basic):
    config = copy.deepcopy(basic['preparation_config'])
    config.update({k: basic[k] for k in ENVIRONMENT})
    target_profile(config)
    return config


def state(basic):
    sources = all(basic.get(k) for k in SOURCE_FIELDS)
    wheels = all(basic.get(k) for k in WHEEL_FIELDS)
    return 'INPUTS_READY' if sources and wheels else 'SOURCES_READY' if sources else 'WHEELS_READY' if wheels else 'BASIC_READY'


def publish(path, basic, updates, record_updates, *, root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    updated = dict(basic, **updates)
    updated.pop('api_names', None)
    updated = reset_environment(updated)
    updated = relative_metadata(updated, root)
    updated['preparation_status'] = state(updated)
    record_path = path.parent / 'log/step0-preparation.json'
    record = json.loads(record_path.read_text()) if record_path.exists() else {}
    record.update(record_updates)
    record = relative_metadata(record, root)
    record['status'] = updated['preparation_status']
    record.pop('error', None)
    record.pop('failed_stage', None)
    write_json(record_path, record)
    write_json(path, updated)
    return updated


def record_failure(path, stage, error):
    record_path = path.parent / 'log/step0-preparation.json'
    record = json.loads(record_path.read_text()) if record_path.exists() else {}
    record.update(status='FAILED', failed_stage=stage, error=str(error))
    write_json(record_path, record)
    basic=json.loads(path.read_text())
    basic.pop('api_names', None)
    basic = reset_environment(basic)
    basic['preparation_status']='FAILED'
    write_json(path,basic)
