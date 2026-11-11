import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT / 'scripts' if (PROJECT / 'scripts').is_dir() else PROJECT / 'AI_LOOP/scripts'
sys.path.insert(0, str(SCRIPTS))
import api_refresh_runtime as runtime


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run'; self.run.mkdir()
        self.recovery = runtime.RefreshRecovery(self.run / 'basic_information.json', self.root / 'scripts',
                                                Path(sys.executable), self.root, 60, 'main')

    def artifacts(self, recovery):
        recovery.documents(lambda: {'torch.test'})
        (recovery.work / 'docs-api.json').write_text('extracted document')
        cache = recovery.work / 'docs-cache'
        cache.mkdir()
        (cache / 'page.html').write_text('cached webpage')
        (cache / 'missing.html.404').write_text('404')
        for name in ['wheel-api-audit.json', 'dependency-install.log', 'framework-constraints.txt']:
            (recovery.work / name).write_text('audit evidence')

    def test_success_deletes_docs_and_web_cache_but_keeps_small_checkpoint_and_evidence(self):
        with self.recovery as recovery:
            self.artifacts(recovery)
            recovery.complete({'torch.test': True})
            self.assertFalse((recovery.work / 'docs-api.json').exists())
            self.assertFalse((recovery.work / 'docs-cache').exists())
            self.assertTrue(json.loads(recovery.checkpoint.read_text())['completed'])
            for name in ['wheel-api-audit.json', 'dependency-install.log', 'framework-constraints.txt', '.refresh.lock']:
                self.assertTrue((recovery.work / name).is_file(), name)

    def test_success_removes_checkpoint_larger_than_one_mib(self):
        with self.recovery as recovery:
            self.artifacts(recovery)
            recovery.saved['padding'] = 'x' * (1024 * 1024)
            recovery.complete({'torch.test': True})
            self.assertFalse(recovery.checkpoint.exists())
            self.assertTrue((recovery.work / 'wheel-api-audit.json').is_file())

    def test_failure_keeps_documents_cache_and_unfinished_checkpoint(self):
        with self.assertRaisesRegex(RuntimeError, 'validation failed'):
            with self.recovery as recovery:
                self.artifacts(recovery)
                raise RuntimeError('validation failed')
        self.assertTrue((self.recovery.work / 'docs-api.json').exists())
        self.assertTrue((self.recovery.work / 'docs-cache/page.html').exists())
        self.assertFalse(json.loads(self.recovery.checkpoint.read_text())['completed'])

    def test_cleanup_failure_does_not_undo_success_or_remove_results(self):
        with self.recovery as recovery:
            self.artifacts(recovery)
            original = Path.unlink
            def denied(path, *args, **kwargs):
                if path == recovery.work / 'docs-api.json':
                    raise PermissionError('cache locked')
                return original(path, *args, **kwargs)
            with patch.object(Path, 'unlink', new=denied), self.assertLogs(runtime.__name__, level='WARNING'):
                recovery.complete({'torch.test': True})
            self.assertTrue(json.loads(recovery.checkpoint.read_text())['completed'])
            self.assertTrue((recovery.work / 'wheel-api-audit.json').is_file())

    def test_cleanup_path_resolution_failure_does_not_fail_the_refresh(self):
        with self.recovery as recovery:
            self.artifacts(recovery)
            original = Path.resolve
            def denied(path, *args, **kwargs):
                if path == recovery.work:
                    raise PermissionError('cannot resolve cache directory')
                return original(path, *args, **kwargs)
            with patch.object(Path, 'resolve', new=denied), self.assertLogs(runtime.__name__, level='WARNING'):
                recovery.complete({'torch.test': True})
            self.assertTrue(json.loads(recovery.checkpoint.read_text())['completed'])
            self.assertTrue((recovery.work / 'docs-api.json').is_file())

    @unittest.skipIf(os.name == 'nt', 'Symlink boundary checks run on the Linux deployment host')
    def test_cache_symlink_does_not_delete_its_external_target(self):
        outside = self.root / 'outside'; outside.mkdir()
        marker = outside / 'keep.html'; marker.write_text('must survive')
        with self.recovery as recovery:
            recovery.documents(lambda: {'torch.test'})
            (recovery.work / 'docs-cache').symlink_to(outside, target_is_directory=True)
            recovery.complete({'torch.test': True})
            self.assertFalse((recovery.work / 'docs-cache').is_symlink())
            self.assertEqual(marker.read_text(), 'must survive')

    @unittest.skipIf(os.name == 'nt', 'Symlink boundary checks run on the Linux deployment host')
    def test_redirected_work_directory_is_not_cleaned(self):
        outside = self.root / 'outside'; outside.mkdir()
        marker = outside / 'docs-api.json'; marker.write_text('must survive')
        work = self.recovery.work
        work.parent.mkdir(parents=True)
        work.symlink_to(outside, target_is_directory=True)
        with self.assertLogs(runtime.__name__, level='WARNING'):
            self.recovery._cleanup_completed()
        self.assertEqual(marker.read_text(), 'must survive')


if __name__ == '__main__':
    unittest.main(verbosity=2)
