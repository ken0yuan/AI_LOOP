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
 "分类待审核" TEXT NOT NULL DEFAULT '否' CHECK ("分类待审核" IN ('是','否')),
 "API真正名字" TEXT
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
        self.assertEqual(self.checked, [{'torch.stay', 'torch.new_api', 'torch.not_yet_built',
                                        'torch.docgone', 'torch.gone',
                                        'torch.already_offline', 'torch.special'}])
        self.assertEqual(after['torch.docgone']['是否支持'], '文档删除，待下线')
        self.assertEqual(after['torch.gone']['是否支持'], 'API删除，待下线')
        for name in ['torch.stay', 'torch.internal', 'torch.already_offline']:
            self.assertEqual(after[name], before[name])
        self.assertEqual(after['torch.special']['是否支持'], 'API删除，待下线')
        for name in ['torch.docgone', 'torch.gone']:
            self.assertEqual({k:v for k,v in after[name].items() if k != '是否支持'},
                             {k:v for k,v in before[name].items() if k != '是否支持'})
        self.assertEqual(after['torch.not_yet_built']['是否支持'], 'API删除，待下线')
        self.assertEqual(after['torch.new_api']['是否对外'], '是')
        self.assertEqual(after['torch.new_api']['分类待审核'], '是')
        self.assertIsNone(after['torch.new_api']['API适配分类'])
        self.assertEqual(json.loads(self.output.read_text()), ['torch.new_api'])
        self.assert_clean()

    def test_document_removed_but_present_in_wheel_stays_in_handoff(self):
        self.insert('torch.docgone')
        with sqlite3.connect(self.database) as conn:
            conn.execute('UPDATE "API支持数据" SET "细分场景"=NULL WHERE "API名称"=?', ('torch.docgone',))
        self.output.parent.mkdir(parents=True)
        self.output.write_text(json.dumps(['torch.docgone']))
        self.run_refresh({'torch.stay'})
        self.assertEqual(self.rows()['torch.docgone']['是否支持'], '文档删除，待下线')
        self.assertIn('torch.docgone', json.loads(self.output.read_text()))

    def test_missing_from_wheel_is_removed_from_handoff(self):
        self.insert('torch.gone')
        self.output.parent.mkdir(parents=True)
        self.output.write_text(json.dumps(['torch.gone']))
        self.run_refresh({'torch.stay'})
        self.assertEqual(self.rows()['torch.gone']['是否支持'], 'API删除，待下线')
        self.assertNotIn('torch.gone', json.loads(self.output.read_text()))

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
        self.assertEqual(self.checked, [{'torch.Tensor.add', 'torch.Tensor.old'}])
        self.assertEqual(set(rows), {'Tensor.add', 'Tensor.old'})
        self.assertEqual(rows['Tensor.add']['是否支持'], '支持')
        self.assertEqual(rows['Tensor.old']['是否支持'], '文档删除，待下线')
        self.assertEqual(json.loads(self.output.read_text()), [])

    def test_existing_internal_name_is_promoted_without_duplicate_insert(self):
        self.insert('torch.new_api', public='否')
        before = self.rows()
        self.run_refresh({'torch.new_api'})
        self.assertEqual(self.checked, [{'torch.new_api'}])
        expected = before['torch.new_api'].copy()
        expected['是否对外'] = '是'
        self.assertEqual(self.rows(), {'torch.new_api': expected})
        self.assertEqual(json.loads(self.output.read_text()), [])

    def test_incomplete_promoted_row_is_handed_off_without_replacing_existing_fields(self):
        self.insert('torch.new_api', public='否')
        with sqlite3.connect(self.database) as conn:
            conn.execute('UPDATE "API支持数据" SET "细分场景"=NULL WHERE "API名称"=?', ('torch.new_api',))
        before = self.rows()['torch.new_api']
        self.run_refresh({'torch.new_api'})
        expected = dict(before, 是否对外='是')
        self.assertEqual(self.rows()['torch.new_api'], expected)
        self.assertEqual(json.loads(self.output.read_text()), ['torch.new_api'])

    def test_completed_previous_handoff_is_removed_on_rerun(self):
        self.insert('torch.stay')
        self.output.parent.mkdir(parents=True)
        self.output.write_text(json.dumps(['torch.stay']))
        self.run_refresh({'torch.stay'})
        self.assertEqual(json.loads(self.output.read_text()), [])

    def test_promoted_shorter_representative_inherits_only_missing_business_fields(self):
        self.insert('torch.new_api')
        self.insert('torch.a', public='否')
        with sqlite3.connect(self.database) as conn:
            conn.execute('UPDATE "API支持数据" SET "细分场景"=NULL, "适配策略"=? WHERE "API名称"=?',
                         ('keep-internal-strategy', 'torch.a'))
        with (self.torch / '__init__.py').open('a') as stream:
            stream.write('\na = new_api\n')
        self.run_refresh({'torch.new_api', 'torch.a'})
        row = self.rows()['torch.a']
        self.assertEqual(row['细分场景'], 'fixture-scenario')
        self.assertEqual(row['适配策略'], 'keep-internal-strategy')
        self.assertEqual(self.rows()['torch.new_api']['API真正名字'], 'torch.a')
        self.assertEqual(json.loads(self.output.read_text()), [])

    def test_schema_migration_keeps_rows_indexes_and_triggers_and_allows_null_type(self):
        self.insert('torch.stay')
        before = self.rows()['torch.stay']
        with sqlite3.connect(self.database) as conn:
            conn.execute('CREATE INDEX fixture_strategy ON "API支持数据" ("适配策略")')
            conn.execute('CREATE TABLE fixture_audit (name TEXT)')
            conn.execute('CREATE TRIGGER fixture_added AFTER INSERT ON "API支持数据" '
                         'BEGIN INSERT INTO fixture_audit VALUES (new."API名称"); END')
        self.run_refresh({'torch.stay', 'torch.new_api'})
        self.assertEqual(self.rows()['torch.stay'], before)
        self.assertIsNone(self.rows()['torch.new_api']['API适配分类'])
        with sqlite3.connect(self.database) as conn:
            metadata = {r[1]: r for r in conn.execute('PRAGMA table_info("API支持数据")')}
            self.assertEqual(metadata['API适配分类'][3:5], (0, None))
            self.assertIsNotNone(conn.execute("SELECT name FROM sqlite_master WHERE name='fixture_strategy'").fetchone())
            self.assertEqual(conn.execute('SELECT name FROM fixture_audit').fetchall(), [('torch.new_api',)])
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_documented_existing_name_missing_from_wheel_is_offlined(self):
        self.insert('torch.missing_from_wheel')
        self.run_refresh({'torch.stay', 'torch.missing_from_wheel'})
        self.assertEqual(self.rows()['torch.missing_from_wheel']['是否支持'], 'API删除，待下线')
        self.assertIn('torch.missing_from_wheel', self.checked[0])

    def test_internal_object_alias_is_not_scanned_or_changed(self):
        with (self.torch / '__init__.py').open('a') as stream:
            stream.write('\na = new_api\n')
        self.insert('torch.a', public='否')
        before = self.rows()['torch.a']
        self.run_refresh({'torch.new_api'})
        self.assertEqual(self.checked, [{'torch.new_api'}])
        self.assertEqual(self.rows()['torch.a'], before)
        self.assertIsNone(self.rows()['torch.new_api']['API真正名字'])

    def test_internal_tensor_shorthand_is_not_promoted_by_fuzzy_matching(self):
        self.insert('Tensor.add', public='否')
        before = self.rows()['Tensor.add']
        self.run_refresh({'torch.Tensor.add'})
        self.assertEqual(self.rows()['Tensor.add'], before)
        self.assertEqual(self.rows()['torch.Tensor.add']['是否对外'], '是')

    def test_shortest_public_name_redirects_existing_long_name_and_inherits_business(self):
        self.insert('torch.new_api')
        self.insert('torch.old_alias')
        self.insert('torch.historical_alias')
        self.insert('torch.internal_alias', public='否')
        with sqlite3.connect(self.database) as conn:
            for name in ['torch.old_alias', 'torch.historical_alias', 'torch.internal_alias']:
                conn.execute('UPDATE "API支持数据" SET "API真正名字"=? WHERE "API名称"=?',
                             ('torch.new_api', name))
        internal_before = self.rows()['torch.internal_alias']
        before = self.rows()['torch.new_api']
        with (self.torch / '__init__.py').open('a') as stream:
            stream.write('\na = new_api\nold_alias = new_api\nhistorical_alias = new_api\n')
        self.run_refresh({'torch.new_api', 'torch.a', 'torch.old_alias'})
        rows = self.rows()
        self.assertEqual(rows['torch.new_api']['API真正名字'], 'torch.a')
        self.assertEqual(rows['torch.old_alias']['API真正名字'], 'torch.a')
        self.assertEqual(rows['torch.historical_alias']['API真正名字'], 'torch.a')
        self.assertEqual(rows['torch.internal_alias'], internal_before)
        self.assertIsNone(rows['torch.a']['API真正名字'])
        for column in ['是否支持', '分类', '细分模块', '细分场景', '适配策略', '本地是否适配']:
            self.assertEqual(rows['torch.a'][column], before[column])
        self.assertEqual(json.loads(self.output.read_text()), [])

    def test_same_length_uses_name_order_and_alias_rows_are_retained(self):
        with (self.torch / '__init__.py').open('a') as stream:
            stream.write('\naa = new_api\nbb = new_api\n')
        self.run_refresh({'torch.aa', 'torch.bb'})
        rows = self.rows()
        self.assertEqual(set(rows), {'torch.aa', 'torch.bb'})
        self.assertIsNone(rows['torch.aa']['API真正名字'])
        self.assertEqual(rows['torch.bb']['API真正名字'], 'torch.aa')
        self.assertEqual(json.loads(self.output.read_text()), ['torch.aa'])

    def test_new_api_compares_with_existing_public_object_not_in_current_docs(self):
        self.insert('torch.stay')
        with (self.torch / '__init__.py').open('a') as stream:
            stream.write('\na = stay\n')
        self.run_refresh({'torch.a'})
        self.assertEqual(self.checked, [{'torch.a', 'torch.stay'}])
        self.assertEqual(self.rows()['torch.stay']['API真正名字'], 'torch.a')
        self.assertIsNone(self.rows()['torch.a']['API真正名字'])

    def test_gpu_cache_proxy_is_checked_without_initializing_a_device(self):
        backend = self.torch / 'backends'
        backend.mkdir()
        (backend / '__init__.py').write_text('')
        (backend / 'cuda.py').write_text('''
class cuFFTPlanCache:
    def clear(self): raise AssertionError('Must not call API')
    @property
    def size(self): raise AssertionError('Must not read device state')
    @property
    def max_size(self): raise AssertionError('Must not read device state')
class Manager:
    def __getattr__(self, name): raise AssertionError('Must not initialize CUDA')
cufft_plan_cache = Manager()
''')
        docs = {'torch.backends.cuda.cufft_plan_cache.' + n for n in ['clear', 'size', 'max_size']}
        self.run_refresh(docs)
        self.assertEqual(set(self.rows()), docs)
        self.assertTrue(all(row['是否支持'] != 'API删除，待下线' for row in self.rows().values()))

    def test_cached_backfill_report_cannot_modify_internal_aliases(self):
        import backfill_api_aliases as backfill
        self.insert('torch.stay')
        self.insert('torch.new_api')
        self.insert('torch.internal', public='否')
        with sqlite3.connect(self.database) as conn:
            conn.execute('UPDATE "API支持数据" SET "API真正名字"=? WHERE "API名称"=?',
                         ('old-internal-reference', 'torch.internal'))
        before = self.rows()['torch.internal']
        directory = self.task / 'backfill'; directory.mkdir()
        with patch.object(backfill, 'DB', self.database):
            columns, rows = backfill.read_database()
            original = [c for c in columns if c != 'API真正名字']
            values = [[r[columns.index(c)] for c in original] for r in rows]
            report = {'original_columns': original,
                      'original_digest': backfill.input_digest(original, values),
                      'aliases': {'torch.new_api': 'torch.stay', 'torch.internal': 'torch.stay'},
                      'summary': {'rows': 3, 'alias_rows': 2}}
            (directory / 'identity-audit.json').write_text(json.dumps(report))
            backfill.apply(directory)
        self.assertEqual(self.rows()['torch.internal'], before)
        self.assertEqual(self.rows()['torch.new_api']['API真正名字'], 'torch.stay')
        self.assertEqual(json.loads((directory / 'verification.json').read_text())['alias_rows'], 1)

    def test_distinct_objects_remain_separate_despite_identical_behavior(self):
        self.run_refresh({'torch.stay', 'torch.new_api'})
        self.assertTrue(all(row['API真正名字'] is None for row in self.rows().values()))
        self.assertEqual(json.loads(self.output.read_text()), ['torch.new_api', 'torch.stay'])

    def test_alias_identity_is_rechecked_when_wheel_exposure_changes(self):
        with (self.torch / '__init__.py').open('a') as stream:
            stream.write('\nlong_alias = stay\n')
        self.run_refresh({'torch.stay', 'torch.long_alias'})
        self.assertEqual(self.rows()['torch.long_alias']['API真正名字'], 'torch.stay')
        with (self.torch / '__init__.py').open('a') as stream:
            stream.write('\ndef long_alias(): raise AssertionError("not called")\n')
        self.run_refresh({'torch.stay', 'torch.long_alias'})
        self.assertIsNone(self.rows()['torch.long_alias']['API真正名字'])

    def test_alias_becoming_independent_is_handed_off_for_missing_fields(self):
        self.insert('torch.stay')
        with (self.torch / '__init__.py').open('a') as stream:
            stream.write('\nlong_alias = stay\n')
        self.run_refresh({'torch.stay', 'torch.long_alias'})
        self.assertEqual(self.rows()['torch.long_alias']['API真正名字'], 'torch.stay')
        self.assertEqual(json.loads(self.output.read_text()), [])
        before = self.rows()['torch.stay']
        with (self.torch / '__init__.py').open('a') as stream:
            stream.write('\ndef long_alias(): raise AssertionError("not called")\n')
        self.run_refresh({'torch.stay', 'torch.long_alias'})
        self.assertIsNone(self.rows()['torch.long_alias']['API真正名字'])
        self.assertIsNone(self.rows()['torch.long_alias']['API适配分类'])
        self.assertEqual(json.loads(self.output.read_text()), ['torch.long_alias'])
        self.assertEqual(self.rows()['torch.stay'], before)

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
        self.assertEqual(self.checked, [{'torch.Tensor'}])
        self.assertEqual(json.loads(self.output.read_text()), [])

    def test_missing_documented_tensor_class_is_checked_by_full_wheel_name(self):
        self.insert('Tensor')
        self.insert('torch.stay')
        self.run_refresh({'torch.stay'})
        self.assertEqual(self.checked, [{'torch.Tensor', 'torch.stay'}])
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

    def test_repeated_task_preserves_handoff_and_rechecks_all_documented_names(self):
        self.run_refresh({'torch.new_api'})
        self.run_refresh({'torch.new_api'})
        self.assertEqual(self.checked, [{'torch.new_api'}, {'torch.new_api'}])
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
