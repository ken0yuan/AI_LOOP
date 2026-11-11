"""Independent wheel fetcher checks; never contacts OBS or an external project."""
from contextlib import redirect_stdout, redirect_stderr
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import fetch_nightly_wheels as fetch
from obs_downloader import ObsutilDownloader, ObsDownloadError
import test_prepare_api_inputs as fixtures
from wheel_artifacts import wheel_identity


class StandaloneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PreparationTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.PreparationTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_defaults_and_provider_are_owned_locally(self):
        config = dict(self.fixture.config)
        config['obs_prefix'] = 'obs://pytorch-ci-cache/7day-cache/'
        self.assertNotIn('infrastructure_root', config)
        self.assertNotIn('infrastructure_settings', config)
        with patch.dict(sys.modules, {'run_test': None, 'run_test.obs': None, 'run_test.artifacts': None}):
            downloader, identity, selector = fetch.load_provider(config)
        self.assertEqual(downloader.__class__.__module__, 'obs_downloader')
        self.assertEqual(identity.__module__, 'wheel_artifacts')
        self.assertEqual(selector.__module__, 'wheel_artifacts')

    def test_pairing_follows_changed_commit_image_and_recipe(self):
        f = self.fixture
        old = f.urls()
        new = tuple(u.replace('a'*10, 'c'*12).replace('-fixture-', '-batch-209912312359-').replace('-v3.', '-v27.') for u in old)
        listing = f.listing(old, new)
        selected = fetch.select_latest_pair(listing, f.config, wheel_identity, importlib.import_module('wheel_artifacts').select_bundle)
        self.assertEqual(selected['bundle']['wheels'], new[0])
        self.assertEqual(selected['bundle']['wheels_npu'], new[1])

    def test_unversioned_standalone_npu_and_unknown_names_rejected(self):
        self.assertIsNone(wheel_identity(self.fixture.urls()[1].replace('-v3.tar.gz', '.tar.gz')))
        self.assertIsNone(wheel_identity('obs://fixture/7day-cache/nightly-unknown.tar.gz'))

    def test_combined_bundle_still_supported(self):
        f = self.fixture
        pair = f.urls()
        combined = pair[0].replace('wheels-pytorch-'+'a'*10, 'wheels-'+'a'*10+'-npu-'+'b'*10)
        selection = fetch.select_latest_pair(f.listing((combined,)), f.config, wheel_identity, importlib.import_module('wheel_artifacts').select_bundle)
        self.assertEqual(selection['bundle'], {'wheels':combined, 'wheels_npu':None})

    def test_dry_run_has_no_sources_or_runtime_side_effects(self):
        f = self.fixture
        downloader, *_ = f.fixtures()
        config = f.root/'defaults.json'
        config.write_text(json.dumps(f.config))
        provider = (downloader, wheel_identity, importlib.import_module('wheel_artifacts').select_bundle)
        with patch.object(fetch, 'load_provider', return_value=provider), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(fetch.main(['--defaults',str(config),'--data-root',str(f.data),'--dry-run']),0)
        self.assertEqual(downloader.downloads, [])
        self.assertFalse((f.data/'wheels').exists())
        self.assertFalse((f.data/'source_code').exists())
        self.assertFalse((f.data/'run_data').exists())

    def test_standalone_produces_only_flat_wheel_files(self):
        f = self.fixture
        downloader, *_ = f.fixtures()
        config = f.root/'defaults.json'
        config.write_text(json.dumps(f.config))
        provider = (downloader, wheel_identity, importlib.import_module('wheel_artifacts').select_bundle)
        with patch.object(fetch, 'load_provider', return_value=provider), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            argv=['--defaults',str(config),'--data-root',str(f.data)]
            self.assertEqual(fetch.main(argv),0)
            self.assertEqual(fetch.main(argv),0)
        self.assertEqual(len(downloader.downloads),5)
        folder=f.data/'wheels'
        self.assertEqual(len(list(folder.glob('*.whl'))),2)
        self.assertTrue(all(p.is_file() and p.suffix=='.whl' for p in folder.iterdir()))
        self.assertTrue((f.data/'source_code').is_dir())
        self.assertFalse((f.data/'run_data').exists())

    def test_prefix_bucket_mismatch_fails_without_network(self):
        config=dict(self.fixture.config)
        with self.assertRaisesRegex(ValueError,'same bucket'):
            fetch.load_provider(config)


    def test_legacy_external_configuration_is_rejected(self):
        config = dict(self.fixture.config)
        config['obs_prefix'] = 'obs://pytorch-ci-cache/7day-cache/'
        config['infrastructure_settings'] = '/nonexistent/old/settings.json'
        with self.assertRaisesRegex(ValueError, 'Legacy infrastructure'):
            fetch.load_provider(config)

    def test_full_listing_limit_cannot_silently_select_an_old_bundle(self):
        output = '\n'.join(f'obs://fixture/7day-cache/object-{i}\n 2026-11-09T01:00:00Z 1KB Standard "etag"' for i in range(10000))
        downloader = ObsutilDownloader('/nonexistent/fixture-only-obsutil')
        with patch.object(downloader, '_run', return_value=subprocess.CompletedProcess([], 0, stdout=output)):
            with self.assertRaisesRegex(ObsDownloadError, 'truncated snapshot'):
                downloader.list('obs://fixture/7day-cache/')


if __name__=='__main__':
    unittest.main(verbosity=2)
