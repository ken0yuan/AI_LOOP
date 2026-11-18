"""Task dependency merging before environment installation."""
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

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

    def test_release_and_nightly_checks_use_the_published_version(self):
        published = io.BytesIO(b'{"info":{"name":"torch","version":"2.14.0"}}')
        with patch.object(prepare, 'urlopen', return_value=published) as get:
            self.assertFalse(prepare.nightly_target('2.14'))
            self.assertEqual([call.args[0].full_url for call in get.call_args_list],
                             ['https://pypi.org/pypi/torch/2.14.0/json'])
        for target in ('2.16', '2.13', '9.99'):
            missing = HTTPError('fixture', 404, 'missing', {}, None)
            main = io.BytesIO(b'# Sphinx inventory version 2\n# Project: PyTorch\n# Version: main (2.16.0a0)\n')
            with self.subTest(target=target), patch.object(prepare, 'urlopen', side_effect=[missing, main]):
                if target == '2.16':
                    self.assertTrue(prepare.nightly_target(target))
                else:
                    with self.assertRaisesRegex(ValueError, 'absent from PyPI'):
                        prepare.nightly_target(target)
        with patch.object(prepare, 'urlopen', side_effect=OSError('timeout')) as get:
            with self.assertRaisesRegex(ValueError, 'Cannot verify'):
                prepare.nightly_target('2.14')
            get.assert_called_once()

    def test_patch_targets_are_rejected_before_network_and_publication(self):
        for args in (['--torch-version','2.14.0'], ['--torch-version','2.14.1'],
                     ['--requirement','torch==2.14.1']):
            with self.subTest(args=args), patch.object(prepare, 'urlopen') as get, redirect_stderr(io.StringIO()) as err:
                self.assertEqual(prepare.main(['--api','torch.abs','--run-dir',str(self.f.run_dir),*args]), 1)
                self.assertIn('major.minor', err.getvalue())
                get.assert_not_called()
                self.assertFalse(self.f.run_dir.exists())

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
