"""Initial API task contract and real environment collection error handling."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import prepare_api_inputs as prepare
import test_prepare_api_inputs as fixtures

collect_runtime_environment = prepare.collect_runtime_environment


class InitialInformationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PreparationTests.setUpClass()

    def setUp(self):
        self.f = fixtures.PreparationTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def test_initial_task_contains_named_problem_and_collected_versions(self):
        task = self.f.seed()
        basic = json.loads(task.read_text())
        self.assertEqual(basic['run_id'], 'adapt-torch.Tensor.add-261109')
        self.assertEqual(basic['problem-description'], 'API `torch.Tensor.add` needs adaptation')
        self.assertEqual(basic['cann_version'], '9.2.0')
        self.assertEqual(basic['driver_version'], '26.1.1')
        self.assertEqual(basic['chip_type'], 'Ascend910B4')
        self.assertNotIn('source', basic)
        self.assertNotIn('api_names', basic)
        self.assertNotIn('simulated', basic)
        record = json.loads((task.parent/'log/step0-preparation.json').read_text())
        self.assertEqual(record['apis'], ['torch.Tensor.add'])
        self.assertFalse(record['new_apis'])
        self.assertEqual((self.f.root/record['npu_smi_log']).read_text(), self.f.runtime_mock.return_value['npu_smi_output'])

    def invoke(self, run_dir, inputs=None):
        config = self.f.root/'defaults.json'
        config.write_text(json.dumps(self.f.config))
        inputs = ['--api', 'torch.Tensor.add'] if inputs is None else inputs
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return prepare.main([*inputs, '--run-dir', str(run_dir), '--defaults', str(config)])

    def test_api_batch_preserves_all_names_and_uses_first_name_for_run_id(self):
        apis = [f'torch.fixture_{index}' for index in range(128)]
        run_dir = self.f.data/'run_data'/f'adapt-{apis[0]}-261109'
        self.assertEqual(self.invoke(run_dir, ['--api', *apis]), 0)
        basic = json.loads((run_dir/'basic_information.json').read_text())
        record = json.loads((run_dir/'log/step0-preparation.json').read_text())
        self.assertEqual(basic['run_id'], 'adapt-torch.fixture_0-261109')
        self.assertEqual(basic['problem-description'],
                         'API ' + ' '.join(f'`{name}`' for name in apis) + ' needs adaptation')
        self.assertEqual(record['apis'], apis)
        self.assertFalse(record['new_apis'])
        self.assertNotIn('api_names', basic)
        self.assertEqual((run_dir/'requirements.txt').read_bytes(), self.f.default_requirements)

    def test_latest_api_mode_uses_fixed_problem_and_new_run_id(self):
        run_dir = self.f.data/'run_data'/'adapt-new-261109'
        self.assertEqual(self.invoke(run_dir, ['--new-apis']), 0)
        basic = json.loads((run_dir/'basic_information.json').read_text())
        record = json.loads((run_dir/'log/step0-preparation.json').read_text())
        self.assertEqual(basic['run_id'], 'adapt-new-261109')
        self.assertEqual(basic['problem-description'], 'New API needs adaptation')
        self.assertEqual(record['apis'], [])
        self.assertTrue(record['new_apis'])
        self.assertEqual(basic['preparation_status'], 'BASIC_READY')
        self.assertEqual((run_dir/'requirements.txt').read_bytes(), self.f.default_requirements)

    def test_explicit_short_api_name_is_accepted(self):
        run_dir = self.f.data/'run_data'/'adapt-view-261109'
        self.assertEqual(self.invoke(run_dir, ['--api', 'view']), 0)
        basic = json.loads((run_dir/'basic_information.json').read_text())
        self.assertEqual(basic['problem-description'], 'API `view` needs adaptation')

    def test_input_modes_require_exactly_one_choice(self):
        for inputs in ([], ['--api'], ['--api', 'torch.add', '--new-apis']):
            with self.subTest(inputs=inputs), self.assertRaises(SystemExit) as stopped:
                self.invoke(self.f.run_dir, inputs)
            self.assertEqual(stopped.exception.code, 2)
            self.assertFalse(self.f.run_dir.exists())
        self.f.runtime_mock.assert_not_called()

    def test_invalid_nonfirst_api_stops_before_environment_collection(self):
        run_dir = self.f.data/'run_data'/'adapt-torch.add-261109'
        for invalid in ('bad-name', 'torch..missing', 'torch.add()'):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.invoke(run_dir, ['--api', 'torch.add', invalid]), 1)
                self.assertFalse(run_dir.exists())
        self.f.runtime_mock.assert_not_called()

    def test_batch_run_id_must_match_first_api(self):
        run_dir = self.f.data/'run_data'/'adapt-torch.mul-261109'
        self.assertEqual(self.invoke(run_dir, ['--api', 'torch.add', 'torch.mul']), 1)
        self.assertFalse(run_dir.exists())
        self.f.runtime_mock.assert_not_called()

    def test_latest_api_mode_rejects_named_api_run_id_before_environment_collection(self):
        self.assertEqual(self.invoke(self.f.run_dir, ['--new-apis']), 1)
        self.assertFalse(self.f.run_dir.exists())
        self.f.runtime_mock.assert_not_called()

    def test_invalid_api_run_id_stops_before_environment_collection(self):
        for name in ('arbitrary-name', 'adapt-torch.add-261109', 'adapt-torch.Tensor.add-261332'):
            with self.subTest(name=name):
                run_dir = self.f.data/'run_data'/name
                self.assertEqual(self.invoke(run_dir), 1)
                self.assertFalse(run_dir.exists())
        self.f.runtime_mock.assert_not_called()

    def test_failed_environment_collection_does_not_publish_initial_inputs(self):
        self.f.runtime_mock.side_effect = subprocess.CalledProcessError(1, ['npu-smi', 'info'])
        self.assertEqual(self.invoke(self.f.run_dir), 1)
        self.assertFalse(self.f.run_dir.exists())

    def test_driver_is_read_from_npu_smi_and_cann_from_active_installation(self):
        active = self.f.root/'active-cann'
        info = active/'arm64-linux/ascend_toolkit_install.info'
        info.parent.mkdir(parents=True)
        info.write_text('package_name=Ascend-cann-toolkit\nversion=9.2.0\n')
        report = '| npu-smi 26.1.1                 Version: 26.1.1 |\n| 0     910B4 | OK |\n'
        with patch.dict(os.environ, {'ASCEND_HOME_PATH':str(active), 'ASCEND_TOOLKIT_HOME':''}), \
                patch.object(prepare.platform, 'machine', return_value='aarch64'), \
                patch.object(prepare.subprocess, 'run', return_value=SimpleNamespace(stdout=report)) as command:
            result = collect_runtime_environment()
        self.assertEqual(command.call_args.args[0], ['npu-smi', 'info'])
        self.assertEqual(result['driver_version'], '26.1.1')
        self.assertEqual(result['chip_type'], 'Ascend910B4')
        self.assertEqual(result['cann_version'], '9.2.0')
        self.assertEqual(result['cann_version_file'], info)

    def test_all_device_rows_are_read_and_normalized(self):
        report = '\n'.join('| %d     910B4 | OK |' % i for i in range(8))
        self.assertEqual(prepare.read_chip_type(report), 'Ascend910B4')
        self.assertEqual(prepare.read_chip_type('| 0 Ascend910b4 | OK |'), 'Ascend910B4')

    def test_missing_or_mixed_chip_models_are_not_guessed(self):
        for report in ('| NPU Name | Health |', '| 0 910B4 | OK |\n| 1 910B2 | OK |'):
            with self.subTest(report=report), self.assertRaises(ValueError):
                prepare.read_chip_type(report)

    def test_device_id_rows_are_not_chip_names(self):
        report = '| 0 910B4 | OK |\n| 0 0 | 0000:C1:00.0 |\n| 1 910B4 | OK |\n| 0 1 | 0000:C2:00.0 |'
        self.assertEqual(prepare.read_chip_type(report), 'Ascend910B4')

    def test_configured_chip_cannot_replace_detected_hardware(self):
        self.f.config['chip_type'] = 'Ascend910B2'
        task = self.f.seed()
        self.assertEqual(json.loads(task.read_text())['chip_type'], 'Ascend910B4')

    def test_explicit_chip_mismatch_does_not_publish_a_task(self):
        config = self.f.root/'defaults.json'
        config.write_text(json.dumps(self.f.config))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = prepare.main(['--api', 'torch.Tensor.add', '--run-dir', str(self.f.run_dir),
                                   '--defaults', str(config), '--chip-type', 'Ascend910B2'])
        self.assertEqual(result, 1)
        self.assertFalse(self.f.run_dir.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
