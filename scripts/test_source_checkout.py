"""Step 0 downloads sources; the source switcher only switches the existing repository."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import fetch_latest_sources as fetch
import fetch_latest_sources as source


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.source-test-', dir=source.DATA/'run_data')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin, self.cache = self.root/'upstream', self.root/'cache'
        self.data = self.root/'data'
        self.log = self.root/'test.log'
        self.call('git','init','-b','master',self.origin)
        (self.origin/'torch_npu').mkdir()
        (self.origin/'torch_npu/__init__.py').write_text('# fixture\n')
        self.first = self.commit('first')
        self.latest = self.commit('latest')

    def call(self, *args):
        result = subprocess.run([str(x) for x in args], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    def commit(self, value, repo=None):
        repo = repo or self.origin
        (repo/'change.txt').write_text(value)
        self.call('git','-C',repo,'add','.')
        self.call('git','-C',repo,'-c','user.name=Fixture','-c','user.email=test@example.invalid','commit','-m',value)
        return self.call('git','-C',repo,'rev-parse','HEAD')

    def clone(self):
        self.call('git','clone','--single-branch','--branch','master',self.origin.as_uri(),self.cache)

    def prepare(self):
        return source.switch_torchnpu_version(self.cache,'1.0.0',self.first,None,self.first,self.log)

    def test_step0_clones_missing_repository_then_updates_same_directory_after_switch(self):
        result = fetch.refresh_torchnpu(self.origin.as_uri(),self.log,data_root=self.data)
        repo = self.data/'source_code/torch_npu'
        self.assertTrue((repo/'.git').exists())
        self.assertEqual(result['main_branch'],'master')
        self.assertEqual(result['commit'],self.latest)
        source.switch_torchnpu_version(repo,'1.0.0',self.first,None,self.first,self.log)
        newer = self.commit('advance master')
        updated = fetch.refresh_torchnpu(self.origin.as_uri(),self.log,data_root=self.data)
        self.assertEqual(updated['path'],result['path'])
        self.assertEqual(self.call('git','-C',repo,'rev-parse','HEAD'),newer)
        self.assertEqual(self.call('git','-C',repo,'branch','--show-current'),'master')

    def test_source_switch_never_clones_missing_repository(self):
        with patch.object(source,'command',side_effect=AssertionError('must not run clone')):
            with self.assertRaisesRegex(ValueError,'repository is missing'):
                self.prepare()
        self.assertFalse(self.cache.exists())

    def test_local_switch_works_with_remote_offline_and_does_not_update_master(self):
        self.clone()
        self.commit('new remote commit')
        self.origin.rename(self.root/'offline-upstream')
        with patch.object(source,'command',wraps=source.command) as commands:
            result = self.prepare()
        self.assertEqual(result['commit'],self.first)
        self.assertEqual(self.call('git','-C',self.cache,'rev-parse','master'),self.latest)
        self.assertEqual(self.call('git','-C',self.cache,'rev-parse','HEAD'),self.first)
        forbidden={'clone','fetch','pull','ls-remote','merge'}
        self.assertTrue(all(not forbidden.intersection(map(str,call.args[0])) for call in commands.call_args_list))

    def test_dirty_worktree_is_preserved(self):
        self.clone();self.prepare()
        work=self.cache/'user-work.txt';work.write_text('keep')
        with self.assertRaisesRegex(ValueError,'uncommitted'):
            self.prepare()
        self.assertEqual(work.read_text(),'keep')
        self.assertEqual(self.call('git','-C',self.cache,'rev-parse','HEAD'),self.first)

    def test_switch_from_adaptation_branch_returns_to_master_before_wheel_commit(self):
        self.clone()
        self.call('git','-C',self.cache,'checkout','-b','adapt-torch.add')
        adapted=self.commit('adaptation work',self.cache)
        with patch.object(source,'command',wraps=source.command) as commands:
            result=self.prepare()
        checkouts=[list(map(str,call.args[0]))[3:] for call in commands.call_args_list
                   if len(call.args[0])>3 and call.args[0][3]=='checkout']
        self.assertEqual(checkouts,[['checkout','master'],['checkout','--detach',self.first]])
        self.assertEqual(result['commit'],self.first)
        self.assertEqual(self.call('git','-C',self.cache,'rev-parse','HEAD'),self.first)
        self.assertEqual(self.call('git','-C',self.cache,'rev-parse','adapt-torch.add'),adapted)

    def test_local_branch_commits_remain_unchanged(self):
        self.clone()
        local=self.commit('local branch commit',self.cache)
        self.prepare()
        self.assertEqual(self.call('git','-C',self.cache,'rev-parse','master'),local)
        self.assertEqual(self.call('git','-C',self.cache,'rev-parse','HEAD'),self.first)

    def test_conflicting_wheel_identity_is_rejected_before_checkout(self):
        self.clone()
        with self.assertRaisesRegex(ValueError,'does not match'):
            source.switch_torchnpu_version(self.cache,'1.0.0',self.first,None,self.latest,self.log)
        self.assertEqual(self.call('git','-C',self.cache,'rev-parse','HEAD'),self.latest)

    def test_exact_existing_local_tag_when_wheel_has_no_commit(self):
        self.call('git','-C',self.origin,'tag','v1.0.0',self.first)
        self.clone()
        result=source.switch_torchnpu_version(self.cache,'1.0.0',None,None,None,self.log)
        self.assertEqual(result['commit'],self.first)

    def test_missing_commit_is_rejected_without_fetch_or_checkout(self):
        self.clone()
        with patch.object(source,'command',wraps=source.command) as commands:
            with self.assertRaisesRegex(ValueError,'missing locally'):
                source.switch_torchnpu_version(self.cache,'1.0.0','c'*40,None,'c'*40,self.log)
        self.assertTrue(all('fetch' not in call.args[0] for call in commands.call_args_list))
        self.assertEqual(self.call('git','-C',self.cache,'rev-parse','HEAD'),self.latest)

    def test_step0_downloads_wheel_commit_outside_main_history_before_local_switch(self):
        self.call('git','-C',self.origin,'checkout','-b','wheel-build')
        wheel_commit=self.commit('wheel branch only')
        self.call('git','-C',self.origin,'checkout','master')
        fetch.refresh_torchnpu(self.origin.as_uri(),self.log,data_root=self.data)
        repo=self.data/'source_code/torch_npu'
        probe=subprocess.run(['git','-C',str(repo),'cat-file','-e',wheel_commit+'^{commit}'],capture_output=True)
        self.assertNotEqual(probe.returncode,0)
        fetch.ensure_torchnpu_commit(wheel_commit,self.log,data_root=self.data)
        self.assertEqual(self.call('git','-C',repo,'rev-parse','HEAD'),self.latest)
        result=source.switch_torchnpu_version(repo,'1.0.0',wheel_commit,None,wheel_commit,self.log)
        self.assertEqual(result['commit'],wheel_commit)

    def test_preparation_switches_source_and_writes_relative_result_path(self):
        fetch.refresh_torchnpu(self.origin.as_uri(),self.log,data_root=self.data)
        run=self.data/'run_data/adapt-torch.add-261109';run.mkdir(parents=True)
        record=run/'log/step0-source.json'
        with patch.object(source,'ROOT',self.root):
            result=source.complete_wheel_sources({'version':'1.0.0','commit':self.first},
                self.log,data_root=self.data,result_path=record)
        self.assertEqual(result,json.loads(record.read_text()))
        self.assertEqual(result['status'],'SOURCE_READY')
        self.assertEqual(result['path'],'data/source_code/torch_npu')
        self.assertEqual(result['commit'],self.first)
        self.assertEqual(self.call('git','-C',self.data/'source_code/torch_npu','rev-parse','HEAD'),self.first)


if __name__=='__main__':
    unittest.main(verbosity=2)
