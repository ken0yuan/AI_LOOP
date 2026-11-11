"""Task dependency merging before environment installation."""
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
import unittest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import prepare_api_inputs as prepare
import test_prepare_api_inputs as fixtures


class TaskRequirementsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PreparationTests.setUpClass()

    def setUp(self):
        self.f = fixtures.PreparationTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def invoke(self, *requirements):
        config = self.f.root/'defaults.json'
        config.write_text(json.dumps(self.f.config))
        args = ['--api','torch.Tensor.add','--run-dir',str(self.f.run_dir),'--defaults',str(config)]
        for value in requirements:
            args += ['--requirement', value]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return prepare.main(args)

    def test_required_dependencies_replace_defaults_and_are_written_in_one_file(self):
        self.assertEqual(self.invoke('pytest==8.3.0', 'requests>=2.30'), 0)
        path = self.f.run_dir/'requirements.txt'
        result = path.read_text()
        self.assertIn('numpy==1.26.4', result)
        self.assertIn('pytest==8.3.0', result)
        self.assertIn('requests>=2.30', result)
        self.assertNotIn('pytest==8.4.2', result)
        self.assertEqual((self.f.root/'requirements-default.txt').read_bytes(), self.f.default_requirements)
        record = json.loads((self.f.run_dir/'log/step0-preparation.json').read_text())
        self.assertEqual(record['requirements_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_inactive_marker_does_not_remove_required_default_for_target_python(self):
        result = prepare.merge_requirements(b'pytest==8.4.2\n',
                 ['pytest==9.0; python_version >= "3.12"'], self.f.config).decode()
        self.assertIn('pytest==8.4.2', result)
        self.assertIn('pytest==9.0;', result)

    def test_package_names_are_normalized_when_replacing_default(self):
        result = prepare.merge_requirements(b'typing-extensions==4.16.0\n',
                 ['typing_extensions==4.15.0'], self.f.config).decode()
        self.assertNotIn('==4.16.0', result)
        self.assertIn('typing_extensions==4.15.0', result)

    def test_invalid_dependency_stops_before_collecting_environment_or_writing_inputs(self):
        self.assertEqual(self.invoke('pytest=>8'), 1)
        self.f.runtime_mock.assert_not_called()
        self.assertFalse(self.f.run_dir.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
