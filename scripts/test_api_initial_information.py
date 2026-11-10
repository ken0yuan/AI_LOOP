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
        self.assertNotIn('source', basic)
        self.assertNotIn('api_names', basic)
        self.assertNotIn('simulated', basic)
        record = json.loads((task.parent/'log/step0-preparation.json').read_text())
        self.assertEqual((self.f.root/record['npu_smi_log']).read_text(), self.f.runtime_mock.return_value['npu_smi_output'])

    def invoke(self, run_dir):
        config = self.f.root/'defaults.json'
        config.write_text(json.dumps(self.f.config))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return prepare.main(['--api', 'torch.Tensor.add', '--run-dir', str(run_dir), '--defaults', str(config)])

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
        report = '| npu-smi 26.1.1                 Version: 26.1.1 |\n'
        with patch.dict(os.environ, {'ASCEND_HOME_PATH':str(active), 'ASCEND_TOOLKIT_HOME':''}), \
                patch.object(prepare.platform, 'machine', return_value='aarch64'), \
                patch.object(prepare.subprocess, 'run', return_value=SimpleNamespace(stdout=report)) as command:
            result = collect_runtime_environment()
        self.assertEqual(command.call_args.args[0], ['npu-smi', 'info'])
        self.assertEqual(result['driver_version'], '26.1.1')
        self.assertEqual(result['cann_version'], '9.2.0')
        self.assertEqual(result['cann_version_file'], info)


if __name__ == '__main__':
    unittest.main(verbosity=2)
