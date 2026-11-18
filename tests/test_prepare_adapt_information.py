"""Offline checks for API-to-branch preparation; no Git or wheel operations."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import prepare_adapt_information as prepare


class AdaptInformationTests(unittest.TestCase):
    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.database)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='test-adapt-information-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = self.root / 'data/run_data/adapt-fixture-261109'
        self.task.mkdir(parents=True)
        self.basic = self.task / 'basic_information.json'
        self.database = self.root / 'data/api-support.sqlite3'
        with self.connection() as conn:
            conn.execute('CREATE TABLE "API支持数据" ("API名称" TEXT PRIMARY KEY, "本地是否适配" TEXT)')
            conn.executemany('INSERT INTO "API支持数据" VALUES (?,?)',
                             [('Tensor.add','是'), ('torch.Tensor','是'), ('torch.nn.functional.relu','是'),
                              ('torch.already_done','否'), ('view','是')])
        with self.connection() as conn:
            conn.execute('ALTER TABLE "API支持数据" ADD COLUMN "是否对外" TEXT DEFAULT "是"')
            conn.execute('ALTER TABLE "API支持数据" ADD COLUMN "是否支持" TEXT DEFAULT "支持"')
            conn.execute('ALTER TABLE "API支持数据" ADD COLUMN "API真正名字" TEXT')
            conn.execute('ALTER TABLE "API支持数据" ADD COLUMN "适配策略" TEXT DEFAULT "补充testcase"')
        self.output = self.task / 'adapt-information.json'

    def description(self, text, venv='data/venv/python-3.11.17', **fields):
        self.basic.write_text(json.dumps({'problem-description':text, 'venv':venv, **fields},
                                         ensure_ascii=False), encoding='utf-8')

    def run_prepare(self):
        return prepare.prepare(self.basic, root=self.root)

    def test_same_named_api_uses_only_the_selected_table(self):
        with self.connection() as conn:
            conn.execute('CREATE TABLE "API支持数据_2.14" AS SELECT * FROM "API支持数据"')
            conn.execute('UPDATE "API支持数据_2.14" SET "API真正名字"=? WHERE "API名称"=?', ('view', 'Tensor.add'))
            conn.execute('UPDATE "API支持数据_2.14" SET "适配策略"=? WHERE "API名称"=?',
                         ('已支持', 'torch.nn.functional.relu'))
        batch = '2.14 API `torch.Tensor.add` `torch.nn.functional.relu` needs adaptation'
        before = self.database.read_bytes()
        for release, python in [('2.14', '3.10.14'), ('2.14.1', '3.11.17')]:
            with self.subTest(release=release):
                self.description(batch, str(self.root/f'data/venv/torch-{release}/python-{python}'),
                                 torch_npu_version='2.16.0.dev20261101', pytorch_version=release+'+cpu')
                result = self.run_prepare()
                self.assertEqual(result['status'], 'ADAPT_INFORMATION_READY')
                self.assertEqual(result['skipped_apis'], ['torch.nn.functional.relu'])
                self.assertEqual(json.loads(self.output.read_bytes()), {'task-branches':['adapt-view']})
        self.description(batch)
        self.run_prepare()
        self.assertEqual(json.loads(self.output.read_bytes()),
                         {'task-branches':['adapt-Tensor.add', 'adapt-torch.nn.functional.relu']})
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse((self.task/'api-refresh').exists())

    def test_fixed_table_and_all_requested_names_must_exist(self):
        self.description('2.14 API `view` `Tensor.add` needs adaptation', 'data/venv/torch-2.14/python-3.11.17')
        with self.assertRaisesRegex(ValueError, 'table missing: API支持数据_2.14'):
            self.run_prepare()
        with self.connection() as conn:
            conn.execute('CREATE TABLE "API支持数据_2.14" AS SELECT * FROM "API支持数据" WHERE "API名称"=?', ('view',))
        with self.assertRaisesRegex(ValueError, 'API支持数据_2.14.*Tensor.add'):
            self.run_prepare()
        self.assertFalse(self.output.exists())

    def test_fixed_env_rejects_new_api_mode_even_with_nightly_handoff(self):
        self.latest(['view'])
        self.description('New API needs adaptation', 'data/venv/torch-2.14/python-3.11.17')
        with self.assertRaisesRegex(ValueError, 'explicit API names'):
            self.run_prepare()
        self.assertFalse(self.output.exists())

    def test_unrecognized_venv_never_selects_nightly(self):
        for path in [None, '', 'data/venv/torch-wrong/python-3.11.17', 'other/venv/python-3.11.17']:
            with self.subTest(path=path):
                self.description('API `view` needs adaptation', path)
                with self.assertRaisesRegex(ValueError, 'venv path'):
                    self.run_prepare()
                self.assertFalse(self.output.exists())

    def test_multiple_apis_only_local_yes_and_exact_database_names(self):
        self.description('需要适配torch.Tensor.add、torch.nn.functional.relu。另有 torch.already_done。')
        before = self.database.read_bytes()
        result = self.run_prepare()
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8')), {'task-branches':[
            'adapt-Tensor.add', 'adapt-torch.nn.functional.relu']})
        self.assertEqual(result['matched'],3)
        self.assertEqual(result['skipped_apis'],['torch.already_done'])
        self.assertEqual(self.database.read_bytes(),before)
        self.assertEqual({p.name for p in self.task.iterdir()}, {'basic_information.json','adapt-information.json'})

    def test_single_step_zero_description_and_deduplicated_aliases(self):
        self.description('API `torch.Tensor.add` needs adaptation; 同一接口 Tensor.add，torch.Tensor.add().')
        self.run_prepare()
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8'))['task-branches'], ['adapt-Tensor.add'])

    def test_step_zero_batch_keeps_order_and_reports_each_unknown_name(self):
        self.description('API `torch.nn.functional.relu` `torch.Tensor.add` needs adaptation')
        self.run_prepare()
        self.assertEqual(json.loads(self.output.read_text())['task-branches'],
                         ['adapt-torch.nn.functional.relu', 'adapt-Tensor.add'])
        before = self.output.read_bytes()
        for unknown in ('Optimizer.missing', 'missing'):
            with self.subTest(unknown=unknown):
                self.description(f'API `torch.Tensor.add` `{unknown}` needs adaptation')
                with self.assertRaisesRegex(ValueError, unknown):
                    self.run_prepare()
                self.assertEqual(self.output.read_bytes(), before)

    def latest(self, names=()):
        self.description('New API needs adaptation', pytorch_version='2.16.0a0+gitfixture')
        path = self.task/'api-refresh/added-apis.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(list(names)))

    def test_latest_uses_only_actual_additions_and_existing_api_filters(self):
        self.latest(['torch.new_alias','torch.new_docgone','torch.new_public',
                     'torch.new_offline','torch.new_internal','torch.new_done','torch.new_disabled'])
        with self.connection() as conn:
            conn.execute('ALTER TABLE "API支持数据" ADD COLUMN "api首次出现版本" TEXT')
            # All old rows share the current version; none are new task candidates.
            conn.execute('UPDATE "API支持数据" SET "api首次出现版本"=?', ('2.16',))
            conn.executemany('INSERT INTO "API支持数据" '
                             '("API名称","本地是否适配","是否对外","是否支持",'
                             '"API真正名字","适配策略","api首次出现版本") VALUES (?,?,?,?,?,?,?)', [
                ('torch.new_alias',None,'是','未提供','Tensor.add',None,'2.16'),
                ('torch.new_public','是','是','未提供',None,'补充testcase','2.16'),
                ('torch.new_docgone','是','是','文档删除，待下线',None,'补充testcase','2.16'),
                ('torch.new_offline','是','是','API删除，待下线',None,'补充testcase','2.16'),
                ('torch.new_internal','是','否','支持',None,'补充testcase','2.16'),
                ('torch.new_done','是','是','支持',None,'已支持','2.16'),
                ('torch.new_disabled','否','是','未提供',None,'补充testcase','2.16')])
        mapping = self.root/'data/branch-mapping.json'
        mapping.write_text(json.dumps({'adapt-torch.new_alias':'existing-work'}))
        # The completion queue is intentionally empty: it is not the task source.
        queue = self.task/'api-refresh/new-apis.json'
        queue.write_text('[]')
        before_db, before_mapping = self.database.read_bytes(), mapping.read_bytes()
        result = self.run_prepare()
        self.assertEqual(json.loads(self.output.read_text())['task-branches'],
                         ['adapt-torch.new_alias', 'adapt-torch.new_docgone', 'adapt-torch.new_public'])
        self.assertEqual(result['resolved_apis']['torch.new_alias'], 'Tensor.add')
        self.assertEqual(set(result['skipped_apis']),
                         {'torch.new_offline','torch.new_internal','torch.new_done','torch.new_disabled'})
        first = self.output.read_bytes()
        self.run_prepare()
        self.assertEqual(self.output.read_bytes(), first)
        self.assertEqual(self.database.read_bytes(), before_db)
        self.assertEqual(mapping.read_bytes(), before_mapping)

    def test_latest_no_actual_additions_stops_and_clears_stale_dispatch(self):
        self.latest()
        with self.connection() as conn:
            conn.execute('ALTER TABLE "API支持数据" ADD COLUMN "api首次出现版本" TEXT')
            conn.execute('UPDATE "API支持数据" SET "api首次出现版本"=?', ('2.16',))
        self.assertEqual(self.run_prepare()['status'], 'NO_ADAPTATION_REQUIRED')
        self.assertFalse(self.output.exists())
        self.output.write_text(json.dumps({'task-branches':['adapt-view'],
                                          'adapt-plan':{'old':'plan'}, 'dispatcher':{'old':'dispatch'},
                                          'api-status':{'view':'支持'}, 'other':'keep'}))
        self.assertEqual(self.run_prepare()['status'], 'NO_ADAPTATION_REQUIRED')
        self.assertEqual(json.loads(self.output.read_text()), {'task-branches':[], 'other':'keep'})

    def test_latest_missing_or_invalid_handoff_preserves_existing_output(self):
        self.description('New API needs adaptation')
        self.output.write_text('{"task-branches":["adapt-view"]}')
        before = self.output.read_bytes()
        with self.assertRaises(FileNotFoundError):
            self.run_prepare()
        self.latest()
        path = self.task/'api-refresh/added-apis.json'
        for value in ({'apis':[]}, ['torch.missing'], [None]):
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    self.run_prepare()
                self.assertEqual(self.output.read_bytes(), before)

    def test_latest_handoff_does_not_require_a_version_column_or_wheel_version(self):
        self.latest(['Tensor.add'])
        self.description('New API needs adaptation')
        self.run_prepare()
        self.assertEqual(json.loads(self.output.read_text())['task-branches'], ['adapt-Tensor.add'])

    def test_shorter_representative_reuses_existing_task_and_checkpoint(self):
        with self.connection() as conn:
            conn.executemany('INSERT INTO "API支持数据" '
                             '("API名称","本地是否适配","是否对外","是否支持","API真正名字","适配策略") '
                             'VALUES (?,?,?,?,?,?)', [
                ('torch.new_api','是','是','未提供','torch.a','补充testcase'),
                ('torch.a','是','是','未提供',None,'补充testcase')])
        task = 'adapt-torch.new_api'
        mapping = self.root/'data/branch-mapping.json'
        mapping.write_text(json.dumps({task:'existing-work','fix-timeout':'other-work'}), encoding='utf-8')
        checkpoint = {'workflow-version':2,'status':'FAILED','branch':'existing-work',
                      'requested-branches':[task],'remaining-branches':[task]}
        self.output.write_text(json.dumps({'task-branches':[task],'development-branch':'existing-work',
                                          'git-control':checkpoint}), encoding='utf-8')
        before_mapping, before_db = mapping.read_bytes(), self.database.read_bytes()
        self.description('API `torch.new_api`; API `torch.a`')
        self.run_prepare()
        saved = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(saved['task-branches'], [task])
        self.assertEqual(saved['development-branch'], 'existing-work')
        self.assertEqual(saved['git-control'], checkpoint)
        self.assertEqual(mapping.read_bytes(), before_mapping)
        self.assertEqual(self.database.read_bytes(), before_db)

    def test_supported_representative_is_not_dispatched_again(self):
        with self.connection() as conn:
            conn.execute('UPDATE "API支持数据" SET "适配策略"=? WHERE "API名称"=?', ('已支持','Tensor.add'))
            conn.execute('UPDATE "API支持数据" SET "API真正名字"=? WHERE "API名称"=?', ('Tensor.add','view'))
        self.description('API `Tensor.add` and API `view` and API `torch.nn.functional.relu` need adaptation')
        result = self.run_prepare()
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8'))['task-branches'], ['adapt-torch.nn.functional.relu'])
        self.assertEqual(set(result['skipped_apis']), {'Tensor.add','view'})

    def test_all_supported_stops_and_clears_old_tasks(self):
        with self.connection() as conn:
            conn.execute('UPDATE "API支持数据" SET "适配策略"=? WHERE "API名称"=?', ('已支持','Tensor.add'))
        self.description('API `Tensor.add` needs adaptation')
        self.output.write_text(json.dumps({'task-branches':['adapt-Tensor.add'],'api-status':{'Tensor.add':'支持'}}),encoding='utf-8')
        self.assertEqual(self.run_prepare()['status'], 'NO_ADAPTATION_REQUIRED')
        saved = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(saved['task-branches'], [])
        self.assertNotIn('api-status', saved)

    def test_class_alias_and_bare_api_name(self):
        self.description('API `Tensor` needs adaptation; API `view` needs adaptation.')
        self.run_prepare()
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8'))['task-branches'],
                         ['adapt-torch.Tensor', 'adapt-view'])

    def test_does_not_use_parent_prefix_of_unknown_member(self):
        self.description('API `torch.Tensor.add.missing` needs adaptation')
        with self.assertRaisesRegex(ValueError,'not registered'):
            self.run_prepare()
        self.assertFalse(self.output.exists())

    def test_unknown_api_cannot_silently_disappear_from_a_mixed_request(self):
        self.description('适配 torch.Tensor.add 和 torch.missing_api')
        self.output.write_text('{"task-branches":{"old":"adapt-old"}}',encoding='utf-8')
        before = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError,'torch.missing_api'):
            self.run_prepare()
        self.assertEqual(self.output.read_bytes(),before)

    def test_explicitly_labelled_unknown_short_api_is_not_ignored(self):
        self.description('API `Tensor.add` needs adaptation; API `Optimizer.missing` needs adaptation.')
        with self.assertRaisesRegex(ValueError,'Optimizer.missing'):
            self.run_prepare()
        self.assertFalse(self.output.exists())

    def test_all_no_stops_without_creating_output(self):
        self.description('API `torch.already_done` needs adaptation')
        result = self.run_prepare()
        self.assertEqual(result['status'],'NO_ADAPTATION_REQUIRED')
        self.assertNotIn('output',result)
        self.assertFalse(self.output.exists())

    def test_all_no_clears_old_tasks_and_preserves_other_material(self):
        self.description('API `torch.already_done` needs adaptation')
        self.output.write_text('{"task-branches":{"old":"adapt-old"},"base-commit":"keep"}',encoding='utf-8')
        before = self.output.read_bytes()
        result = self.run_prepare()
        self.assertEqual(result['status'],'NO_ADAPTATION_REQUIRED')
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8')), {'task-branches':[], 'base-commit':'keep'})

    def test_alias_uses_representative_local_flag_and_is_deduplicated(self):
        with self.connection() as conn:
            conn.execute('UPDATE "API支持数据" SET "API真正名字"=?, "本地是否适配"=? WHERE "API名称"=?',
                         ('Tensor.add', '否', 'view'))
        self.description('API `view` and API `Tensor.add` need adaptation')
        result = self.run_prepare()
        self.assertEqual(json.loads(self.output.read_text())['task-branches'], ['adapt-Tensor.add'])
        self.assertEqual(result['resolved_apis']['view'], 'Tensor.add')

    def test_offline_and_internal_original_names_are_not_rescued_by_alias(self):
        for column, value in [('是否支持', 'API删除，待下线'), ('是否对外', '否')]:
            with self.subTest(column=column):
                with self.connection() as conn:
                    conn.execute('UPDATE "API支持数据" SET "是否对外"=?,"是否支持"=?,"API真正名字"=? '
                                 'WHERE "API名称"=?', ('是','支持','Tensor.add','view'))
                    conn.execute('UPDATE "API支持数据" SET "'+column+'"=? WHERE "API名称"=?', (value,'view'))
                self.description('API `view` needs adaptation')
                self.assertEqual(self.run_prepare()['status'], 'NO_ADAPTATION_REQUIRED')
                self.assertFalse(self.output.exists())

    def test_offline_representative_is_filtered_but_document_removal_is_allowed(self):
        with self.connection() as conn:
            conn.execute('UPDATE "API支持数据" SET "API真正名字"=? WHERE "API名称"=?', ('Tensor.add','view'))
            conn.execute('UPDATE "API支持数据" SET "是否支持"=? WHERE "API名称"=?', ('API删除，待下线','Tensor.add'))
        self.description('API `view` needs adaptation')
        self.assertEqual(self.run_prepare()['status'], 'NO_ADAPTATION_REQUIRED')
        with self.connection() as conn:
            conn.execute('UPDATE "API支持数据" SET "是否支持"=? WHERE "API名称"=?', ('文档删除，待下线','Tensor.add'))
        self.assertEqual(self.run_prepare()['status'], 'ADAPT_INFORMATION_READY')

    def test_empty_filtered_selection_clears_plan_and_retains_git_recovery(self):
        self.description('API `Tensor.add` needs adaptation')
        checkpoint = {'status':'MERGE_CONFLICT', 'requested-branches':['adapt-Tensor.add']}
        self.output.write_text(json.dumps({'task-branches':['adapt-Tensor.add'], 'git-control':checkpoint,
                                          'development-branch':'keep', 'adapt-plan':{'old':{}}, 'dispatcher':{}}))
        with self.connection() as conn:
            conn.execute('UPDATE "API支持数据" SET "是否支持"=? WHERE "API名称"=?', ('API删除，待下线','Tensor.add'))
        self.assertEqual(self.run_prepare()['status'], 'NO_ADAPTATION_REQUIRED')
        saved = json.loads(self.output.read_text())
        self.assertEqual(saved['task-branches'], [])
        self.assertEqual(saved['git-control'], checkpoint)
        self.assertNotIn('adapt-plan', saved)
        self.assertNotIn('dispatcher', saved)

    def test_mixed_selection_drops_offline_tasks_during_pending_recovery(self):
        self.description('API `Tensor.add` and API `view` need adaptation')
        checkpoint = {'status':'MERGE_CONFLICT'}
        self.output.write_text(json.dumps({'task-branches':['adapt-Tensor.add','adapt-view'], 'git-control':checkpoint}))
        with self.connection() as conn:
            conn.execute('UPDATE "API支持数据" SET "是否支持"=? WHERE "API名称"=?', ('API删除，待下线','view'))
        self.run_prepare()
        saved = json.loads(self.output.read_text())
        self.assertEqual(saved['task-branches'], ['adapt-Tensor.add'])
        self.assertEqual(saved['git-control'], checkpoint)

    def test_missing_description_or_no_explicit_api_does_not_create_output(self):
        for description in [None, '', 'Please view the problem details.']:
            self.description(description)
            with self.assertRaises(ValueError):
                self.run_prepare()
        self.assertFalse(self.output.exists())

    def test_links_do_not_create_false_api_names(self):
        self.description('适配 [torch.nn.functional.relu](https://docs.pytorch.org/docs/main/generated/torch.nn.functional.relu.html)。')
        self.run_prepare()
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8'))['task-branches'],
                         ['adapt-torch.nn.functional.relu'])

    def test_unknown_local_flag_is_not_assumed_yes_or_no(self):
        with self.connection() as conn:
            conn.execute('UPDATE "API支持数据" SET "本地是否适配"=NULL WHERE "API名称"=?', ('Tensor.add',))
        self.description('API `Tensor.add` needs adaptation')
        with self.assertRaisesRegex(ValueError,'must be 是 or 否'):
            self.run_prepare()
        self.assertFalse(self.output.exists())

    def test_failed_output_does_not_modify_old_file_and_cleans_temporary(self):
        self.description('API `Tensor.add` needs adaptation')
        self.output.write_text('{"task-branches":{}}',encoding='utf-8')
        before = self.output.read_bytes()
        with patch.object(prepare.os,'replace',side_effect=OSError('fixture output failure')):
            with self.assertRaisesRegex(OSError,'output failure'):
                self.run_prepare()
        self.assertEqual(self.output.read_bytes(),before)
        self.assertEqual(list(self.task.glob('*.tmp')),[])

    def test_retry_is_stable_and_existing_other_values_are_preserved(self):
        self.description('API `Tensor.add` needs adaptation')
        self.output.write_text('{"base-commit":"keep"}',encoding='utf-8')
        self.run_prepare()
        before = self.output.read_bytes()
        self.run_prepare()
        self.assertEqual(self.output.read_bytes(),before)
        self.assertEqual(json.loads(before)['base-commit'],'keep')

    def test_cli_outputs_only_requested_run_file(self):
        scripts = self.root/'scripts'
        scripts.mkdir()
        source = Path(prepare.__file__).resolve().parent
        for name in ['prepare_adapt_information.py','branch_mapping.py','refresh_api_support_db.py','api_refresh_runtime.py','api_first_seen.py']:
            (scripts/name).write_bytes((source/name).read_bytes())
        self.description('API `Tensor.add` needs adaptation')
        result = subprocess.run([sys.executable,'-B',str(scripts/'prepare_adapt_information.py'),
                                 '--basic-information',str(self.basic),'--database',str(self.database)],
                                capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['branches'],1)
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8')), {'task-branches':['adapt-Tensor.add']})


    def test_changed_selection_clears_old_step3_result_only(self):
        self.description('API `Tensor.add` needs adaptation')
        self.output.write_text(json.dumps({'task-branches': {'view': 'adapt-view'},
                                          'development-branch': 'adapt-set-old',
                                          'git-control': {'status': 'BRANCH_READY'},
                                          'adapt-plan': {'step1': {'agentname': 'api-adaptation',
                                                                  'task-branches': ['adapt-view']}},
                                          'dispatcher': {'agents': ['old-agent']}, 'other': 'keep'}), encoding='utf-8')
        self.run_prepare()
        result = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertNotIn('development-branch', result)
        self.assertNotIn('git-control', result)
        self.assertNotIn('adapt-plan', result)
        self.assertNotIn('dispatcher', result)
        self.assertEqual(result['other'], 'keep')

    def test_api_status_survives_same_selection_but_not_a_new_batch(self):
        self.description('API `Tensor.add` needs adaptation')
        self.run_prepare()
        result = json.loads(self.output.read_text(encoding='utf-8'))
        result['api-status'] = {'Tensor.add': '支持'}
        self.output.write_text(json.dumps(result), encoding='utf-8')
        self.run_prepare()
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8'))['api-status'], {'Tensor.add': '支持'})
        self.description('API `view` needs adaptation')
        self.run_prepare()
        self.assertNotIn('api-status', json.loads(self.output.read_text(encoding='utf-8')))

    def test_changed_selection_preserves_pending_step3_recovery(self):
        self.description('API `Tensor.add` needs adaptation')
        self.output.write_text('{"task-branches":{"view":"adapt-view"},"git-control":{"status":"MERGE_CONFLICT"}}', encoding='utf-8')
        before = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, 'pending Step 3'):
            self.run_prepare()
        self.assertEqual(self.output.read_bytes(), before)

    def test_merged_source_is_filtered_without_losing_child_recovery(self):
        self.description('API `Tensor.add`; API `view`')
        checkpoint = {'workflow-version': 2, 'status': 'MERGE_CONFLICT',
                      'requested-branches': ['adapt-Tensor.add','adapt-view'],
                      'remaining-branches': ['adapt-view'], 'branch': 'adapt-set-child'}
        self.output.write_text(json.dumps({'task-branches': ['adapt-Tensor.add','adapt-view'],
                                          'git-control': checkpoint}), encoding='utf-8')
        with self.connection() as conn:
            conn.execute('UPDATE "API支持数据" SET "适配策略"=? WHERE "API名称"=?',('已支持','Tensor.add'))
        self.run_prepare()
        saved = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(saved['task-branches'], ['adapt-view'])
        self.assertEqual(saved['git-control'], checkpoint)

    def test_same_selection_preserves_pending_step3_recovery(self):
        self.description('API `Tensor.add` needs adaptation')
        self.output.write_text('{"task-branches":{"Tensor.add":"adapt-Tensor.add"},"git-control":{"status":"MERGE_CONFLICT","branch":"adapt-set-existing"}}', encoding='utf-8')
        self.run_prepare()
        result = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(result['git-control']['branch'], 'adapt-set-existing')
        self.assertEqual(result['task-branches'], ['adapt-Tensor.add'])



    def test_same_original_selection_restores_full_list_without_losing_pruned_checkpoint(self):
        self.description('API `Tensor.add` needs adaptation; API `view` needs adaptation')
        original = ['adapt-Tensor.add', 'adapt-view']
        checkpoint = {'workflow-version': 2, 'status': 'FAILED', 'branch': 'adapt-set-existing',
                      'requested-branches': original, 'remaining-branches': ['adapt-view']}
        self.output.write_text(json.dumps({'task-branches': ['adapt-view'],
                                          'git-control': checkpoint}), encoding='utf-8')
        self.run_prepare()
        result = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(result['task-branches'], original)
        self.assertEqual(result['git-control'], checkpoint)

    def test_legacy_object_checkpoint_keeps_recovery_and_outputs_array(self):
        self.description('API `Tensor.add` needs adaptation; API `view` needs adaptation')
        checkpoint = {'workflow-version': 2, 'status': 'FAILED', 'branch': 'adapt-set-old',
                      'requested-branches': {'Tensor.add': 'adapt-Tensor.add', 'view': 'adapt-view'},
                      'remaining-branches': {'view': 'adapt-view'}}
        self.output.write_text(json.dumps({'task-branches': {'view': 'adapt-view'},
                                          'git-control': checkpoint}), encoding='utf-8')
        self.run_prepare()
        result = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(result['task-branches'], ['adapt-Tensor.add', 'adapt-view'])
        self.assertEqual(result['git-control'], checkpoint)



if __name__ == '__main__':
    unittest.main(verbosity=2)
