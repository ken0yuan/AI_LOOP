from contextlib import closing
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
import pr_tracking
import reconcile_prs as script


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root/'data/source_code/torch_npu'
        self.repo.mkdir(parents=True)
        self.origin = self.root/'external/team/fork.git'
        self.upstream = self.root/'external/team/upstream.git'
        self.origin.parent.mkdir(parents=True)
        self.run_git(None,'init','--bare',str(self.origin))
        self.run_git(None,'init','--bare',str(self.upstream))
        self.git('init','-b','master')
        self.git('config','user.name','Test')
        self.git('config','user.email','test@example.invalid')
        self.git('config','commit.gpgsign','false')
        (self.repo/'base.txt').write_text('base\n')
        self.git('add','base.txt');self.git('commit','-m','base')
        self.git('remote','add','origin',self.origin.as_uri())
        self.git('remote','add','upstream',self.upstream.as_uri())
        self.git('push','origin','master');self.git('push','upstream','master')
        self.branch = 'adapt-set-test123'
        self.git('checkout','-b',self.branch)
        path=self.repo/'test/api_supplement/torch/foo/test_torch_foo.py'
        path.parent.mkdir(parents=True);path.write_text('import unittest\n')
        self.git('add','test');self.git('commit','-m','add test')
        self.sha=self.git('rev-parse','HEAD')
        self.git('push','origin',self.branch)
        self.git('checkout','master')
        self.db=self.root/'data/api-support.sqlite3'
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('CREATE TABLE "API支持数据" ("API名称" TEXT PRIMARY KEY,"是否对外" TEXT,'
                      '"是否支持" TEXT,"API真正名字" TEXT,"适配策略" TEXT)')
            c.executemany('INSERT INTO "API支持数据" VALUES (?,?,?,?,?)',[
                ('torch.foo','是','未提供',None,'补充testcase'),
                ('torch.alias','是','未提供','torch.foo',None),
                ('torch.other','是','未提供',None,'补充testcase')])
            for column,default in [('分类待审核','是'),('分类','基础计算'),('细分模块','基础计算'),('细分场景','函数API')]:
                c.execute('ALTER TABLE "API支持数据" ADD COLUMN "'+column+'" TEXT DEFAULT "'+default+'"')
        self.mapping=self.root/'data/branch-mapping.json'
        self.mapping.write_text(json.dumps({'adapt-torch.foo':self.branch,'adapt-torch.other':'other-branch'}))
        self.run=self.root/'data/run_data/previous';self.run.mkdir(parents=True)
        self.record=self.run/'pr.json'
        self.record.write_text(json.dumps({'number':1,'repository':'team/upstream','fork':'team/fork',
                                          'head':self.branch,'commit':self.sha,'status':'created'}))
        self.adapt=self.run/'adapt-information.json'
        self.adapt.write_text(json.dumps({'task-branches':['adapt-torch.foo'],'api-status':{'torch.foo':'支持'}}))
        self.remote={'number':1,'merged':True,'state':'open','head':{'ref':self.branch,'sha':self.sha,'repo':{'full_name':'team/fork'}},
                     'base':{'ref':'master','repo':{'full_name':'team/upstream'}}}

    def run_git(self,cwd,*args,check=True):
        p=subprocess.run(['git',*args],cwd=cwd,capture_output=True,text=True)
        if check and p.returncode:self.fail(p.stderr)
        return p.stdout.strip()

    def git(self,*args,**kwargs):
        return self.run_git(self.repo,*args,**kwargs)

    def merge_upstream(self):
        self.git('merge','--ff-only',self.branch)
        self.git('push','upstream','master')

    def rows(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            return c.execute('SELECT * FROM "API支持数据" ORDER BY "API名称"').fetchall()

    def reconcile(self,remote=None):
        return script.reconcile(root=self.root,viewer=lambda _:remote or self.remote)

    def fixed_version_pr(self):
        fixed_branch = 'adapt-2.15-set-123456789abc'
        self.git('branch', '-m', self.branch, fixed_branch)
        self.branch = fixed_branch
        self.merge_upstream()
        self.remote['head']['ref'] = fixed_branch
        record = json.loads(self.record.read_text())
        record.update(head=fixed_branch)
        record['api-status'] = {'torch.alias': '支持'}
        self.record.write_text(json.dumps(record))
        groups = {'nightly': {'adapt-torch.other': 'adapt-nightly-set-abcdef123456'},
                  '2.15': {'adapt-torch.other': fixed_branch, 'adapt-torch.foo': 'another-branch'}}
        self.mapping.write_text(json.dumps(groups))
        return groups

    def test_fixed_pr_closes_only_its_version_table_group_and_evidence(self):
        groups = self.fixed_version_pr()
        nightly = self.rows()
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('CREATE TABLE "API支持数据_2.15" AS SELECT * FROM "API支持数据"')
            c.execute('UPDATE "API支持数据_2.15" SET "API真正名字"=? WHERE "API名称"=?',
                      ('torch.other', 'torch.alias'))
        directory = self.db.parent / 'api-classification'
        directory.mkdir()
        shared = directory / 'classification-evidence.jsonl'
        reasons = b'{"api":"torch.alias"}\n{"api":"torch.foo"}\n'
        pending = {'kind':'definition', 'level':'category', 'parent':[], 'name':'基础计算', 'review_status':'待审核'}
        reasons += (json.dumps(pending) + '\n').encode()
        shared.write_bytes(reasons)
        self.assertTrue(self.reconcile()['complete'])
        self.assertEqual(self.rows(), nightly)
        with closing(sqlite3.connect(self.db)) as c:
            rows = c.execute('SELECT "API名称","是否支持","适配策略","分类待审核" FROM "API支持数据_2.15" '
                             'ORDER BY "API名称"').fetchall()
            self.assertEqual(rows, [('torch.alias','未提供',None,'是'), ('torch.foo','未提供','补充testcase','是'),
                                    ('torch.other','支持','已支持','否')])
        del groups['2.15']['adapt-torch.other']
        self.assertEqual(json.loads(self.mapping.read_text()), groups)
        self.assertEqual(shared.read_bytes(), reasons)
        self.assertTrue(json.loads(self.record.read_text())['cleanup-completed'])

    def test_missing_fixed_table_or_api_keeps_pr_pending(self):
        groups = self.fixed_version_pr()
        nightly = self.rows()
        for missing in ('table', 'api'):
            if missing == 'api':
                with closing(sqlite3.connect(self.db)) as c, c:
                    c.execute('CREATE TABLE "API支持数据_2.15" AS SELECT * FROM "API支持数据" WHERE 0')
            with self.subTest(missing=missing):
                self.assertFalse(self.reconcile()['complete'])
                self.assertTrue(self.record.exists())
                self.assertEqual(json.loads(self.mapping.read_text()), groups)
                self.assertEqual(self.rows(), nightly)

    def test_merge_backfills_removes_task_mapping_and_preserves_pr_and_branches(self):
        self.merge_upstream();before_refs=self.git('show-ref');current=self.git('branch','--show-current')
        fixed = {'adapt-torch.foo': 'adapt-2.15-set-beef12345678'}
        self.mapping.write_text(json.dumps({'nightly': json.loads(self.mapping.read_text()), '2.15': fixed}))
        result=self.reconcile()
        self.assertTrue(result['complete'])
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT "是否支持","适配策略","分类待审核" FROM "API支持数据" WHERE "API名称"="torch.foo"').fetchone(),('支持','已支持','否'))
            self.assertIsNone(c.execute('SELECT "适配策略" FROM "API支持数据" WHERE "API名称"="torch.alias"').fetchone()[0])
            self.assertEqual(c.execute('SELECT "分类待审核" FROM "API支持数据" WHERE "API名称"="torch.other"').fetchone()[0],'是')
        self.assertEqual(json.loads(self.mapping.read_text())['nightly'],{'adapt-torch.other':'other-branch'})
        self.assertEqual(json.loads(self.mapping.read_text())['2.15'], fixed)
        self.assertEqual(self.git('show-ref'),before_refs)
        self.assertEqual(self.git('branch','--show-current'),current)
        self.assertTrue(self.git('branch','--list',self.branch))
        self.assertTrue(self.run_git(None,'--git-dir',str(self.origin),'branch','--list',self.branch))
        self.assertTrue(json.loads(self.record.read_text())['cleanup-completed'])

    def test_open_pr_does_not_change_database_or_branches(self):
        before=self.rows();result=self.reconcile(dict(self.remote,merged=False))
        self.assertTrue(result['complete']);self.assertEqual(self.rows(),before)
        self.assertTrue(self.git('branch','--list',self.branch))
        self.assertEqual(result['open_pr_reviews'],
                         [{'run_id':'previous','run_dir':'data/run_data/previous',
                           'pr_record':'data/run_data/previous/pr.json','pr':1,
                           'repository':'team/upstream','fork':'team/fork','branch':self.branch,
                           'commit':self.sha,'head_sha':self.sha,'head_updated':False}])

    def test_open_pr_with_advanced_head_is_still_listed_for_review_check(self):
        advanced='f'*40
        remote=dict(self.remote,merged=False,head=dict(self.remote['head'],sha=advanced))
        before=self.rows()
        result=self.reconcile(remote)
        self.assertTrue(result['complete']);self.assertEqual(self.rows(),before)
        entry=result['open_pr_reviews'][0]
        self.assertEqual((entry['commit'],entry['head_sha'],entry['head_updated']),(self.sha,advanced,True))

    def test_open_pr_identity_mismatch_stays_pending_and_is_not_listed(self):
        remote=dict(self.remote,merged=False,head=dict(self.remote['head'],repo={'full_name':'another/fork'}))
        before=self.rows()
        result=self.reconcile(remote)
        self.assertFalse(result['complete']);self.assertEqual(self.rows(),before)
        self.assertEqual(result['open_pr_reviews'],[])
        self.assertEqual([item['pr_record'] for item in result['pending']],
                         [str(self.root/'data/run_data/previous/pr.json')])
        self.assertIn('不一致',result['pending'][0]['reason'])
        self.assertTrue(self.record.exists())

    def test_closed_unmerged_pr_is_not_listed_for_review_check(self):
        result=self.reconcile(dict(self.remote,merged=False,state='closed'))
        self.assertTrue(result['complete'])
        self.assertEqual(result['open_pr_reviews'],[])
        index = self.root / 'data/pr-tracking.json'
        self.assertEqual(json.loads(index.read_text())[0]['pr_status'], 'closed')
        with patch.object(Path, 'glob', side_effect=AssertionError('historical runs rescanned')):
            reopened = self.reconcile(dict(self.remote, merged=False))
            self.assertEqual([pr['pr'] for pr in reopened['open_pr_reviews']], [1])
            self.assertEqual(json.loads(index.read_text())[0]['pr_status'], 'open')
            self.assertTrue(self.reconcile()['complete'])
        self.assertEqual(json.loads(index.read_text())[0]['pr_status'], 'merged')

    def test_retargeted_or_renumbered_pr_is_not_sent_for_repairs(self):
        for change in ({'number':2}, {'base':dict(self.remote['base'], ref='release')}):
            with self.subTest(change=change):
                result=self.reconcile(dict(self.remote, merged=False, **change))
                self.assertFalse(result['complete'])
                self.assertEqual(result['open_pr_reviews'],[])

    def test_comments_paginate_and_keep_edited_replies_with_context(self):
        first=[{'id':i,'updated_at':'v1','resolved':True} for i in range(1,101)]
        thread={'id':101,'updated_at':'v1','discussion_id':'d-101','comment_type':'diff_comment',
                'diff_position':{'new_path':'test_foo.py','new_line':17}, 'body':'请检查边界',
                'reply':[{'id':102,'updated_at':'v2','body':'请按新边界补测'},
                         {'id':105,'updated_at':'v1','body':'已按意见修改'}]}
        ordinary={'id':103,'updated_at':'v1','comment_type':'pr_comment','body':'请检查之前的数据类型'}
        ledger={'processed':[{'id':'101','updated_at':'v1'}, {'id':102,'updated_at':'v1'},
                             {'id':104,'updated_at':'v1','reply_id':105}]}
        (self.run/'review-handling.json').write_text(json.dumps(ledger))
        with patch.object(script,'view_pr',return_value=dict(self.remote,merged=False)), \
             patch.object(script.subprocess,'check_output',side_effect=[
                 json.dumps(first), json.dumps([thread,ordinary,{'id':104,'updated_at':'v1'}])
             ]) as get:
            result=script.read_review_comments(self.record,root=self.root)
        saved=json.loads((self.root/result['review_file']).read_text())
        self.assertEqual(self.root/result['review_file'],self.run/'pr-review-comments.json')
        self.assertEqual((result['discussion_count'],result['candidate_count']),(103,2))
        self.assertEqual(len(saved['discussions']),103)
        self.assertEqual(saved['discussions'][0],{'candidate_ids':[],'discussion':first[0]})
        self.assertEqual(saved['discussions'][-3:],[
            {'candidate_ids':[102],'discussion':thread}, {'candidate_ids':[103],'discussion':ordinary},
            {'candidate_ids':[],'discussion':{'id':104,'updated_at':'v1'}}])
        self.assertEqual([call.args[0][2] for call in get.call_args_list],[
            f'repos/team/upstream/pulls/1/comments?page={n}&per_page=100&direction=asc' for n in (1,2)])

    def test_closed_pr_skips_comment_query(self):
        with patch.object(script,'view_pr',return_value=self.remote), patch.object(script.subprocess,'check_output') as get:
            self.assertIsNone(script.read_review_comments(self.record,root=self.root)['review_file'])
            get.assert_not_called()

    def test_empty_review_snapshot_and_interrupted_refresh(self):
        remote=dict(self.remote,merged=False)
        with patch.object(script,'view_pr',return_value=remote), patch.object(
                script.subprocess,'check_output',return_value='[]'):
            result=script.read_review_comments(self.record,root=self.root)
        path=self.root/result['review_file']
        original=path.read_bytes()
        snapshot=json.loads(original)
        self.assertEqual(snapshot['discussions'],[])
        self.assertEqual(snapshot['head_sha'],self.sha)
        self.assertEqual(result['candidate_count'],0)
        page=[{'id':i,'updated_at':'v1'} for i in range(100)]
        with patch.object(script,'view_pr',return_value=remote), patch.object(
                script.subprocess,'check_output',side_effect=[json.dumps(page), OSError('network')]):
            with self.assertRaises(OSError):
                script.read_review_comments(self.record,root=self.root)
        self.assertEqual(path.read_bytes(),original)

    def test_open_pr_checklist_survives_incomplete_merged_sync(self):
        blocked=self.root/'data/run_data/blocked';blocked.mkdir()
        blocked_record=blocked/'pr.json'
        blocked_record.write_text(json.dumps({'number':2,'repository':'team/upstream','fork':'team/fork',
                                              'head':self.branch,'commit':self.sha,'status':'created'}))
        open_remote=dict(self.remote,merged=False)
        viewer=lambda record: open_remote if record['number']==1 else self.remote
        result=script.reconcile(root=self.root,viewer=viewer)
        self.assertFalse(result['complete'])
        self.assertEqual([item['pr'] for item in result['open_pr_reviews']],[1])
        self.assertEqual([item['pr_record'] for item in result['pending']],[str(blocked_record)])

    def test_sync_returns_merged_results_and_open_prs_together(self):
        second = self.run.with_name('second')
        second.mkdir()
        record = json.loads(self.record.read_text())
        record['number'] = 2
        (second/'pr.json').write_text(json.dumps(record))
        def viewer(record):
            return dict(self.remote, number=record['number'], merged=record['number']==1)
        result = script.reconcile(root=self.root, viewer=viewer)
        self.assertTrue(result['complete'])
        self.assertEqual([row['pr'] for row in result['merged']], [1])
        self.assertEqual([row['pr'] for row in result['open_pr_reviews']], [2])
        self.assertTrue(json.loads(self.record.read_text())['cleanup-completed'])
        self.assertEqual({row['run_data']: row['pr_status'] for row in json.loads(
            (self.root/'data/pr-tracking.json').read_text())},
            {'data/run_data/previous': 'merged', 'data/run_data/second': 'open'})

    def test_merge_cleanup_is_idempotent(self):
        self.merge_upstream()
        receipt = json.loads(self.record.read_text())
        self.assertTrue(self.reconcile()['complete'])
        self.assertEqual(json.loads(self.record.read_text()), dict(receipt, **{'cleanup-completed': True}))
        index = self.root/'data/pr-tracking.json'
        expected = [{'pr_url': pr_tracking.receipt_url(receipt), 'run_data': 'data/run_data/previous',
                     'pr_status': 'merged'}]
        self.assertEqual(json.loads(index.read_text()), expected)
        # Recover after a receipt write followed by an interrupted index update.
        index.write_text(json.dumps([dict(expected[0], pr_status='merged_pending')]))
        with self.assertRaisesRegex(ValueError, 'already merged'):
            pr_tracking.register_receipt(self.run, receipt)
        def unexpected_query(record):
            self.fail('Completed PR was queried again')
        for rebuild in (False, True):
            if rebuild:
                index.unlink()
            result = script.reconcile(root=self.root, viewer=unexpected_query)
            self.assertTrue(result['complete'])
            self.assertEqual(result['merged'], [])
            self.assertEqual(result['open_pr_reviews'], [])
            self.assertEqual(json.loads(index.read_text()), expected)

    def test_completed_index_entry_skips_receipt(self):
        self.assertTrue(self.reconcile()['complete'])
        receipt = self.record.read_bytes()
        real_open = Path.open
        def forbid_receipt(path, *args, **kwargs):
            if path == self.record:
                self.fail('A merged index entry must skip its receipt')
            return real_open(path, *args, **kwargs)
        def forbid_query(record):
            self.fail('A merged index entry must skip its remote PR')
        with patch.object(Path, 'open', forbid_receipt):
            result = script.reconcile(root=self.root, viewer=forbid_query)
            self.assertTrue(result['complete'])
            self.assertEqual(result['merged'], [])
            self.assertEqual(result['open_pr_reviews'], [])
        self.assertEqual(self.record.read_bytes(), receipt)

    def test_upstream_matching_is_deferred_and_no_git_sync_is_repeated(self):
        path=self.repo/'test/api_supplement/torch/alias/test_torch_alias.py'
        path.parent.mkdir(parents=True);path.write_text('import unittest\n')
        self.git('add','test');self.git('commit','-m','upstream tests');self.git('push','upstream','master')
        before=self.rows();mapping=self.mapping.read_bytes()
        with patch.object(script.subprocess,'run',side_effect=AssertionError('Step 0.1 must not run Git operations')):
            result=self.reconcile(dict(self.remote,merged=False))
        self.assertTrue(result['complete']);self.assertEqual(result['upstream_test_overlaps'],[])
        self.assertEqual(self.rows(),before);self.assertEqual(self.mapping.read_bytes(),mapping)
        self.assertTrue(self.record.exists())



    def test_parent_merge_does_not_delete_child_branch_or_take_its_task(self):
        child='adapt-set-child'
        self.git('checkout',self.branch);self.git('checkout','-b',child)
        (self.repo/'child-implementation.py').write_text('child = True\n')
        self.git('add','child-implementation.py');self.git('commit','-m','child work');self.git('push','origin',child)
        self.mapping.write_text(json.dumps({'adapt-torch.foo':self.branch,'adapt-torch.other':child}))
        self.git('checkout','master');self.merge_upstream()
        self.assertTrue(self.reconcile()['complete'])
        self.assertEqual(json.loads(self.mapping.read_text())['nightly'],{'adapt-torch.other':child})
        self.assertTrue(self.git('branch','--list',child))

    def classification_material(self,review='已审核'):
        directory=self.root/'data/api-classification';directory.mkdir()
        ref=self.root/'references/api-classification/compute.md';ref.parent.mkdir(parents=True);ref.write_text('# Existing definitions\n')
        with closing(sqlite3.connect(self.db)) as c,c:
            c.execute('UPDATE "API支持数据" SET "细分场景"=? WHERE "API名称"=?',('新场景','torch.foo'))
        definition={'kind':'definition','level':'scenario','parent':['基础计算','基础计算'],
                    'name':'新场景','definition':'已批准的场景定义','apply_when':['适用条件'],
                    'do_not_apply_when':['边界'],'example_apis':['torch.foo'],
                    'why_existing_does_not_fit':'已有分类缺少该职责','review_status':review,
                    'reference_file':'references/api-classification/compute.md'}
        records=[definition,{'api':'torch.foo','category_reason':'foo'},
                 {'api':'torch.alias','category_reason':'alias'}, {'api':'torch.other','category_reason':'keep'}]
        path=directory/'classification-evidence.jsonl'
        path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')
        return path,ref

    def test_merge_syncs_approved_definition_and_removes_only_own_reasons(self):
        path,ref=self.classification_material();self.merge_upstream()
        self.assertTrue(self.reconcile()['complete'])
        text=ref.read_text(encoding='utf-8');self.assertIn('已批准的场景定义',text)
        remaining=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([r['api'] for r in remaining if 'api' in r],['torch.other'])
        before=ref.read_bytes();self.assertTrue(self.reconcile()['complete']);self.assertEqual(ref.read_bytes(),before)

    def test_pending_new_definition_keeps_reasons_database_and_branch(self):
        path,ref=self.classification_material('待审核');self.merge_upstream()
        before=self.rows();reasons=path.read_bytes();reference=ref.read_bytes()
        self.assertFalse(self.reconcile()['complete'])
        self.assertEqual(self.rows(),before);self.assertEqual(path.read_bytes(),reasons);self.assertEqual(ref.read_bytes(),reference)
        self.assertTrue(self.git('branch','--list',self.branch))

    def test_merge_without_new_definition_deletes_finished_api_reason_file(self):
        directory=self.root/'data/api-classification';directory.mkdir();path=directory/'classification-evidence.jsonl'
        path.write_text(json.dumps({'api':'torch.foo','category_reason':'finished'}),encoding='utf-8')
        self.merge_upstream();self.assertTrue(self.reconcile()['complete']);self.assertFalse(path.exists())

    def test_missing_api_status_blocks_cleanup(self):
        self.merge_upstream();self.adapt.write_text(json.dumps({'task-branches':['adapt-torch.foo']}))
        before=self.rows();result=self.reconcile()
        self.assertFalse(result['complete']);self.assertEqual(self.rows(),before)
        self.assertTrue(self.git('branch','--list',self.branch))

    def test_wrong_pr_identity_cannot_update_or_remove_records(self):
        self.merge_upstream();remote=copy.deepcopy(self.remote)
        remote['head']['repo']['full_name']='another/fork'
        before=self.rows();self.assertFalse(self.reconcile(remote)['complete'])
        self.assertEqual(self.rows(),before)
        self.assertTrue(self.git('branch','--list',self.branch))

    def test_support_result_does_not_resurrect_an_offline_api(self):
        self.merge_upstream()
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('UPDATE "API支持数据" SET "是否支持"=? WHERE "API名称"=?',('API删除，待下线','torch.foo'))
        self.assertTrue(self.reconcile()['complete'])
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT "是否支持" FROM "API支持数据" WHERE "API名称"="torch.foo"').fetchone()[0],'API删除，待下线')

    def test_newer_local_and_origin_commits_are_preserved(self):
        self.merge_upstream();self.git('checkout',self.branch)
        (self.repo/'extra.txt').write_text('unmerged')
        self.git('add','extra.txt');self.git('commit','-m','unmerged');self.git('push','origin',self.branch)
        before=self.git('show-ref');result=self.reconcile()
        self.assertTrue(result['complete']);self.assertEqual(self.git('show-ref'),before)
        self.assertEqual((self.repo/'extra.txt').read_text(),'unmerged')
        self.assertTrue(json.loads(self.record.read_text())['cleanup-completed'])

    def test_dirty_current_branch_does_not_block_record_cleanup(self):
        self.merge_upstream();self.git('checkout',self.branch);(self.repo/'untracked.txt').write_text('keep')
        before=self.git('status','--porcelain');result=self.reconcile()
        self.assertTrue(result['complete']);self.assertEqual(self.git('status','--porcelain'),before)
        self.assertEqual(self.git('branch','--show-current'),self.branch)
        self.assertEqual((self.repo/'untracked.txt').read_text(),'keep')
        self.assertTrue(json.loads(self.record.read_text())['cleanup-completed'])

    def test_pending_adaptation_status_backfills_strategy_only(self):
        self.merge_upstream();self.adapt.write_text(json.dumps({'api-status':{'torch.foo':'待适配'}}))
        self.assertTrue(self.reconcile()['complete'])
        with closing(sqlite3.connect(self.db)) as c, c:
            self.assertEqual(c.execute('SELECT "是否支持","适配策略" FROM "API支持数据" WHERE "API名称"="torch.foo"').fetchone(),('未提供','待适配'))

    def test_other_tasks_on_same_branch_keep_their_records(self):
        self.merge_upstream();self.mapping.write_text(json.dumps({'adapt-torch.foo':self.branch,'adapt-torch.other':self.branch}))
        self.assertTrue(self.reconcile()['complete'])
        self.assertEqual(json.loads(self.mapping.read_text())['nightly'],{'adapt-torch.other':self.branch})
        self.assertTrue(self.git('branch','--list',self.branch))

    def test_merge_sync_uses_pr_snapshot_without_adapt_file_or_origin_access(self):
        self.merge_upstream();record=json.loads(self.record.read_text());record['api-status']={'torch.foo':'支持'}
        self.record.write_text(json.dumps(record));self.adapt.unlink();self.git('remote','remove','origin')
        self.assertTrue(self.reconcile()['complete'])
        self.assertTrue(json.loads(self.record.read_text())['cleanup-completed'])
        self.assertTrue(self.git('branch','--list',self.branch))

    def test_partial_record_cleanup_failure_retains_pr_tracking_for_retry(self):
        self.merge_upstream();original=script.write_json
        def fail_mapping(path,value):
            if path==self.mapping:raise OSError('simulated mapping write failure')
            return original(path,value)
        with patch.object(script,'write_json',side_effect=fail_mapping):
            self.assertFalse(self.reconcile()['complete'])
        self.assertIn('adapt-torch.foo',json.loads(self.mapping.read_text()))
        index = self.root/'data/pr-tracking.json'
        self.assertEqual(json.loads(index.read_text())[0]['pr_status'], 'merged_pending')
        with self.assertRaisesRegex(ValueError, 'already merged'):
            pr_tracking.register_receipt(self.run, json.loads(self.record.read_text()))
        self.assertTrue(self.record.exists());self.assertTrue(self.git('branch','--list',self.branch))
        self.assertTrue(self.reconcile()['complete']);self.assertTrue(json.loads(self.record.read_text())['cleanup-completed'])
        self.assertEqual(json.loads(index.read_text())[0]['pr_status'], 'merged')


if __name__ == '__main__':
    unittest.main()
