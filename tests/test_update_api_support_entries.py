"""Isolated checks for eleven-column inputs and short shared classification reasons."""
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import update_api_support_entries as update
import refresh_api_support_db as refresh


class CompleteEntriesTests(unittest.TestCase):
    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.database)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='test-api-completion-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = self.root / 'data/run_data/adapt-fixture-261109'
        (self.task / 'api-refresh').mkdir(parents=True)
        self.basic = self.task / 'basic_information.json'
        self.basic.write_text(json.dumps({'venv': 'data/venv/fixture'}), encoding='utf-8')
        python = self.root / 'data/venv/fixture/bin/python'
        python.parent.mkdir(parents=True)
        python.touch()
        self.names = self.task / 'api-refresh/new-apis.json'
        self.names.write_text('[]', encoding='utf-8')
        self.entries = self.task / 'api-entries.jsonl'
        self.evidence = self.root / 'data/api-classification/classification-evidence.jsonl'
        self.database = self.root / 'data/api-support.sqlite3'
        definitions = ','.join('"' + c + '" TEXT' + (' PRIMARY KEY' if c == 'API名称' else '') for c in update.COLUMNS)
        with self.connection() as conn:
            conn.execute('CREATE TABLE "API支持数据" (' + definitions + ')')

    def rows(self):
        with self.connection() as conn:
            conn.row_factory = sqlite3.Row
            return {r['API名称']: dict(r) for r in conn.execute('SELECT * FROM "API支持数据"')}

    def fields(self, name='torch.new_api', existing=None):
        before = dict.fromkeys(update.COLUMNS)
        before.update({'API名称': name, 'API适配分类': '框架类API', '是否对外': '是', '分类待审核': '是'})
        before.update(existing or {})
        with self.connection() as conn:
            conn.execute('INSERT INTO "API支持数据" VALUES (' + ','.join('?' for _ in update.COLUMNS) + ')',
                         [before[c] for c in update.COLUMNS])
        final = dict(before)
        for c, value in update.DEFAULTS.items():
            if not update.text(final[c]):
                final[c] = value
        final.update({'分类': 'Core', '细分模块': 'fixture-module', '细分场景': 'fixture-scenario'})
        return final

    def reason(self, name='torch.new_api'):
        return {'api': name, 'category_reason': 'Manages framework runtime state.',
                'module_reason': 'Owns fixture device lifecycle.', 'scenario_reason': 'Queries fixture device status.'}

    def write(self, fields, names=None, reasons=True):
        self.names.write_text(json.dumps(names if names is not None else [f['API名称'] for f in fields]), encoding='utf-8')
        self.entries.write_text(''.join(json.dumps(f, ensure_ascii=False) + '\n' for f in fields), encoding='utf-8')
        if reasons:
            update.save_reasons([self.reason(f['API名称']) for f in fields], root=self.root)

    def run_complete(self):
        return update.complete(self.basic, root=self.root)

    def test_only_eleven_fields_and_three_reasons_then_clean_retry(self):
        fields = self.fields()
        self.write([fields])
        self.assertEqual(set(json.loads(self.entries.read_text(encoding='utf-8'))), set(update.COLUMNS))
        for attempt in range(2):
            result = self.run_complete()
            self.assertEqual((result['completed'], result['reused']), (1, attempt))
            self.assertNotIn('eligible_apis', result)
            self.assertFalse(self.entries.exists())
            self.assertEqual(self.rows()['torch.new_api'], fields)
        self.assertEqual(fields['API等级'], 'L2')
        self.assertEqual(fields['API适配分类'], '框架类API')
        self.assertEqual(fields['是否支持'], '未提供')
        self.assertEqual(fields['适配策略'], '补充testcase')
        self.assertEqual(fields['本地是否适配'], '是')
        self.assertEqual(fields['分类待审核'], '是')
        self.assertEqual(set(update.shared_reasons(self.evidence)['torch.new_api']), {'api', *update.REASONS})
        self.assertFalse((self.task / 'api-classification').exists())

    def test_consumes_real_refresh_registration(self):
        fields = self.fields()
        with self.connection() as conn:
            conn.execute('DELETE FROM "API支持数据"')
        refresh.publish(self.database, refresh.database_snapshot(self.database), {'torch.new_api'}, {}, self.names)
        self.write([fields])
        self.run_complete()
        self.assertEqual(self.rows()['torch.new_api'], fields)

    def test_no_local_flag_filter_and_known_values_preserved(self):
        fields = self.fields(existing={'API等级':'L0', '是否支持':'支持', '本地是否适配':'否',
                                       '适配策略':'既有策略', 'API适配分类':'算子类API'})
        self.write([fields])
        result = self.run_complete()
        self.assertEqual(result['completed'], 1)
        self.assertNotIn('skipped_apis', result)
        self.assertEqual(self.rows()['torch.new_api'], fields)

    def test_extra_nested_fields_and_partial_batch_roll_back(self):
        first = self.fields('torch.first')
        second = self.fields('torch.second')
        original = self.rows()
        for change in [lambda f:f.update({'细分场景':''}), lambda f:f.update({'API等级':'L1'}),
                       lambda f:f.update({'API适配分类':'算子类API'}), lambda f:f.update({'before':{}}),
                       lambda f:f.update({'reason':'must not be in the eleven-column file'}),
                       lambda f:f.update({'分类待审核':'否'})]:
            bad = copy.deepcopy(second)
            change(bad)
            self.write([first, bad])
            with self.assertRaises(ValueError):
                self.run_complete()
            self.assertEqual(self.rows(), original)
            self.assertTrue(self.entries.exists())

    def test_exact_names_duplicates_and_missing_entries(self):
        fields = self.fields('Tensor.add')
        for values, names in [([fields], ['torch.Tensor.add']), ([fields, fields], ['Tensor.add']),
                              ([], ['Tensor.add']), ([fields], ['Tensor.add','Tensor.add'])]:
            # Duplicate input is deliberately written without the upsert helper.
            self.write(values, names, reasons=False)
            with self.assertRaises(ValueError):
                self.run_complete()

    def test_pending_scenario_requires_candidate_and_gap_reason_without_changing_reference(self):
        fields = self.fields()
        self.write([fields])
        reason = self.reason()
        reason['new_label_reasons'] = {'scenario': 'Existing scenarios cover execution, while this API manages a new lifetime boundary.'}
        update.save_reasons([reason], root=self.root)
        with self.assertRaisesRegex(ValueError, 'not registered'):
            self.run_complete()
        proposal = {'level':'scenario', 'parent':['Core','fixture-module'], 'name':'fixture-scenario',
                    'definition':'Manages a distinct fixture lifetime boundary.', 'apply_when':'Fixture lifetime APIs.',
                    'do_not_apply_when':'General execution APIs.', 'example_apis':['torch.new_api'],
                    'why_existing_does_not_fit':'Existing scenarios cover execution only.', 'review_status':'待审核',
                    'reference_file':'references/api-classification/core.md'}
        update.save_definitions([proposal], root=self.root)
        with self.assertRaisesRegex(ValueError, 'target reference'):
            self.run_complete()
        ref = self.root/proposal['reference_file']
        ref.parent.mkdir(parents=True)
        ref.write_text('Existing reviewed scenario definitions only.', encoding='utf-8')
        original = ref.read_bytes()
        self.run_complete()
        self.assertEqual(self.rows()['torch.new_api']['分类待审核'], '是')
        self.assertEqual(ref.read_bytes(), original)
        self.assertNotIn(proposal['name'], ref.read_text(encoding='utf-8'))

    def test_approved_candidate_must_have_reference_updated_after_approval(self):
        fields = self.fields()
        self.write([fields])
        proposal = {'level':'scenario', 'parent':['Core','fixture-module'], 'name':'fixture-scenario',
                    'definition':'Approved distinct scenario responsibility.', 'apply_when':'Fixture lifetime APIs.',
                    'do_not_apply_when':'General execution APIs.', 'example_apis':['torch.new_api'],
                    'why_existing_does_not_fit':'Existing scenarios cover execution only.', 'review_status':'已审核',
                    'reference_file':'references/api-classification/core.md'}
        update.save_definitions([proposal], root=self.root)
        ref = self.root/proposal['reference_file']
        ref.parent.mkdir(parents=True)
        ref.write_text('Existing reviewed scenario definitions only.', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'approved label not synchronized'):
            self.run_complete()
        ref.write_text(proposal['name'], encoding='utf-8')
        self.run_complete()
        self.assertEqual(self.rows()['torch.new_api'], fields)

    def test_missing_review_flag_defaults_to_yes_and_human_reviewed_value_is_preserved(self):
        first = self.fields('torch.first', existing={'分类待审核': None})
        second = self.fields('torch.second', existing={'分类待审核': '否'})
        self.write([first, second])
        self.run_complete()
        self.assertEqual(self.rows()['torch.first']['分类待审核'], '是')
        self.assertEqual(self.rows()['torch.second']['分类待审核'], '否')

    def test_cross_run_and_mixed_batch_reuse_without_reclassifying(self):
        first = self.fields('torch.first')
        self.write([first])
        self.run_complete()
        original_reason = self.evidence.read_bytes()
        later = self.root/'data/run_data/adapt-later-261110'
        (later/'api-refresh').mkdir(parents=True)
        basic = later/'basic_information.json'
        basic.write_bytes(self.basic.read_bytes())
        (later/'api-refresh/new-apis.json').write_text('["torch.first"]', encoding='utf-8')
        result = update.complete(basic, root=self.root)
        self.assertEqual((result['updated'],result['reused']), (0,1))
        self.assertEqual(self.evidence.read_bytes(), original_reason)
        second = self.fields('torch.second')
        self.write([second], names=['torch.first','torch.second'])
        result = self.run_complete()
        self.assertEqual((result['updated'],result['reused']), (1,1))

    def test_reason_upsert_keeps_one_record_and_other_apis(self):
        self.fields()
        first = self.reason('torch.first')
        second = self.reason('torch.second')
        update.save_reasons([first,second], root=self.root)
        first['scenario_reason'] = 'A revised short scenario reason.'
        update.save_reasons([first], root=self.root)
        reasons = update.shared_reasons(self.evidence)
        self.assertEqual(reasons, {'torch.first': first, 'torch.second': second})
        self.assertEqual(len(self.evidence.read_text(encoding='utf-8').splitlines()),2)
        self.assertEqual(list(self.evidence.parent.glob('*.tmp')),[])

    def test_definitions_and_api_reasons_share_file_without_duplicate_records(self):
        self.fields()
        proposal = {'level':'scenario', 'parent':['Core','fixture-module'], 'name':'new-scenario',
                    'definition':'A distinct responsibility.', 'apply_when':'Fixture lifetime APIs.',
                    'do_not_apply_when':'General execution APIs.', 'example_apis':['torch.new_api'],
                    'why_existing_does_not_fit':'Existing scenarios cover execution only.', 'review_status':'待审核',
                    'reference_file':'references/api-classification/core.md'}
        for _ in range(2):
            update.save_definitions([proposal], root=self.root)
            update.save_reasons([self.reason()], root=self.root)
        reasons, definitions = update.classification_evidence(self.evidence)
        self.assertEqual(len(reasons), 1)
        self.assertEqual(len(definitions), 1)
        self.assertEqual(next(iter(definitions.values())), dict(proposal, kind='definition'))
        self.assertEqual(len(self.evidence.read_text(encoding='utf-8').splitlines()), 2)
        self.assertFalse((self.evidence.parent/'taxonomy-proposals.json').exists())

    def test_new_category_targets_separate_rules_file_without_modifying_it(self):
        fields = self.fields()
        fields['分类'] = 'fixture-new-category'
        self.write([fields])
        reason = self.reason()
        reason['new_label_reasons'] = {'category': 'None of the five category definitions covers this distinct fixture responsibility.'}
        update.save_reasons([reason], root=self.root)
        proposal = {'level':'category', 'parent':[], 'name':'fixture-new-category',
                    'definition':'A distinct fixture responsibility.', 'apply_when':'Fixture-specific APIs.',
                    'do_not_apply_when':'Existing five responsibilities.', 'example_apis':['torch.new_api'],
                    'why_existing_does_not_fit':'None of the five category definitions fits.', 'review_status':'待审核',
                    'reference_file':'references/api-classification/rules.md'}
        update.save_definitions([proposal], root=self.root)
        rules = self.root/proposal['reference_file']
        rules.parent.mkdir(parents=True)
        rules.write_text('Existing category definitions.', encoding='utf-8')
        original = rules.read_bytes()
        self.run_complete()
        self.assertEqual(rules.read_bytes(), original)
        self.assertEqual(self.rows()['torch.new_api']['分类'], 'fixture-new-category')

    def test_missing_duplicate_and_long_reasons_fail(self):
        fields = self.fields()
        self.write([fields], reasons=False)
        with self.assertRaisesRegex(ValueError,'three-level reasons'):
            self.run_complete()
        reason = self.reason()
        reason['scenario_reason'] = 'x'*101
        with self.assertRaisesRegex(ValueError,'100 characters'):
            update.save_reasons([reason], root=self.root)
        self.assertFalse(self.evidence.exists())
        reason = self.reason()
        update.save_reasons([reason], root=self.root)
        once = self.evidence.read_bytes()
        self.evidence.write_bytes(once+once)
        with self.assertRaisesRegex(ValueError,'Duplicate shared'):
            self.run_complete()

    def test_empty_new_list_cannot_enter_completion_or_report_ready(self):
        before = self.database.read_bytes()
        with self.assertRaisesRegex(ValueError, 'stop after refresh'):
            self.run_complete()
        self.assertFalse(self.evidence.exists())
        self.assertFalse(self.entries.exists())
        self.assertEqual(self.database.read_bytes(), before)

    def test_commit_failure_keeps_temp_input_and_rolls_back(self):
        fields = self.fields()
        self.write([fields])
        before = self.rows()
        before_reason = self.evidence.read_bytes()
        connect = sqlite3.connect
        class Connection:
            def __init__(self,c):object.__setattr__(self,'conn',c)
            def __getattr__(self,n):return getattr(self.conn,n)
            def __setattr__(self,n,v):setattr(self.conn,n,v)
            def commit(self):raise sqlite3.OperationalError('fixture commit failure')
        with patch.object(update.sqlite3,'connect',side_effect=lambda *a,**kw:Connection(connect(*a,**kw))):
            with self.assertRaisesRegex(sqlite3.OperationalError,'fixture commit'):
                self.run_complete()
        self.assertEqual(self.rows(),before)
        self.assertEqual(self.evidence.read_bytes(),before_reason)
        self.assertTrue(self.entries.exists())

    def test_reason_publish_failure_preserves_old_explanations(self):
        self.fields()
        update.save_reasons([self.reason()],root=self.root)
        before = self.evidence.read_bytes()
        with patch.object(update.os,'replace',side_effect=OSError('fixture output failure')):
            with self.assertRaisesRegex(OSError,'output failure'):
                update.save_reasons([self.reason('torch.other')],root=self.root)
        self.assertEqual(self.evidence.read_bytes(),before)
        self.assertEqual(list(self.evidence.parent.glob('*.tmp')),[])

    def test_cli_uses_flat_entry_arguments(self):
        fields = self.fields()
        self.write([fields])
        scripts = self.root/'scripts'
        scripts.mkdir()
        source = Path(update.__file__).resolve().parent
        for name in ['update_api_support_entries.py','refresh_api_support_db.py','api_refresh_runtime.py','api_first_seen.py']:
            (scripts/name).write_bytes((source/name).read_bytes())
        cmd=[sys.executable,'-B',str(scripts/'update_api_support_entries.py'),
             '--basic-information',str(self.basic),'--database',str(self.database),
             '--new-apis',str(self.names),'--entries',str(self.entries)]
        result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'API_ENTRIES_READY')
        self.assertFalse(self.entries.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
