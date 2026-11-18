"""Fixed-release selection and the Step 0/1 handoff, without network access."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import preparation_state as state
import prepare_fixed_wheels as fixed
from pip._vendor.packaging.tags import Tag
from pip._vendor.packaging.version import Version


class FixedPreparationTests(unittest.TestCase):
    def test_active_exact_pin_routes_before_nightly_discovery(self):
        basic = {'python_version':'3.11.17', 'architecture':'aarch64'}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root/'basic_information.json'
            (root/'requirements.txt').write_text('-r more.txt\n')
            (root/'more.txt').write_text('torch==2.15; python_version == "3.11"\ntorch==2.14; python_version < "3.11"\n')
            self.assertEqual(state.fixed_torch_release(path,basic),Version('2.15.0'))
            with self.assertRaisesRegex(ValueError,'prepare_fixed_wheels.py'):
                state.require_nightly_task(path,basic)
            (root/'more.txt').write_text('pytest==8.4.2\n')
            self.assertIsNone(state.fixed_torch_release(path,basic))

    def test_fixed_handoff_requires_both_sources_and_npu_checkout(self):
        task = {key:'value' for key in state.WHEEL_FIELDS}
        task.update(torch_npu_commit='wheel commit', torch_npu_latest_source='tree',torch_npu_main_commit='main')
        self.assertEqual(state.state(task, fixed_release=True), 'WHEELS_READY')
        task.update(pytorch_source='release tree')
        self.assertEqual(state.state(task), 'WHEELS_READY')
        self.assertEqual(state.state(task, fixed_release=True),'INPUTS_READY')
        task.pop('torch_npu_commit')
        self.assertNotEqual(state.state(task, fixed_release=True),'INPUTS_READY')

    def test_fixed_prepare_uses_release_directory_without_source_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root/'data'
            path = data/'run_data/example/basic_information.json'
            path.parent.mkdir(parents=True)
            state.write_json(path, {'python_version':'3.11.17','architecture':'aarch64','os':'linux',
                                    'chip_type':'fixture','pytorch_source_commit':'old', 'preparation_config':{'torch_npu_repo':'fixture',
                                    'wheel_profiles':[{'architecture':'aarch64','os':'linux'}]}})
            (path.parent/'requirements.txt').write_text('torch==2.15\n')
            source = data/'source_code/pytorch-2.15.0'
            source.mkdir(parents=True)
            wheels = {n:{'version':'2.15.0+cpu' if n=='torch' else '2.15.0.dev1',
                         'commit':'b'*40,'path':str(data/'wheels'/(n+'.whl'))} for n in ('torch','torch-npu')}
            npu = {'path':str(data/'source_code/torch_npu'),'commit':'c'*40}
            with (patch.object(fixed,'DATA',data), patch.object(fixed,'ROOT',root),
                  patch.object(fixed.framework,'ROOT',root),
                  patch.object(fixed,'ensure_environment',return_value=(data/'venv',{},None,wheels)),
                  patch.object(fixed.sources,'refresh_torchnpu',return_value=npu),
                  patch.object(fixed.sources,'complete_wheel_sources',return_value={'commit':'b'*40}),
                  patch.object(fixed.installer,'finish_environment')):
                fixed.prepare(path)
                basic = json.loads(path.read_text())
                self.assertEqual(basic['pytorch_source'], 'data/source_code/pytorch-2.15.0')
                self.assertIsNone(basic['pytorch_source_commit'])
                self.assertEqual(basic['pytorch_wheel_commit'], 'b'*40)
                self.assertEqual(basic['preparation_status'], 'INPUTS_READY')
                source.rename(source.with_name('pytorch-2.14.0'))
                with self.assertRaisesRegex(ValueError, 'Missing PyTorch source directory'):
                    fixed.prepare(path)
                self.assertEqual(json.loads(path.read_text())['preparation_status'], 'FAILED')

    def test_stable_post_release_and_compatible_nightly_are_distinct(self):
        tag='cp311-cp311-manylinux_2_28_aarch64'
        names = [f'torch_npu-{v}-{tag}.whl' for v in ['2.15.0.post1','2.15.0.dev20261118','2.14.0']]
        names.append('torch_npu-2.15.0-cp310-cp310-manylinux_2_28_aarch64.whl')
        def selected(pre):
            return [name for _,name,_ in fixed.package_candidates(names,'torch-npu',Version('2.15'),{Tag(*tag.split('-'))},pre)]
        self.assertEqual(selected(False),names[:1])
        self.assertEqual(selected(True),names[1:2])

    def test_torch_pin_accepts_cpu_build_but_excludes_cuda_build(self):
        tag = Tag('cp311','cp311','manylinux_2_28_aarch64')
        names = [f'torch-2.15.0+{kind}-{tag}.whl' for kind in ('cpu','cu132')]
        choices = fixed.package_candidates(names,'torch',Version('2.15'),{tag},False)
        self.assertEqual([name for _,name,_ in choices],names[:1])
