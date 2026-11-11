"""Flat wheel outputs: no archives, JSON, logs or nested directories."""
import io
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import fetch_nightly_wheels as fetch
import test_prepare_api_inputs as fixtures


class FlatWheelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.PreparationTests.setUpClass()

    def setUp(self):
        self.f=fixtures.PreparationTests('runTest');self.f.setUp();self.addCleanup(self.f.tearDown)
        self.downloader,*_=self.f.fixtures()
        self.selection=self.f.select(self.downloader.listing)

    def download(self):
        return fetch.download_wheels(self.selection,self.downloader,self.f.config,data_root=self.f.data)

    def test_optional_torchvision_is_saved_next_to_framework_wheels(self):
        pt_url=next(u for u in self.selection['objects'] if 'wheels-pytorch-' in u)
        pt_archive=self.downloader.files[pt_url]
        with tarfile.open(pt_archive,'r:gz') as tar:
            old=tar.getmembers()[0];data=tar.extractfile(old).read();name=old.name
        vision=self.f.wheel('torchvision','c'*40,version='0.30.0a0')
        with tarfile.open(pt_archive,'w:gz') as tar:
            info=tarfile.TarInfo(name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
            tar.add(vision,arcname='wheelhouse/'+vision.name)
        result=self.download()
        output=self.f.data/'wheels'
        self.assertEqual(len(list(output.iterdir())),3)
        self.assertTrue((output/vision.name).is_file())
        self.assertTrue(all(p.is_file() and p.suffix=='.whl' for p in output.iterdir()))
        self.assertEqual(set(result),{'torch','torch-npu'})

    def test_invalid_new_bundle_keeps_existing_wheels_unchanged(self):
        first=self.download();original={k:Path(v['path']).read_bytes() for k,v in first.items()}
        npu_url=next(u for u in self.downloader.files if 'wheels-torchnpu-' in u)
        identity=self.f.identity(npu_url)
        bad=self.f.wheel('torch_npu',identity['torch_npu_short']+'a'*30,requires='torch==0.0.0')
        self.downloader.files[npu_url]=self.f.bundle('invalid-new.tar.gz',bad)
        with self.assertRaisesRegex(ValueError,'incompatible torch'):self.download()
        for key,value in first.items():self.assertEqual(Path(value['path']).read_bytes(),original[key])
        self.assertFalse(list(self.f.data.glob('.wheel-download-*')))
        self.assertTrue(all(p.suffix=='.whl' for p in (self.f.data/'wheels').iterdir()))

    def test_download_failure_cleans_temporary_archives(self):
        with patch.object(self.downloader,'download',side_effect=RuntimeError('download failed')):
            with self.assertRaisesRegex(RuntimeError,'download failed'):self.download()
        self.assertFalse(list((self.f.data/'wheels').iterdir()))
        self.assertFalse(list(self.f.data.glob('.wheel-download-*')))


if __name__=='__main__':unittest.main(verbosity=2)
