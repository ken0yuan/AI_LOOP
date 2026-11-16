from contextlib import redirect_stdout
from datetime import datetime, timezone
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_nightly_availability as health
from obs_downloader import ObsDownloadError
from wheel_artifacts import select_bundle, wheel_identity


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.config = {'python_version': '3.11.17', 'chip_type': 'Ascend910B2',
                       'os': 'ubuntu22.04', 'architecture': 'aarch64', 'obs_prefix': 'obs://fixture/7day-cache/',
                       'wheel_profiles': [{'chip_type': 'Ascend910B2', 'os': 'ubuntu22.04',
                                           'architecture': 'aarch64', 'wheel_name_contains': ['-torch-npu-builder-']}]}
        self.now = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)
        suffix = '-py311-torch-npu-builder-aarch64-test-aarch64-v3.tar.gz'
        prefix = self.config['obs_prefix']
        self.pt = prefix + 'wheels-pytorch-' + 'a' * 10 + suffix
        self.npu = prefix + 'wheels-torchnpu-' + 'b' * 10 + '-' + 'a' * 10 + suffix
        self.source = prefix + 'pytorch-source-' + 'a' * 10 + '.tar.gz'
        self.listing = {u: {'last_modified': '2026-11-11T19:00:00Z', 'etag': u} for u in [self.pt, self.npu, self.source]}

    def run_check(self, previous=None, failure=None):
        class Downloader:
            def list(inner, prefix):
                if failure:
                    raise failure
                return self.listing
        with patch.object(health.fetch, 'load_provider', return_value=(Downloader(), wheel_identity, select_bundle)):
            return health.check(self.config, previous, now=self.now)

    def test_unchanged_old_wheels_are_not_mistaken_for_new_source_builds(self):
        initial = self.run_check()
        self.listing[self.config['obs_prefix'] + 'pytorch-source-' + 'c' * 10 + '.tar.gz'] = {
            'last_modified': '2026-11-16T03:00:00Z', 'etag': 'new-source'}
        result = self.run_check(initial['selection'])
        self.assertEqual(result['status'], 'NO_NEW_BUILD')
        self.assertTrue(result['stale'])
        self.assertFalse(result['newer_than_task'])
        self.assertEqual(result['newest_source']['etag'], 'new-source')

    def test_reupload_with_new_etag_is_detected_despite_unchanged_names(self):
        previous = self.run_check()['selection']
        self.listing = copy.deepcopy(self.listing)
        self.listing[self.npu].update(etag='new-npu-build', last_modified='2026-11-16T03:00:00Z')
        result = self.run_check(previous)
        self.assertEqual(result['status'], 'NEWER_AVAILABLE')
        self.assertFalse(result['stale'])

    def test_older_available_bundle_cannot_be_reported_as_newer(self):
        previous = self.run_check()['selection']
        previous = copy.deepcopy(previous)
        previous['objects'][self.npu].update(etag='newer-task-build', last_modified='2026-11-16T03:00:00Z')
        result = self.run_check(previous)
        self.assertEqual(result['status'], 'DIFFERENT_AVAILABLE')
        self.assertFalse(result['newer_than_task'])

    def test_missing_source_or_npu_member_is_not_a_complete_bundle(self):
        del self.listing[self.source]
        result = self.run_check()
        self.assertEqual(result['status'], 'NO_COMPLETE_BUNDLE')
        self.assertEqual(result['complete_pairs_missing_source'], ['a' * 10])
        self.listing[self.source] = {'last_modified': '2026-11-11T19:00:00Z', 'etag': 'source'}
        del self.listing[self.npu]
        self.assertEqual(self.run_check()['status'], 'NO_COMPLETE_BUNDLE')

    def test_network_failure_does_not_claim_no_new_build(self):
        result = self.run_check(failure=ObsDownloadError('fixture timeout'))
        self.assertEqual(result['status'], 'QUERY_FAILED')
        self.assertNotIn('newer_than_task', result)

    def test_check_keeps_ready_task_and_preparation_record_byte_identical(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root / 'data/run_data/task'
            (run / 'log').mkdir(parents=True)
            basic = run / 'basic_information.json'
            task = {key: self.config[key] for key in health.task_state.ENVIRONMENT}
            task.update(preparation_config=self.config, environment_status='ENV_READY',
                        preparation_status='INPUTS_READY', python='existing/python')
            basic.write_text(json.dumps(task), encoding='utf-8')
            record = run / 'log/step0-preparation.json'
            record.write_text(json.dumps({'selection': self.run_check()['selection']}), encoding='utf-8')
            before = [basic.read_bytes(), record.read_bytes()]
            with patch.object(health, 'check', return_value={'status': 'NO_NEW_BUILD'}), redirect_stdout(io.StringIO()):
                self.assertEqual(health.main(['--basic-information', str(basic), '--data-root', str(root / 'data')]), 0)
                with self.assertRaisesRegex(ValueError, 'must not overwrite'):
                    health.main(['--basic-information', str(basic), '--data-root', str(root / 'data'), '--output', str(basic)])
            self.assertEqual(before, [basic.read_bytes(), record.read_bytes()])


if __name__ == '__main__':
    unittest.main()
