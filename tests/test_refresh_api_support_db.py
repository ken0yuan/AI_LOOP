"""Offline end-to-end refresh tests using the inventory extractor and wheel checker."""
import base64
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import venv
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import refresh_api_support_db as refresh


SCHEMA = '''CREATE TABLE "API支持数据" (
 "API名称" TEXT NOT NULL PRIMARY KEY, "是否支持" TEXT, "API等级" TEXT,
 "API适配分类" TEXT NOT NULL DEFAULT '框架类API'
 CHECK ("API适配分类" IN ('框架类API','算子类API','专项适配API')),
 "是否对外" TEXT, "分类" TEXT, "细分模块" TEXT, "细分场景" TEXT,
 "适配策略" TEXT, "本地是否适配" TEXT,
 "分类待审核" TEXT NOT NULL DEFAULT '否' CHECK ("分类待审核" IN ('是','否'))
)'''

TORCH = '''from types import SimpleNamespace
__version__ = '2.16.0a0'
version = SimpleNamespace(git_version='fixture', cuda=None)
def stay(): raise AssertionError('API bodies must not be called')
def new_api(): raise AssertionError('API bodies must not be called')
def docgone(): raise AssertionError('API bodies must not be called')
class Tensor:
    def add(self): raise AssertionError('API bodies must not be called')
    def old(self): raise AssertionError('API bodies must not be called')
'''


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='test-api-refresh-')
        self.root = Path(self.temp.name)
        self.scripts = self.root / 'scripts'
        self.scripts.mkdir()
        source = Path(refresh.__file__).resolve().parent
        for name in ['refresh_api_support_db.py', 'api_refresh_runtime.py', 'api_first_seen.py',
                     'scan_documented_apis.py', 'extract_docs_inventory.py']:
            shutil.copyfile(source / name, self.scripts / name)
        self.venv = self.root / 'data/venv/fixture'
        venv.EnvBuilder(with_pip=False).create(self.venv)
        result = subprocess.run([str(self.venv / 'bin/python'), '-I', '-B', '-c',
                                 'import site;print(site.getsitepackages()[0])'],
                                capture_output=True, text=True, check=True)
        self.torch = Path(result.stdout.strip()) / 'torch'
        self.torch.mkdir()
        distribution = self.torch.parent / 'torch-2.16.0a0.dist-info'
        distribution.mkdir()
        (distribution / 'METADATA').write_text(
            'Metadata-Version: 2.1\nName: torch\nVersion: 2.16.0a0\n')
        (distribution / 'RECORD').write_text('')
        (self.torch / '__init__.py').write_text(TORCH)
        self.task = self.root / 'data/run_data/adapt-torch.new_api-261109'
        self.task.mkdir(parents=True)
        self.basic = self.task / 'basic_information.json'
        self.basic.write_text(json.dumps({'venv': 'data/venv/fixture', 'api_names': ['torch.new_api']}))
        self.database = self.root / 'data/api-support.sqlite3'
        with sqlite3.connect(self.database) as conn:
            conn.execute(SCHEMA)
        self.output = self.task / 'api-refresh/new-apis.json'
        self.checked = []
        self.working_directories = []

    def tearDown(self):
        self.temp.cleanup()

    def insert(self, name, status='支持', public='是'):
        with sqlite3.connect(self.database) as conn:
            conn.execute('INSERT INTO "API支持数据" '
                         '("API名称","是否支持","API等级","是否对外","分类","细分模块",'
                         '"细分场景","适配策略","本地是否适配","分类待审核") VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (name, status, 'L1', public, 'Core', 'fixture-module', 'fixture-scenario',
                          'existing-strategy', '否', '否'))

    def rows(self):
        conn = sqlite3.connect(self.database)
        try:
            conn.row_factory = sqlite3.Row
            return {row['API名称']: dict(row) for row in conn.execute('SELECT * FROM "API支持数据"')}
        finally:
            conn.close()

    def checker(self, python, scanner, names, work):
        self.checked.append(set(names))
        self.working_directories.append(work)
        return refresh.check_in_wheel(python, scanner, names, work, timeout=30)

    def run_refresh(self, docs, **kwargs):
        def extract(work):
            self.working_directories.append(work)
            return set(docs)
        return refresh.refresh(self.basic, self.database, root=self.root,
                               extractor=extract, checker=kwargs.pop('checker', self.checker), **kwargs)

    def assert_clean(self, *, completed=True, checkpoint=True):
        work_paths = set(self.working_directories) | {self.task / 'log/api-refresh'}
        for work in work_paths:
            self.assertTrue(work.is_dir())
            self.assertTrue((work / '.refresh.lock').is_file())
            saved = work / 'checkpoint.json'
            if checkpoint:
                self.assertTrue(saved.is_file())
                state = json.loads(saved.read_text())
                self.assertEqual(state['completed'], completed)
                self.assertTrue(state['documented_apis'])
            else:
                self.assertFalse(saved.exists())
            if completed:
                self.assertFalse((work / 'docs-api.json').exists())
                self.assertFalse((work / 'docs-cache').exists())
        self.assertEqual(list(self.root.rglob('*.bak*')), [])
        self.assertEqual(list(self.root.rglob('*initial.sqlite3')), [])
        if self.output.parent.exists():
            self.assertEqual({p.name for p in self.output.parent.iterdir()},
                             {'new-apis.json'} if self.output.exists() else set())

    def test_full_database_update_and_exact_minimal_handoff(self):
        self.insert('torch.stay')
        self.insert('torch.docgone', '不支持')
        self.insert('torch.gone', '未提供')
        self.insert('torch.internal', '支持', '否')
        self.insert('torch.already_offline', 'API删除，待下线')
        self.insert('torch.special', '专项处理中')
        before = self.rows()
        result = self.run_refresh({'torch.stay', 'torch.new_api', 'torch.not_yet_built'})
        after = self.rows()
        self.assertEqual(result['status'], 'DATABASE_REFRESHED')
        self.assertEqual(self.checked, [{'torch.new_api', 'torch.not_yet_built', 'torch.docgone', 'torch.gone'}])
        self.assertEqual(after['torch.docgone']['是否支持'], '文档删除，待下线')
        self.assertEqual(after['torch.gone']['是否支持'], 'API删除，待下线')
        for name in ['torch.stay', 'torch.internal', 'torch.already_offline', 'torch.special']:
            self.assertEqual(after[name], before[name])
        for name in ['torch.docgone', 'torch.gone']:
            self.assertEqual({k:v for k,v in after[name].items() if k != '是否支持'},
                             {k:v for k,v in before[name].items() if k != '是否支持'})
        self.assertNotIn('torch.not_yet_built', after)
        self.assertEqual(after['torch.new_api']['是否对外'], '是')
        self.assertEqual(after['torch.new_api']['分类待审核'], '是')
        self.assertEqual(after['torch.new_api']['API适配分类'], '框架类API')
        self.assertEqual(json.loads(self.output.read_text()), ['torch.new_api'])
        self.assert_clean()

    def test_adaptation_category_default_and_three_value_constraint(self):
        self.insert('torch.stay')
        self.assertEqual(self.rows()['torch.stay']['API适配分类'], '框架类API')
        with sqlite3.connect(self.database) as conn:
            for value in ['框架类API', '算子类API', '专项适配API']:
                conn.execute('UPDATE "API支持数据" SET "API适配分类"=?', (value,))
            for value in ['其他', '', None]:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute('UPDATE "API支持数据" SET "API适配分类"=?', (value,))

    def test_all_three_normal_states_are_eligible(self):
        for i, status in enumerate(['支持', '不支持', '未提供']):
            self.insert('torch.missing' + str(i), status)
        self.run_refresh({'torch.stay'})
        self.assertEqual({row['是否支持'] for row in self.rows().values() if row['API名称'].startswith('torch.missing')},
                         {'API删除，待下线'})
        self.assert_clean()

    def test_tensor_baseline_alias_is_matched_without_renaming_or_duplicates(self):
        self.insert('Tensor.add')
        self.insert('Tensor.old')
        self.run_refresh({'torch.Tensor.add'})
        rows = self.rows()
        self.assertEqual(self.checked, [{'torch.Tensor.old'}])
        self.assertEqual(set(rows), {'Tensor.add', 'Tensor.old'})
        self.assertEqual(rows['Tensor.add']['是否支持'], '支持')
        self.assertEqual(rows['Tensor.old']['是否支持'], '文档删除，待下线')
        self.assertEqual(json.loads(self.output.read_text()), [])

    def test_existing_internal_name_is_not_promoted_or_inserted_again(self):
        self.insert('torch.new_api', public='否')
        before = self.rows()
        self.run_refresh({'torch.new_api'})
        self.assertEqual(self.checked, [set()])
        self.assertEqual(self.rows(), before)
        self.assertEqual(json.loads(self.output.read_text()), [])

    def test_tensor_class_and_member_queries_match_both_spellings(self):
        for full, short in [('torch.Tensor', 'Tensor'), ('torch.Tensor.add', 'Tensor.add')]:
            self.assertEqual(refresh.match_api_name(full, {short}), short)
            self.assertEqual(refresh.match_api_name(short, {full}), full)
            self.assertEqual(refresh.match_api_name(full, {full, short}), full)
            self.assertEqual(refresh.match_api_name(short, {full, short}), short)
        self.assertIsNone(refresh.match_api_name('torch.nn.Module', {'Module'}))
        self.assertEqual(refresh.canonical('Tensor'), 'torch.Tensor')

    def test_bare_tensor_class_in_database_is_not_inserted_again(self):
        self.insert('Tensor')
        before = self.rows()
        self.run_refresh({'torch.Tensor'})
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.checked, [set()])
        self.assertEqual(json.loads(self.output.read_text()), [])

    def test_missing_documented_tensor_class_is_checked_by_full_wheel_name(self):
        self.insert('Tensor')
        self.insert('torch.stay')
        self.run_refresh({'torch.stay'})
        self.assertEqual(self.checked, [{'torch.Tensor'}])
        self.assertEqual(self.rows()['Tensor']['是否支持'], '文档删除，待下线')
        self.assertNotIn('torch.Tensor', self.rows())

    def test_missing_new_name_is_retried_when_it_appears_in_later_wheel(self):
        self.run_refresh({'torch.later'})
        self.assertEqual(json.loads(self.output.read_text()), [])
        with (self.torch / '__init__.py').open('a') as stream:
            stream.write('\ndef later(): pass\n')
        self.run_refresh({'torch.later'})
        self.assertEqual(json.loads(self.output.read_text()), ['torch.later'])
        self.assertIn('torch.later', self.rows())

    def test_repeated_task_preserves_handoff_and_does_not_recheck_common_names(self):
        self.run_refresh({'torch.new_api'})
        self.run_refresh({'torch.new_api'})
        self.assertEqual(self.checked, [{'torch.new_api'}, set()])
        self.assertEqual(json.loads(self.output.read_text()), ['torch.new_api'])
        self.assertEqual(len(self.rows()), 1)
        self.assert_clean()

    def test_dependency_failure_never_becomes_deletion(self):
        self.insert('torch.docgone')
        (self.torch / 'optional.py').write_text('import missing_refresh_fixture_dependency\n')
        before = self.database.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'lookup failed'):
            self.run_refresh({'torch.optional.NewClass'})
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse(self.output.exists())
        self.assert_clean(completed=False)

    def test_venv_is_required_instead_of_guessing_from_python_version(self):
        self.basic.write_text(json.dumps({'python_version': '3.11.17'}))
        with self.assertRaisesRegex(ValueError, 'venv path'):
            self.run_refresh({'torch.new_api'})
        self.assertFalse(self.output.exists())

    def test_concurrent_database_edits_are_preserved(self):
        self.insert('torch.stay')
        def check(*args):
            result = self.checker(*args)
            with sqlite3.connect(self.database) as conn:
                conn.execute('UPDATE "API支持数据" SET "适配策略"=? WHERE "API名称"=?',
                             ('concurrent-user-change', 'torch.stay'))
            return result
        with self.assertRaisesRegex(RuntimeError, 'changed during refresh'):
            self.run_refresh({'torch.stay', 'torch.new_api'}, checker=check)
        self.assertEqual(self.rows()['torch.stay']['适配策略'], 'concurrent-user-change')
        self.assertNotIn('torch.new_api', self.rows())
        self.assertFalse(self.output.exists())
        self.assert_clean(completed=False)

    def test_output_failure_rolls_back_database(self):
        self.insert('torch.docgone')
        before = self.rows()
        replace = refresh.os.replace
        def fail_handoff_only(source, destination):
            if Path(destination).resolve() == self.output.resolve():
                raise OSError('fixture output unavailable')
            return replace(source, destination)
        with patch.object(refresh.os, 'replace', side_effect=fail_handoff_only):
            with self.assertRaisesRegex(OSError, 'output unavailable'):
                self.run_refresh({'torch.new_api'})
        self.assertEqual(self.rows(), before)
        self.assertFalse(self.output.exists())
        self.assert_clean(completed=False)

    def test_commit_failure_rolls_back_and_restores_existing_handoff(self):
        self.insert('torch.stay')
        self.output.parent.mkdir()
        self.output.write_text('["torch.stay"]\n')
        old_output = self.output.read_bytes()
        before = self.rows()
        connect = sqlite3.connect
        class Connection:
            def __init__(self, connection): self.connection = connection
            def __getattr__(self, name): return getattr(self.connection, name)
            def commit(self): raise sqlite3.OperationalError('fixture commit failure')
        def failing_connect(*args, **kwargs):
            return Connection(connect(*args, **kwargs))
        with patch.object(refresh.sqlite3, 'connect', side_effect=failing_connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'commit failure'):
                self.run_refresh({'torch.stay', 'torch.new_api'})
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.output.read_bytes(), old_output)
        self.assert_clean(completed=False)

    def cli(self, pages):
        fixtures = self.root / 'pages.json'
        fixtures.write_text(json.dumps(pages))
        temporary_root = self.root / 'temporary'
        temporary_root.mkdir(exist_ok=True)
        # Exercise the inventory extractor over deterministic HTTP responses, then
        # the real scanner subprocess in the Step 0 venv and real SQLite writes.
        bootstrap = '''import base64,io,json,sys,urllib.request,urllib.error
from pathlib import Path
root=Path(sys.argv[1]);pages=json.loads((root/'pages.json').read_text())
def urlopen(request,timeout=None):
 url=request.full_url if hasattr(request,'full_url') else str(request)
 value=pages.get(url)
 if value is None:raise urllib.error.HTTPError(url,404,'fixture missing',{},None)
 if isinstance(value,int):raise urllib.error.HTTPError(url,value,'fixture HTTP failure',{},None)
 data=base64.b64decode(value['base64']) if isinstance(value,dict) else value.encode()
 return io.BytesIO(data)
urllib.request.urlopen=urlopen
sys.path.insert(0,str(root/'scripts'))
import refresh_api_support_db
sys.argv=['refresh_api_support_db.py','--basic-information','data/run_data/adapt-torch.new_api-261109/basic_information.json']
raise SystemExit(refresh_api_support_db.main())
'''
        result = subprocess.run([sys.executable, '-B', '-c', bootstrap, str(self.root)],
                                cwd=self.root, env=dict(os.environ, TMPDIR=str(temporary_root)),
                                capture_output=True, text=True, timeout=40)
        self.assertEqual(list(temporary_root.iterdir()), [])
        return result

    def pages(self):
        root = refresh.MAIN
        names = ['torch.stay', 'torch.new_api']
        header = ('# Sphinx inventory version 2\n# Project: PyTorch\n'
                  '# Version: main (2.16.0a0+gitfixture)\n# The remainder is compressed using zlib.\n')
        entries = '\n'.join(f'{name} py:function 1 generated/{name}.html#$ -'
                            for name in names) + '\n'
        inventory = header.encode() + zlib.compress(entries.encode())
        return {root + '/objects.inv': {'base64': base64.b64encode(inventory).decode('ascii')}}

    def test_single_entry_cli_reuses_real_extractor_checker_and_updates_sqlite(self):
        self.insert('torch.stay')
        self.insert('torch.docgone')
        self.insert('torch.gone', '不支持')
        result = self.cli(self.pages())
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary['documented'], 2)
        self.assertEqual(summary['added'], 1)
        self.assertEqual(summary['api_deleted'], 1)
        self.assertEqual(summary['documentation_deleted'], 1)
        self.assertEqual(json.loads(self.output.read_text()), ['torch.new_api'])
        self.assertEqual(self.rows()['torch.docgone']['是否支持'], '文档删除，待下线')
        self.assert_clean()

    def test_extractor_failure_leaves_database_unchanged(self):
        self.insert('torch.docgone')
        pages = self.pages()
        pages[refresh.MAIN + '/objects.inv'] = 503
        before = self.database.read_bytes()
        result = self.cli(pages)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('REFRESH_FAILED', result.stderr)
        self.assertIn('503', result.stderr)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse(self.output.exists())
        self.assert_clean(completed=False, checkpoint=False)

    def test_document_names_and_count_are_preserved_without_normalization(self):
        data = {'torch.__init__': ['kept'], 'torch.Tensor': ['add'],
                '_signatures': {}, '_sources': {}}
        names = refresh.names_from_document(data)
        self.assertEqual(names, {'torch.__init__.kept', 'torch.Tensor.add'})
        self.assertEqual(len(names), 2)
        self.assertNotIn('torch.kept', names)


if __name__ == '__main__':
    unittest.main(verbosity=2)
