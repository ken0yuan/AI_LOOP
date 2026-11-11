"""Task-local refresh recovery and verified dependency installation."""
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import shutil
import tempfile

try:
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version
except ImportError:
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.utils import canonicalize_name
    from pip._vendor.packaging.version import Version


IMPORT_DISTRIBUTIONS = {
    'tensorboard': 'tensorboard',
    'tensorboard_data_server': 'tensorboard-data-server',
    'PIL': 'Pillow', 'yaml': 'PyYAML', 'dateutil': 'python-dateutil',
    'google.protobuf': 'protobuf', 'optree': 'optree', 'opt_einsum': 'opt-einsum',
}
COMPLETED_CHECKPOINT_MAX_BYTES = 1024 * 1024


@contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if stream.tell() == 0:
                stream.write(b'\0'); stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_checkpoint(path, data):
    atomic_text(path, json.dumps(data, ensure_ascii=False, sort_keys=True) + '\n')


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def target_packages(python):
    # Metadata lookup does not import torch or initialize an accelerator.
    code = """
import hashlib, importlib.metadata as m, json, sys
packages = {}
for name in ('torch', 'torch-npu'):
    try:
        dist = m.distribution(name)
    except m.PackageNotFoundError:
        continue
    packages[name] = {'version': dist.version,
                      'record': hashlib.sha256((dist.read_text('RECORD') or '').encode()).hexdigest(),
                      'location': str(dist.locate_file(''))}
print(json.dumps({'python': sys.version, 'executable': sys.executable, 'packages': packages}))
"""
    result = subprocess.run([str(python), '-I', '-c', code], capture_output=True,
                            text=True, encoding='utf-8', check=True, timeout=60)
    identity = json.loads(result.stdout)
    if 'torch' not in identity.get('packages', {}):
        raise ValueError('The task interpreter has no installed torch distribution')
    return identity


def requirements(path):
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        text = line.split(' #', 1)[0].strip()
        if text and not text.startswith(('#', '-')):
            try:
                yield Requirement(text)
            except ValueError:
                continue  # Other pip syntax stays untouched; installation validates it.


def distribution_for(module, defaults):
    if not isinstance(module, str) or not all(part.isidentifier() for part in module.split('.')):
        raise ValueError(f'Invalid missing module: {module!r}')
    root = module.split('.')[0]
    if root in {'torch', 'torch_npu'}:
        raise ValueError(f'{module} is a framework lookup failure, not an installable optional dependency')
    for prefix, distribution in IMPORT_DISTRIBUTIONS.items():
        if module == prefix or module.startswith(prefix + '.'):
            return distribution
    for requirement in requirements(defaults):
        if canonicalize_name(root) == canonicalize_name(requirement.name):
            return requirement.name
    raise ValueError(f'No verified distribution mapping for missing import {module}; '
                     'refresh checkpoint retained, no package name was guessed')


def remember_requirement(path, name, version):
    Version(version)
    with locked(Path(path).with_name('.' + Path(path).name + '.lock')):
        existing = list(requirements(path))
        for requirement in existing:
            if canonicalize_name(requirement.name) == canonicalize_name(name):
                if requirement.specifier and not requirement.specifier.contains(version, prereleases=True):
                    raise ValueError(f'Installed {name}=={version} conflicts with {path}: {requirement}')
                return
        content = Path(path).read_text(encoding='utf-8')
        atomic_text(path, content.rstrip('\n') + '\n' + name + '==' + version + '\n')


def install_dependency(python, module, work, root, task_requirements, identity, timeout):
    defaults = Path(root) / 'requirements-default.txt'
    distribution = distribution_for(module, defaults)
    requirement = next((str(r) for r in requirements(defaults)
                        if canonicalize_name(r.name) == canonicalize_name(distribution)), distribution)
    constraints = Path(work) / 'framework-constraints.txt'
    command = [str(python), '-I', '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input',
               '-c', str(constraints), '-c', str(defaults), requirement]
    with locked(Path(python).parent.parent / '.install.lock'):
        # Recovery may add packages, but must not upgrade/downgrade existing dependencies.
        frozen = subprocess.run([str(python), '-I', '-c',
            "import importlib.metadata as m,json; print(json.dumps({d.metadata['Name']:d.version "
            "for d in m.distributions() if d.metadata['Name']}))"],
            capture_output=True, text=True, encoding='utf-8', check=True, timeout=60)
        versions = {canonicalize_name(name): version for name, version in json.loads(frozen.stdout).items()}
        for name, info in identity['packages'].items():
            if versions.get(canonicalize_name(name)) != info['version']:
                raise RuntimeError('Framework version changed before dependency installation; retry refresh')
        atomic_text(constraints, ''.join(name + '==' + version + '\n' for name, version in sorted(versions.items())))
        installed = subprocess.run(command, cwd=work, capture_output=True, text=True,
                                   encoding='utf-8', errors='replace', timeout=timeout)
        with (Path(work) / 'dependency-install.log').open('a', encoding='utf-8') as log:
            log.write(f'\nMissing import: {module}; install requirement: {requirement}\n')
            log.write(installed.stdout + '\n' + installed.stderr + '\n')
        if installed.returncode:
            raise RuntimeError(f'Dependency installation failed for {module}; '
                               f'see {Path(work) / "dependency-install.log"}')
        # Check the requested distribution actually provides the missing top-level import.
        code = """
import importlib.metadata as m, importlib.util, json, sys
name, module = sys.argv[1:]
dist = m.distribution(name)
root = module.split('.')[0]
files = [str(p).replace('\\\\', '/') for p in (dist.files or [])]
if not any(p == root + '.py' or p.startswith(root + '/') for p in files):
    raise RuntimeError(name + ' does not provide import ' + root)
if importlib.util.find_spec(root) is None:
    raise RuntimeError('Installed import still unavailable: ' + root)
print(json.dumps({'name': dist.metadata['Name'], 'version': dist.version}))
"""
        checked = subprocess.run([str(python), '-I', '-c', code, distribution, module],
                                 capture_output=True, text=True, encoding='utf-8', check=True, timeout=60)
        package = json.loads(checked.stdout)
    # Update only after successful installation and import ownership verification.
    remember_requirement(defaults, package['name'], package['version'])
    remember_requirement(task_requirements, package['name'], package['version'])
    return package


class WheelDependencyError(RuntimeError):
    """Preserve the original RuntimeError contract while retaining the scanner report."""
    def __init__(self, report, exists):
        self.report = report
        self.exists = frozenset(exists)
        row = next(row for row in report['rows'] if row['status'] == 'missing_dependency')
        super().__init__(f'Wheel lookup failed for {row["name"]}: '
                         f'{row["status"]}: {row.get("message", "")}')


class RefreshRecovery:
    """Recovery around the original extractor, wheel checker and DB publication."""
    def __init__(self, basic_information, scripts, python, root, timeout, source):
        self.basic_information = Path(basic_information)
        self.scripts = Path(scripts)
        self.python = Path(python)
        self.root = Path(root)
        self.timeout = timeout
        self.work = self.basic_information.parent / 'log/api-refresh'
        self.checkpoint = self.work / 'checkpoint.json'
        crawler = self.scripts / 'extract_docs_inventory.py'
        self.document_identity = {'source': source,
                                  'crawler': file_hash(crawler) if crawler.is_file() else None}
        self.saved = {}

    def __enter__(self):
        self.work.mkdir(parents=True, exist_ok=True)
        self._lock = locked(self.work / '.refresh.lock')
        self._lock.__enter__()
        return self

    def __exit__(self, *error):
        return self._lock.__exit__(*error)

    def _save(self):
        save_checkpoint(self.checkpoint, self.saved)

    def documents(self, extract):
        previous = json.loads(self.checkpoint.read_text(encoding='utf-8')) if self.checkpoint.exists() else {}
        if not isinstance(previous, dict):
            raise ValueError('Invalid refresh checkpoint')
        resume = bool(previous) and not previous.get('completed') and previous.get('document_identity') == self.document_identity
        if resume:
            names = previous.get('documented_apis')
            if (not isinstance(names, list) or not names or
                    not all(isinstance(n, str) and n for n in names) or len(names) != len(set(names))):
                raise ValueError('Invalid saved document names; refusing an inaccurate refresh')
            self.saved = previous
            print(f'Resume refresh: reusing {len(names)} documented APIs', flush=True)
            return set(names)
        names = extract()
        if not isinstance(names, set) or not names:
            raise ValueError('Expected a non-empty extracted API name set')
        self.saved = {'document_identity': self.document_identity, 'documented_apis': sorted(names),
                      'checked': {}, 'missing': {}, 'completed': False}
        self._save()
        return names

    def _progress(self, checked, missing):
        self.saved.update(checked=dict(checked), missing=dict(missing))
        self._save()

    def _validate_report(self, report, names):
        rows = report['rows']
        if len(rows) != len(names) or {row['name'] for row in rows} != names:
            raise ValueError('Wheel report names do not match the requested names')
        provenance = report['provenance']
        if Path(provenance['python_executable']).absolute() != self.python.absolute():
            raise ValueError('Wheel report did not use the task interpreter')
        if provenance.get('inventory_sha256') != file_hash(self.work / 'check-apis.jsonl'):
            raise ValueError('Wheel report does not match the current inventory')

    def check(self, original_check, names):
        if not names:
            return {}
        identity = target_packages(self.python)
        scanner = self.scripts / 'scan_documented_apis.py'
        wheel_identity = {'installed': identity, 'scanner': file_hash(scanner)}
        if self.saved.get('wheel_identity') != wheel_identity:
            self.saved.update(wheel_identity=wheel_identity, checked={}, missing={})
            self._save()
        checked = {name: value for name, value in self.saved.get('checked', {}).items() if name in names}
        if any(type(value) is not bool for value in checked.values()):
            raise ValueError('Invalid saved wheel existence results')
        attempted = set()

        def recover(modules):
            for module in sorted(modules):
                if module in attempted or len(attempted) >= 8:
                    raise RuntimeError(f'Unresolved dependency {module}; checkpoint retained in {self.work}')
                package = install_dependency(self.python, module, self.work, self.root,
                                             self.basic_information.parent / 'requirements.txt',
                                             identity, self.timeout)
                attempted.add(module)
                print(f'Dependency ready: {package["name"]}=={package["version"]}; '
                      'default and task requirements updated', flush=True)
            self._progress(checked, {})

        remembered = {module for name, module in self.saved.get('missing', {}).items()
                      if name in names and name not in checked}
        if remembered:
            recover(remembered)
        while names - set(checked):
            pending = names - set(checked)
            # A failed checker must never leave an older report available for recovery.
            report_path = self.work / 'wheel-api-audit.json'
            report_path.unlink(missing_ok=True)
            try:
                fresh = original_check(self.python, scanner, pending, self.work, timeout=self.timeout)
            except WheelDependencyError as error:
                self._validate_report(error.report, pending)
                missing = {}
                failures = []
                for row in error.report['rows']:
                    if row['status'] in error.exists:
                        checked[row['name']] = True
                    elif row['status'] == 'unresolved' and row.get('error_type') == 'AttributeError':
                        checked[row['name']] = False
                    elif row['status'] == 'missing_dependency' and row.get('missing_module'):
                        missing[row['name']] = row['missing_module']
                    else:
                        failures.append(row)
                self._progress(checked, missing)
                if failures:
                    row = failures[0]
                    raise RuntimeError(f'Wheel lookup failed for {row["name"]}: '
                                       f'{row["status"]}: {row.get("message", "")}')
                recover(set(missing.values()))
                print(f'Wheel dependency recovery: rechecking {len(names - set(checked))} APIs', flush=True)
            else:
                self._validate_report(json.loads(report_path.read_text(encoding='utf-8')), pending)
                if set(fresh) != pending or any(type(value) is not bool for value in fresh.values()):
                    raise ValueError('Incomplete wheel existence results')
                checked.update(fresh)
                self._progress(checked, {})
        if target_packages(self.python) != identity:
            self._progress({}, {})
            raise RuntimeError('Installed framework changed during refresh; checkpoint results invalidated')
        return checked

    def complete(self, results):
        self.saved.update(completed=True, checked=dict(results), missing={})
        self._save()
        self._cleanup_completed()

    def _cleanup_completed(self):
        # Never remove the working directory/lock or follow cache links outside the task.
        try:
            expected = self.basic_information.parent.resolve() / 'log/api-refresh'
            if self.work.resolve() != expected:
                raise OSError('Cache cleanup resolved outside task directory')
        except (OSError, RuntimeError) as error:
            logging.getLogger(__name__).warning('Refresh succeeded; skip cache cleanup: %s', error)
            return
        for name in ('docs-api.json', 'docs-cache'):
            path = self.work / name
            try:
                if name == 'docs-cache' and path.is_dir() and not path.is_symlink():
                    if path.resolve().parent != expected:
                        raise OSError('Cache directory resolved outside task working directory')
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except OSError as error:
                logging.getLogger(__name__).warning('Refresh succeeded; cache cleanup failed for %s: %s', path, error)
        try:
            if self.checkpoint.is_file() and self.checkpoint.stat().st_size > COMPLETED_CHECKPOINT_MAX_BYTES:
                self.checkpoint.unlink()
        except OSError as error:
            logging.getLogger(__name__).warning('Refresh succeeded; checkpoint cleanup failed: %s', error)
