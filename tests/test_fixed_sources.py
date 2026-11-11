"""Fixed-directory replacement and mainline pull checks using isolated local data."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sys
import tarfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import fetch_latest_sources as sources
import fetch_nightly_wheels as fetch
import install_env as installer
import test_prepare_api_inputs as fixtures


class FixedSourcesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PreparationTests.setUpClass()

    def setUp(self):
        self.f=fixtures.PreparationTests('runTest');self.f.setUp();self.addCleanup(self.f.tearDown)
        self.downloader,*_=self.f.fixtures()
        self.provider=patch.object(fetch,'load_provider',return_value=(self.downloader,self.f.identity,self.f.selector))
        self.provider.start();self.addCleanup(self.provider.stop)
        _,self.selection=fetch.resolve(self.f.config)
        self.source_url=self.selection['pytorch_source']['url']

    def old_tree(self):
        target=self.f.data/'source_code/pytorch';target.mkdir(parents=True)
        (target/'old-file.txt').write_text('old sources')
        return target

    def replace(self):
        return sources.replace_pytorch(self.f.config,self.selection,self.downloader,self.f.log,data_root=self.f.data)

    def test_replacement_removes_old_files_and_keeps_safe_symlinks(self):
        target=self.old_tree()
        result=self.replace()
        self.assertEqual(Path(result['path']),target)
        self.assertFalse((target/'old-file.txt').exists())
        self.assertTrue((target/'torch/__init__.py').is_file())
        self.assertTrue((target/'.dockerignore').is_symlink())
        self.assertFalse((self.f.data/'source_code/latest').exists())

    def test_download_failure_keeps_existing_source(self):
        target=self.old_tree()
        with patch.object(self.downloader,'download',side_effect=RuntimeError('connection lost')):
            with self.assertRaisesRegex(RuntimeError,'connection lost'):self.replace()
        self.assertEqual((target/'old-file.txt').read_text(),'old sources')

    def test_archive_traversal_is_rejected_before_old_source_deletion(self):
        target=self.old_tree()
        archive=self.downloader.files[self.source_url]
        with tarfile.open(archive,'w:gz') as tar:
            info=tarfile.TarInfo('pytorch/../../escape.txt');info.size=1;tar.addfile(info,io.BytesIO(b'x'))
        with self.assertRaisesRegex(ValueError,'Unsafe'):self.replace()
        self.assertTrue((target/'old-file.txt').is_file())
        self.assertFalse((self.f.data/'escape.txt').exists())

    def test_symlink_replacement_target_is_rejected(self):
        cache=self.f.data/'source_code';cache.mkdir()
        protected=self.f.root/'protected';protected.mkdir();(protected/'keep').write_text('keep')
        (cache/'pytorch').symlink_to(protected,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'inside data/source_code'):self.replace()
        self.assertEqual((protected/'keep').read_text(),'keep')

    def test_newer_wheels_without_matching_source_are_not_selected(self):
        newer=self.f.urls('c'*10)
        self.downloader.listing.update(self.f.listing(newer))
        self.downloader.listing[newer[0]]['last_modified']='2026-11-09T01:00:00Z'
        self.downloader.listing[newer[1]]['last_modified']='2026-11-09T01:00:00Z'
        _,selection=fetch.resolve(self.f.config)
        self.assertEqual(selection['pytorch_source']['url'],self.source_url)
        del self.downloader.listing[self.source_url]
        with self.assertRaisesRegex(ValueError,'No complete'):fetch.resolve(self.f.config)

    def invoke(self,path):
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            return fetch.main(['--basic-information',str(path),'--data-root',str(self.f.data)])

    def test_repeated_fetch_replaces_pytorch_and_pulls_torchnpu_in_place(self):
        path=self.f.seed();self.assertEqual(self.invoke(path),0)
        before=json.loads(path.read_text())
        (self.f.root/before['pytorch_source']/'obsolete.txt').write_text('remove')
        new_commit=self.f.commit(self.f.root/'npu-origin','new mainline')
        self.assertEqual(self.invoke(path),0)
        after=json.loads(path.read_text())
        self.assertEqual(after['pytorch_source'],before['pytorch_source'])
        self.assertEqual(after['torch_npu_latest_source'],before['torch_npu_latest_source'])
        self.assertFalse((self.f.root/after['pytorch_source']/'obsolete.txt').exists())
        self.assertEqual(after['torch_npu_main_commit'],new_commit)
        self.assertEqual(self.f.git('-C',self.f.root/after['torch_npu_latest_source'],'rev-parse','HEAD'),after['torch_npu_commit'])
        self.assertEqual(after['pytorch_wheel'],before['pytorch_wheel'])
        self.assertEqual(after['preparation_status'],'INPUTS_READY')

    def test_failed_checkout_does_not_publish_ready_inputs(self):
        path=self.f.seed()
        with patch.object(sources,'switch_torchnpu_version',side_effect=ValueError('checkout failed')):
            self.assertEqual(self.invoke(path),1)
        self.assertEqual(json.loads(path.read_text())['preparation_status'],'FAILED')
        self.assertEqual(json.loads((path.parent/'log/step0-preparation.json').read_text())['status'],'FAILED')
        self.assertEqual(json.loads((path.parent/'log/step0-source.json').read_text())['status'],'FAILED')

    def test_failed_pull_marks_task_unready_for_installation(self):
        path=self.f.seed();self.assertEqual(self.invoke(path),0)
        basic=json.loads(path.read_text());repo=self.f.root/basic['torch_npu_latest_source']
        (repo/'local-work.txt').write_text('keep me')
        self.assertEqual(self.invoke(path),1)
        self.assertEqual(json.loads(path.read_text())['preparation_status'],'FAILED')
        self.assertEqual((repo/'local-work.txt').read_text(),'keep me')
        errors=io.StringIO()
        with patch.object(installer,'DATA',self.f.data),patch.object(sys,'argv',['install_env.py','--run-dir',str(path.parent),'--dry-run']),redirect_stderr(errors):
            self.assertEqual(installer.main(),1)
        self.assertIn('incomplete',errors.getvalue())


if __name__=='__main__':unittest.main(verbosity=2)
