"""Environment installation rejects incomplete preparation before selecting or modifying a venv."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import install_env as installer


class PreconditionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)/'data'
        self.run_dir = self.data/'run_data/adapt-torch.add-261109'
        (self.run_dir/'log').mkdir(parents=True)
        self.basic = self.run_dir/'basic_information.json'
        self.task = dict(preparation_status='INPUTS_READY', python_version='3.11.17',
                         pytorch_source='data/source_code/pytorch', pytorch_version='1.0.0',
                         pytorch_wheel='data/wheels/torch.whl', torch_npu_version='1.0.0',
                         torch_npu_wheel='data/wheels/torch_npu.whl')
        self.basic.write_text(json.dumps(self.task))
        (self.run_dir/'requirements.txt').write_text('pytest==8.3.0\n')
        self.record = self.run_dir/'log/step0-preparation.json'
        self.record.write_text(json.dumps({'status':'INPUTS_READY'}))
        self.source_record=self.run_dir/'log/step0-source.json'
        self.source_record.write_text(json.dumps({'status':'SOURCE_READY'}))

    def assert_stops_before_environment(self, message):
        errors = io.StringIO()
        with patch.object(installer, 'DATA', self.data), \
                patch.object(installer, 'find_python', side_effect=AssertionError('must stop before selecting Python')), \
                patch.object(sys, 'argv', ['install_env.py','--run-dir',str(self.run_dir)]), \
                redirect_stderr(errors):
            self.assertEqual(installer.main(), 1)
        self.assertIn(message, errors.getvalue())
        self.assertFalse((self.data/'venv').exists())
        self.assertEqual(json.loads(self.basic.read_text())['environment_status'],'FAILED')
        self.assertFalse(list((self.run_dir/'log').glob('*environment.json')))

    def test_missing_requirements_is_rejected_before_interpreter_selection(self):
        (self.run_dir/'requirements.txt').unlink()
        self.assert_stops_before_environment('requires an independent task requirements.txt')

    def test_missing_preparation_record_is_rejected_even_when_basic_is_ready(self):
        self.record.unlink()
        self.assert_stops_before_environment('requires the Step 0 preparation record')

    def test_inconsistent_preparation_status_is_rejected(self):
        self.record.write_text(json.dumps({'status':'FAILED'}))
        self.assert_stops_before_environment('preparation record is incomplete')

    def test_missing_source_checkout_is_rejected_before_installation(self):
        self.source_record.unlink()
        self.assert_stops_before_environment('source checkout record is missing')

    def test_failed_source_checkout_is_rejected_before_installation(self):
        self.source_record.write_text(json.dumps({'status':'FAILED'}))
        self.assert_stops_before_environment('source checkout is incomplete')

    def test_ready_status_does_not_hide_missing_required_field(self):
        self.task.pop('torch_npu_wheel')
        self.basic.write_text(json.dumps(self.task))
        self.assert_stops_before_environment('torch_npu_wheel')


if __name__ == '__main__':
    unittest.main(verbosity=2)
