import csv
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT / 'scripts' if (PROJECT / 'scripts').is_dir() else PROJECT / 'AI_LOOP/scripts'
sys.path.insert(0, str(SCRIPTS))
import obs_downloader as obs
import obs_query_retry as obs_retry
import api_refresh_runtime as runtime
import refresh_api_support_db as refresh


class QueryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.downloader = obs.ObsutilDownloader()

    def test_successful_stat_uses_one_request_and_no_wait(self):
        response = subprocess.CompletedProcess([], 0, 'Last Modified: 2026-11-11T01:00:00Z\nETag: "abc"\nSize (KB): 8\n', '')
        with patch.object(self.downloader, '_run', return_value=response) as run, patch.object(obs.time, 'sleep') as sleep:
            self.assertEqual(self.downloader.stat('obs://bucket/object'),
                             {'last_modified': '2026-11-11T01:00:00Z', 'etag': 'abc', 'size_kb': '8'})
        run.assert_called_once_with(['stat', 'obs://bucket/object'], timeout=120)
        sleep.assert_not_called()

    def test_transient_list_retries_same_request_without_changing_inventory(self):
        output = 'obs://bucket/object\n  2026-11-11T01:00:00Z  8MB  Standard  "abc"\n'
        timeout = obs.ObsDownloadError('timeout')
        timeout.__cause__ = subprocess.TimeoutExpired('obsutil ls', 120)
        with patch.object(self.downloader, '_run', side_effect=[timeout,
                subprocess.CompletedProcess([], 0, output, '')]) as run, patch.object(obs.time, 'sleep') as sleep:
            result = self.downloader.list('obs://bucket/')
        self.assertEqual(result, {'obs://bucket/object': {'last_modified': '2026-11-11T01:00:00Z', 'etag': 'abc', 'size': '8MB'}})
        self.assertEqual(run.call_args_list[0], run.call_args_list[1])
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once()

    def test_permission_error_is_not_missing_or_retried(self):
        with patch.object(self.downloader, '_run', return_value=subprocess.CompletedProcess([], 1, '', 'StatusCode:403 AccessDenied')) as run:
            with self.assertRaisesRegex(obs.ObsDownloadError, '403'):
                self.downloader.stat('obs://bucket/object')
        self.assertEqual(run.call_count, 1)

    def test_permission_error_takes_precedence_over_a_missing_key_in_error_detail(self):
        with patch.object(self.downloader, '_run', return_value=subprocess.CompletedProcess(
                [], 1, '', 'StatusCode:403 AccessDenied; previous request: StatusCode:404')) as run:
            with self.assertRaisesRegex(obs.ObsDownloadError, '403'):
                self.downloader.stat('obs://bucket/object')
        self.assertEqual(run.call_count, 1)

    def test_missing_tool_is_not_retried(self):
        error = obs.ObsDownloadError('missing tool')
        error.__cause__ = FileNotFoundError('obsutil')
        with patch.object(self.downloader, '_run', side_effect=error) as run:
            with self.assertRaises(obs.ObsDownloadError):
                self.downloader.list('obs://bucket/')
        self.assertEqual(run.call_count, 1)

    def test_only_explicit_missing_object_returns_none(self):
        for message in ('StatusCode:404', 'ErrorCode: NoSuchKey'):
            with patch.object(self.downloader, '_run', return_value=subprocess.CompletedProcess([], 1, '', message)) as run:
                self.assertIsNone(self.downloader.stat('obs://bucket/missing'))
                self.assertEqual(run.call_count, 1)
        with patch.object(self.downloader, '_run', return_value=subprocess.CompletedProcess([], 1, '', 'unrecognized output')):
            with self.assertRaises(obs.ObsDownloadError):
                self.downloader.stat('obs://bucket/object')

    def test_transient_query_has_a_bound(self):
        with patch.object(self.downloader, '_run', return_value=subprocess.CompletedProcess([], 1, '', 'StatusCode:503')) as run, patch.object(obs.time, 'sleep'):
            with self.assertRaisesRegex(obs.ObsDownloadError, '503'):
                self.downloader.stat('obs://bucket/object')
        self.assertEqual(run.call_count, 2)

    def test_download_success_does_not_add_queries_or_change_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'bundle'
            def copy(args, timeout):
                self.assertEqual(args, ['cp', '-f', 'obs://bucket/bundle', str(dest)])
                self.assertEqual(timeout, 1800)
                dest.write_bytes(b'exact selected archive')
                return subprocess.CompletedProcess([], 0, '', '')
            with patch.object(self.downloader, '_run', side_effect=copy) as run:
                result = self.downloader.download('obs://bucket/bundle', dest)
            self.assertEqual(dest.read_bytes(), b'exact selected archive')
            self.assertEqual(result.url, 'obs://bucket/bundle')
            self.assertEqual(run.call_count, 1)


class WheelRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/scan_documented_apis.py').write_text('# fixture')
        self.run = self.root / 'run'; self.run.mkdir()
        self.identity = {'packages': {'torch': {'version': '0.0.1', 'record': 'test'}}}

    def context(self):
        return runtime.RefreshRecovery(self.run / 'basic_information.json', self.root / 'scripts',
                                       Path(sys.executable), self.root, 60, refresh.MAIN)

    def report(self, work, names, rows):
        inventory = work / 'check-apis.jsonl'
        inventory.write_text(''.join(json.dumps({'name': n}) + '\n' for n in sorted(names)))
        report = {'provenance': {'python_executable': sys.executable,
                                 'inventory_sha256': hashlib.sha256(inventory.read_bytes()).hexdigest()}, 'rows': rows}
        (work / 'wheel-api-audit.json').write_text(json.dumps(report))
        return report

    def test_retry_only_affected_names_and_never_turn_missing_dependency_into_false(self):
        batches = []
        progress = []
        installed = []
        def check(python, scanner, names, work, **kwargs):
            batches.append(set(names))
            if len(batches) == 1:
                rows = [{'name': 'torch.present', 'status': 'resolved'},
                        {'name': 'torch.gone', 'status': 'unresolved', 'error_type': 'AttributeError'},
                        {'name': 'torch.needs', 'status': 'missing_dependency', 'missing_module': 'tensorboard'}]
                raise runtime.WheelDependencyError(self.report(work, names, rows), refresh.EXISTS)
            self.report(work, names, [{'name': 'torch.needs', 'status': 'resolved'}])
            return {'torch.needs': True}
        with self.context() as recovery, patch.object(runtime, 'target_packages', return_value=self.identity):
            recovery.documents(lambda: {'torch.present', 'torch.needs'})
            def install(python, module, *args):
                installed.append(module)
                progress.append(dict(recovery.saved['checked']))
                return {'name': module, 'version': '1.0'}
            with patch.object(runtime, 'install_dependency', side_effect=install):
                actual = recovery.check(check, {'torch.present', 'torch.gone', 'torch.needs'})
        self.assertEqual(batches, [{'torch.present', 'torch.gone', 'torch.needs'}, {'torch.needs'}])
        self.assertEqual(installed, ['tensorboard'])
        self.assertEqual(progress[0], {'torch.present': True, 'torch.gone': False})
        self.assertEqual(actual, {'torch.present': True, 'torch.gone': False, 'torch.needs': True})

    def test_no_infinite_install_loop_when_dependency_remains_broken(self):
        row = [{'name': 'torch.needs', 'status': 'missing_dependency', 'missing_module': 'tensorboard'}]
        def check(python, scanner, names, work, **kwargs):
            raise runtime.WheelDependencyError(self.report(work, names, row), refresh.EXISTS)
        with self.context() as recovery, patch.object(runtime, 'target_packages', return_value=self.identity):
            recovery.documents(lambda: {'torch.needs'})
            with patch.object(runtime, 'install_dependency', return_value={'name': 'tensorboard', 'version': '1.0'}) as install:
                with patch.object(recovery, '_validate_report', wraps=recovery._validate_report) as validate:
                    with self.assertRaisesRegex(RuntimeError, 'checkpoint retained'):
                        recovery.check(check, {'torch.needs'})
                self.assertEqual(install.call_count, 1)
                self.assertEqual(validate.call_count, 2)

    def test_unknown_module_is_not_installed_by_guess(self):
        with tempfile.TemporaryDirectory() as directory:
            defaults = Path(directory) / 'requirements-default.txt'
            defaults.write_text('numpy==1.26.4\n')
            with self.assertRaisesRegex(ValueError, 'No verified distribution mapping'):
                runtime.distribution_for('unknown_namespace.child', defaults)
            with self.assertRaisesRegex(ValueError, 'framework lookup failure'):
                runtime.distribution_for('torch.internal_missing', defaults)

    def test_generic_checker_failure_cannot_recover_from_an_old_report(self):
        with self.context() as recovery, patch.object(runtime, 'target_packages', return_value=self.identity):
            recovery.documents(lambda: {'torch.needs'})
            self.report(recovery.work, {'torch.needs'}, [{'name': 'torch.needs', 'status': 'missing_dependency',
                                                        'missing_module': 'tensorboard'}])
            def check(*args, **kwargs):
                raise RuntimeError('Wheel checker failed')
            with patch.object(runtime, 'install_dependency') as install:
                with self.assertRaisesRegex(RuntimeError, 'Wheel checker failed'):
                    recovery.check(check, {'torch.needs'})
                install.assert_not_called()
            self.assertFalse((recovery.work / 'wheel-api-audit.json').exists())
            self.assertFalse(recovery.saved['completed'])

    def test_unrelated_lookup_failure_is_not_hidden_by_missing_dependency(self):
        def check(python, scanner, names, work, **kwargs):
            rows = [{'name': 'torch.needs', 'status': 'missing_dependency', 'missing_module': 'tensorboard'},
                    {'name': 'torch.bad', 'status': 'unresolved', 'error_type': 'OSError', 'message': 'broken native library'}]
            raise runtime.WheelDependencyError(self.report(work, names, rows), refresh.EXISTS)
        with self.context() as recovery, patch.object(runtime, 'target_packages', return_value=self.identity):
            recovery.documents(lambda: {'torch.needs', 'torch.bad'})
            with patch.object(runtime, 'install_dependency') as install:
                with self.assertRaisesRegex(RuntimeError, 'broken native library'):
                    recovery.check(check, {'torch.needs', 'torch.bad'})
                install.assert_not_called()

    def test_report_for_another_inventory_is_rejected(self):
        def check(python, scanner, names, work, **kwargs):
            report = self.report(work, names, [{'name': 'torch.needs', 'status': 'missing_dependency',
                                               'missing_module': 'tensorboard'}])
            report['provenance']['inventory_sha256'] = 'incorrect'
            raise runtime.WheelDependencyError(report, refresh.EXISTS)
        with self.context() as recovery, patch.object(runtime, 'target_packages', return_value=self.identity):
            recovery.documents(lambda: {'torch.needs'})
            with patch.object(runtime, 'install_dependency') as install:
                with self.assertRaisesRegex(ValueError, 'current inventory'):
                    recovery.check(check, {'torch.needs'})
                install.assert_not_called()


def make_wheel(directory, name, module, version, requires=()):
    dist = name.replace('-', '_') + '-' + version + '.dist-info'
    files = {module + '/__init__.py': b'VALUE = 1\n',
             dist + '/WHEEL': b'Wheel-Version: 1.0\nGenerator: recovery-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
             dist + '/METADATA': ('Metadata-Version: 2.1\nName: ' + name + '\nVersion: ' + version + '\n' +
                                     ''.join('Requires-Dist: ' + r + '\n' for r in requires) + '\n').encode()}
    record = io.StringIO()
    writer = csv.writer(record)
    for path, data in files.items():
        writer.writerow([path, '', len(data)])
    writer.writerow([dist + '/RECORD', '', ''])
    files[dist + '/RECORD'] = record.getvalue().encode()
    target = Path(directory) / (name.replace('-', '_') + '-' + version + '-py3-none-any.whl')
    with zipfile.ZipFile(target, 'w') as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return target


SCANNER = '''import argparse, hashlib, importlib, json, pathlib, sys
p=argparse.ArgumentParser();p.add_argument('--inventory');p.add_argument('--output');a=p.parse_args()
inventory=pathlib.Path(a.inventory); names=[json.loads(l)['name'] for l in inventory.read_text().splitlines()]
with (inventory.parent/'batches.jsonl').open('a') as f:f.write(json.dumps(names)+'\\n')
rows=[]
for name in names:
    row={'name':name,'status':'resolved'}
    if name=='torch.gone':row.update(status='unresolved',error_type='AttributeError')
    if name=='torch.needs':
        try:importlib.import_module('resume_probe')
        except ModuleNotFoundError as e:row.update(status='missing_dependency',missing_module=e.name)
    rows.append(row)
pathlib.Path(a.output).write_text(json.dumps({'provenance':{'python_executable':sys.executable,'inventory_sha256':hashlib.sha256(inventory.read_bytes()).hexdigest()},'rows':rows}))
'''


@unittest.skipIf(os.name == 'nt', 'Real venv/bin/python integration runs on the Linux deployment host')
class OfflineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'data/run_data/run'
        self.run.mkdir(parents=True)
        (self.root / 'scripts/pytorch_api_diff').mkdir(parents=True)
        (self.root / 'scripts/pytorch_api_diff/extract_docs_crawl.py').write_text('# fixture\n')
        (self.root / 'scripts/scan_documented_apis.py').write_text(SCANNER)
        self.wheels = self.root / 'wheels'; self.wheels.mkdir()
        subprocess.run([sys.executable, '-m', 'venv', str(self.root / 'venv')], check=True, capture_output=True)
        self.python = self.root / 'venv/bin/python'
        for name, module in [('torch', 'torch'), ('torch-npu', 'torch_npu')]:
            wheel = make_wheel(self.wheels, name, module, '0.0.1')
            subprocess.run([str(self.python), '-m', 'pip', 'install', '--disable-pip-version-check', '--no-index', str(wheel)],
                           check=True, capture_output=True)
        self.basic = self.run / 'basic_information.json'
        self.basic.write_text(json.dumps({'venv': 'venv'}))
        self.defaults = self.root / 'requirements-default.txt'; self.defaults.write_text('# existing defaults\n')
        (self.run / 'requirements.txt').write_text('# task inputs\n')
        self.database = self.root / 'data/api-support.sqlite3'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE "API支持数据" ("API名称" TEXT PRIMARY KEY,"是否支持" TEXT,"API适配分类" TEXT,"是否对外" TEXT,"分类待审核" TEXT)')
            db.execute('INSERT INTO "API支持数据" VALUES (?,?,?,?,?)', ('torch.gone','支持','框架类API','是','否'))
        self.original_db = self.database.read_bytes()
        self.extract_calls = 0
        self.environment = patch.dict(os.environ, {'PIP_NO_INDEX': '1', 'PIP_FIND_LINKS': str(self.wheels)})
        self.environment.start(); self.addCleanup(self.environment.stop)
        mapping = patch.dict(runtime.IMPORT_DISTRIBUTIONS, {'resume_probe': 'resume-probe'})
        mapping.start(); self.addCleanup(mapping.stop)

    def extract(self, work):
        self.extract_calls += 1
        return {'torch.present', 'torch.needs'}

    def execute(self, extractor=None):
        return refresh.refresh(self.basic, root=self.root, extractor=extractor or self.extract)

    def batches(self):
        path = self.run / 'log/api-refresh/batches.jsonl'
        return [set(json.loads(line)) for line in path.read_text().splitlines()]

    def test_dependency_recovery_resumes_missing_api_then_groups_full_public_set(self):
        make_wheel(self.wheels, 'resume-probe', 'resume_probe', '1.0', requires=('torch==0.0.1',))
        before = runtime.target_packages(self.python)
        result = self.execute()
        self.assertEqual(result['status'], 'DATABASE_REFRESHED')
        self.assertEqual(result['added'], 2)
        self.assertEqual(self.extract_calls, 1)
        self.assertEqual(self.batches(), [{'torch.present', 'torch.gone', 'torch.needs'}, {'torch.needs'},
                                          {'torch.present', 'torch.gone', 'torch.needs'}])
        self.assertIn('resume-probe==1.0', self.defaults.read_text())
        self.assertIn('resume-probe==1.0', (self.run / 'requirements.txt').read_text())
        self.assertEqual(runtime.target_packages(self.python), before)
        checkpoint = json.loads((self.run / 'log/api-refresh/checkpoint.json').read_text())
        self.assertTrue(checkpoint['completed'])

    def test_failed_pip_keeps_database_unchanged_and_next_command_resumes_without_crawl(self):
        with self.assertRaisesRegex(RuntimeError, 'Dependency installation failed'):
            self.execute()
        self.assertEqual(self.database.read_bytes(), self.original_db)
        self.assertEqual(self.defaults.read_text(), '# existing defaults\n')
        checkpoint = json.loads((self.run / 'log/api-refresh/checkpoint.json').read_text())
        self.assertFalse(checkpoint['completed'])
        self.assertEqual(checkpoint['checked'], {'torch.gone': False, 'torch.present': True})
        self.assertEqual(checkpoint['missing'], {'torch.needs': 'resume_probe'})
        make_wheel(self.wheels, 'resume-probe', 'resume_probe', '1.0')
        self.execute(extractor=lambda work: self.fail('Must not crawl again on resume'))
        self.assertEqual(self.batches(), [{'torch.present', 'torch.gone', 'torch.needs'}, {'torch.needs'},
                                          {'torch.present', 'torch.gone', 'torch.needs'}])
        self.assertIn('resume-probe==1.0', self.defaults.read_text())

    def test_completed_refresh_crawls_again_on_next_invocation(self):
        make_wheel(self.wheels, 'resume-probe', 'resume_probe', '1.0')
        self.execute()
        self.execute()
        self.assertEqual(self.extract_calls, 2)

    def test_dependency_cannot_replace_selected_framework(self):
        make_wheel(self.wheels, 'resume-probe', 'resume_probe', '1.0', requires=('torch>=9',))
        before = runtime.target_packages(self.python)
        with self.assertRaisesRegex(RuntimeError, 'Dependency installation failed'):
            self.execute()
        self.assertEqual(runtime.target_packages(self.python), before)
        self.assertEqual(self.database.read_bytes(), self.original_db)
        self.assertNotIn('resume-probe', self.defaults.read_text())

    def test_changed_framework_invalidates_cached_wheel_results_but_not_document_snapshot(self):
        with self.assertRaises(RuntimeError):
            self.execute()
        replacement = make_wheel(self.wheels, 'torch', 'torch', '0.0.2')
        subprocess.run([str(self.python), '-m', 'pip', 'install', '--disable-pip-version-check',
                        '--no-index', str(replacement)], check=True, capture_output=True)
        make_wheel(self.wheels, 'resume-probe', 'resume_probe', '1.0')
        self.execute(extractor=lambda work: self.fail('Do not recrawl when the wheel changed'))
        self.assertEqual(self.batches(), [
            {'torch.present', 'torch.gone', 'torch.needs'},
            {'torch.present', 'torch.gone', 'torch.needs'}, {'torch.needs'},
            {'torch.present', 'torch.gone', 'torch.needs'}])

    def test_existing_custom_checker_callback_is_not_replaced(self):
        calls = []
        def original_callback(python, scanner, names, work):
            calls.append((python, scanner, names, work))
            return {name: name != 'torch.gone' for name in names}
        with patch.object(runtime, 'target_packages', side_effect=AssertionError('Unused callback must not run')):
            result = refresh.refresh(self.basic, root=self.root, extractor=self.extract, checker=original_callback)
        self.assertEqual(result['status'], 'DATABASE_REFRESHED')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], {'torch.present', 'torch.needs', 'torch.gone'})

    def test_dependency_cannot_upgrade_an_existing_optional_package(self):
        original = make_wheel(self.wheels, 'existing-dep', 'existing_dep', '1.0')
        subprocess.run([str(self.python), '-m', 'pip', 'install', '--disable-pip-version-check',
                        '--no-index', str(original)], check=True, capture_output=True)
        make_wheel(self.wheels, 'existing-dep', 'existing_dep', '2.0')
        make_wheel(self.wheels, 'resume-probe', 'resume_probe', '1.0', requires=('existing-dep>=2',))
        with self.assertRaisesRegex(RuntimeError, 'Dependency installation failed'):
            self.execute()
        version = subprocess.check_output([str(self.python), '-I', '-c',
            "import importlib.metadata as m; print(m.version('existing-dep'))"], text=True).strip()
        self.assertEqual(version, '1.0')
        self.assertEqual(self.database.read_bytes(), self.original_db)
        self.assertNotIn('resume-probe', self.defaults.read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
