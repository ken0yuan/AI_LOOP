import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import preparation_state as state
from task_torchnpu_wheel import FIELD, task_wheel


class BatchWheelTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root/'data/run_data/test/basic_information.json'
        (self.path.parent/'log').mkdir(parents=True)
        basic = {'python_version': '3.11.0', 'chip_type': 'test', 'os': 'linux',
                 'architecture': 'aarch64', 'preparation_config': {'wheel_profiles': [
                     {'os': 'linux', 'architecture': 'aarch64', 'glibc': '2.28'}]}}
        self.base = self.root/'initial'/'torch_npu-2.0-py3-none-any.whl'
        self.base.parent.mkdir()
        self.wheel(self.base, 'original')
        self.initial = state.publish(self.path, basic, {'torch_npu_wheel': str(self.base)}, {}, root=self.root)

    def wheel(self, path, content, package='torch_npu'):
        with zipfile.ZipFile(path, 'w') as out:
            out.writestr(package+'-2.0.dist-info/METADATA', f'Name: {package}\nVersion: 2.0\n')
            out.writestr(package+'/implementation', content)

    def test_rebuilds_with_identical_names_remain_restorable(self):
        self.assertEqual(self.initial[FIELD], self.initial['torch_npu_wheel'])
        builds = self.root/self.base.name
        self.wheel(builds, 'first')
        first = task_wheel(self.path, builds, root=self.root)
        self.assertEqual(first.read_bytes(), builds.read_bytes())
        self.wheel(builds, 'second')
        second = task_wheel(self.path, builds, root=self.root)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first.read_bytes(), second.read_bytes())
        builds.unlink()
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['torch_npu_wheel'], self.initial['torch_npu_wheel'])
        refreshed = state.publish(self.path, saved, {'torch_npu_wheel': str(self.base)}, {}, root=self.root)
        self.assertEqual(refreshed[FIELD], saved[FIELD])
        self.assertEqual(task_wheel(self.path,root=self.root),second)
        second.unlink()
        with self.assertRaises(FileNotFoundError):
            task_wheel(self.path,root=self.root)

    def test_legacy_fallback_and_invalid_build_preserve_the_record(self):
        self.initial.pop(FIELD)
        state.write_json(self.path,self.initial)
        self.assertEqual(task_wheel(self.path,root=self.root),self.base)
        wrong=self.root/'torch-2.0-py3-none-any.whl'
        self.wheel(wrong, 'wrong package', 'torch')
        with self.assertRaisesRegex(ValueError, 'Expected a torch-npu wheel'):
            task_wheel(self.path,wrong,root=self.root)
        self.assertEqual(json.loads(self.path.read_text()),self.initial)
