"""Check portable Step 0 files through preparation, fetch and environment input resolution."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import fetch_nightly_wheels as fetch
import install_env as installer
import obs_downloader
import test_prepare_api_inputs as fixtures


class RelativeHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PreparationTests.setUpClass()

    def setUp(self):
        self.f = fixtures.PreparationTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def test_task_and_receipts_survive_project_move_and_other_working_directory(self):
        downloader, *_ = self.f.fixtures()
        task = self.f.seed()
        initial = json.loads(task.read_text())
        self.assertEqual(initial['preparation_record'],
                         'data/run_data/adapt-torch.Tensor.add-261109/log/step0-preparation.json')
        self.assertFalse(Path(initial['preparation_config']['obs']['downloader']['bin']).is_absolute())
        with patch.object(fetch, 'load_provider', return_value=(downloader, self.f.identity, self.f.selector)), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(fetch.main(['--basic-information', str(task), '--data-root', str(self.f.data)]), 0)
        basic = json.loads(task.read_text())
        record = json.loads((task.parent/'log/step0-preparation.json').read_text())
        receipt = json.loads((self.f.data/'source_code/pytorch-source.json').read_text())
        self.assertEqual(basic['pytorch_source'], 'data/source_code/pytorch')
        self.assertEqual(basic['torch_npu_latest_source'], 'data/source_code/torch_npu')
        for key in ('pytorch_wheel', 'torch_npu_wheel'):
            self.assertTrue(basic[key].startswith('data/wheels/'))
        paths = [basic[k] for k in ('pytorch_source', 'torch_npu_latest_source', 'pytorch_wheel', 'torch_npu_wheel')]
        paths += [record['defaults_path'], record['requirements_defaults_path'], receipt['path'], receipt['archive']]
        paths += [item['path'] for item in record['sources'].values()]
        paths += [item['path'] for item in record['wheels'].values()]
        self.assertTrue(all(not Path(value).is_absolute() for value in paths))
        self.assertEqual(record['status'], 'INPUTS_READY')
        for item in record['wheels'].values():
            self.assertEqual(fetch.sha256(self.f.root/item['path']), item['sha256'])
        # The installer must resolve the same relative handoff after the project is moved.
        with tempfile.TemporaryDirectory() as target:
            moved = Path(target)/'AI_LOOP'
            shutil.copytree(self.f.data, moved/'data', symlinks=True)
            old_cwd = Path.cwd()
            try:
                os.chdir(target)
                with patch.object(installer, 'ROOT', moved), patch.object(installer, 'DATA', moved/'data'):
                    self.assertEqual(installer.cache_path(basic['pytorch_source'], 'source_code'), moved/'data/source_code/pytorch')
                    for key, name, version in (('pytorch_wheel', 'torch', 'pytorch_version'),
                                               ('torch_npu_wheel', 'torch-npu', 'torch_npu_version')):
                        wheel = installer.cache_path(basic[key], 'wheels')
                        installer.wheel_metadata(wheel, name, basic[version])
            finally:
                os.chdir(old_cwd)

    def test_relative_downloader_binary_is_resolved_from_project_root(self):
        with patch.object(obs_downloader, 'ROOT', self.f.root):
            binary = obs_downloader.ObsutilDownloader('../../tools/obsutil').bin_path
            self.assertEqual(Path(binary).resolve(), (self.f.root/'../../tools/obsutil').resolve())
            self.assertEqual(obs_downloader.ObsutilDownloader('obsutil').bin_path, 'obsutil')


if __name__ == '__main__':
    unittest.main(verbosity=2)
