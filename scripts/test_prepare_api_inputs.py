"""Offline Step 0 behavior checks with isolated Git repositories and tar/wheel fixtures."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import prepare_api_inputs as prepare
import fetch_latest_sources as sources
import fetch_nightly_wheels as wheel_fetch
import install_env as installer


class Downloader:
    def __init__(self, listing, files):
        self.listing, self.files = listing, files
        self.downloads = []

    def list(self, prefix):
        return self.listing

    def stat(self, url):
        return self.listing[url]

    def download(self, url, destination):
        self.downloads.append(url)
        shutil.copyfile(self.files[url], destination)


class PreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defaults = json.loads((prepare.ROOT / 'api-defaults.json').read_text())
        # Import the same production parser/pair selector, without listing/downloading OBS.
        _, cls.identity, cls.selector = wheel_fetch.load_provider(cls.defaults)
        cls.identity, cls.selector = staticmethod(cls.identity), staticmethod(cls.selector)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='step0-api-test-')
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.patch_root = patch.object(prepare, 'ROOT', self.root)
        self.patch_data = patch.object(prepare, 'DATA', self.data)
        self.patch_root.start()
        self.patch_data.start()
        self.patch_runtime = patch.object(prepare, 'collect_runtime_environment', return_value={
            'driver_version':'26.1.1', 'cann_version':'9.2.0',
            'npu_smi_output':'| npu-smi 26.1.1 Version: 26.1.1 |\n',
            'cann_version_file':self.root/'cann-version.info'})
        self.runtime_mock = self.patch_runtime.start()
        self.patch_source_data = patch.object(sources, "DATA", self.data)
        self.patch_source_data.start()
        self.extra_root_patches = []
        for module in (sources, wheel_fetch):
            root_patch = patch.object(module, 'ROOT', self.root)
            root_patch.start()
            self.extra_root_patches.append(root_patch)
        self.default_requirements=b'# fixture defaults\nnumpy==1.26.4\npytest==8.4.2\n'
        (self.root/'requirements-default.txt').write_bytes(self.default_requirements)
        self.config = dict(self.defaults)
        self.config['obs_prefix'] = 'obs://fixture/7day-cache/'
        self.run_dir = self.data / 'run_data/adapt-torch.Tensor.add-261109'
        self.log = self.root / 'test.log'

    def tearDown(self):
        self.patch_runtime.stop()
        for root_patch in reversed(self.extra_root_patches):
            root_patch.stop()
        self.patch_source_data.stop()
        self.patch_data.stop()
        self.patch_root.stop()
        self.temp.cleanup()

    def git(self, *args):
        result = subprocess.run(['git', *map(str, args)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    def origin(self, name, branch):
        root = self.root / name
        self.git('init', '-b', branch, root)
        (root/'torch_npu').mkdir()
        (root/'torch_npu/__init__.py').write_text('# fixture\n')
        first = self.commit(root, 'first')
        latest = self.commit(root, 'latest')
        return root, first, latest

    def commit(self, root, value):
        (root / 'tracked.txt').write_text(value)
        self.git('-C', root, 'add', '.')
        self.git('-C', root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-m', value)
        return self.git('-C', root, 'rev-parse', 'HEAD')

    def urls(self, pt='a' * 10, npu='b' * 10, py='311', arch='aarch64'):
        suffix = f'-py{py}-torch-npu-builder-{arch}-fixture-{arch}-v3.tar.gz'
        return (self.config['obs_prefix'] + 'wheels-pytorch-' + pt + suffix,
                self.config['obs_prefix'] + 'wheels-torchnpu-' + npu + '-' + pt + suffix)

    def listing(self, *pairs):
        result = {}
        for number, pair in enumerate(pairs, start=1):
            for url in pair:
                result[url] = {'last_modified': f'2026-11-0{number}T01:00:00Z',
                               'etag': 'fixture-etag-' + str(number)}
        return result

    def select(self, listing):
        return wheel_fetch.select_latest_pair(listing, self.config, self.identity, self.selector)

    def wheel(self, name, commit, version='2.16.0a0', py='311', arch='aarch64', requires=None):
        path = self.root / f'{name}-{version}-cp{py}-cp{py}-manylinux_2_28_{arch}.whl'
        metadata = f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nRequires-Python: >=3.10\n'
        if requires:
            metadata += 'Requires-Dist: ' + requires + '\n'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr(f'{name}-{version}.dist-info/METADATA', metadata)
            z.writestr(name + '/version.py', f"git_version = '{commit}'\n")
        return path

    def bundle(self, filename, wheel, member=None):
        path = self.root / filename
        with tarfile.open(path, 'w:gz') as tar:
            tar.add(wheel, arcname=member or ('wheelhouse/' + wheel.name))
        return path

    def fixtures(self):
        pt_repo, pt_wheel_commit, pt_latest = self.origin('pytorch-origin', 'main')
        npu_repo, npu_wheel_commit, npu_latest = self.origin('npu-origin', 'master')
        self.config.update(pytorch_repo=str(pt_repo), torch_npu_repo=str(npu_repo), torch_npu_fork_repo=None)
        urls = self.urls(pt_wheel_commit[:10], npu_wheel_commit[:10])
        pt = self.wheel('torch', pt_wheel_commit, version='2.16.0a0+gitunknown')
        npu = self.wheel('torch_npu', npu_wheel_commit, requires='torch==2.16.0a0')
        files = {urls[0]: self.bundle('pt.tar.gz', pt), urls[1]: self.bundle('npu.tar.gz', npu)}
        source_url=self.config['obs_prefix']+'pytorch-source-'+pt_wheel_commit[:10]+'.tar.gz'
        archive=self.root/'pytorch-source.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            for name,data in [('torch/__init__.py',b''),('test/test_fixture.py',b''),('setup.py',b''),('COMMIT',pt_wheel_commit.encode()),('.gitignore',b'')]:
                info=tarfile.TarInfo('pytorch/'+name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
            link=tarfile.TarInfo('pytorch/.dockerignore');link.type=tarfile.SYMTYPE;link.linkname='.gitignore';tar.addfile(link)
        files[source_url]=archive
        listing=self.listing(urls)
        listing[source_url]={'last_modified':'2026-11-01T01:00:00Z','etag':'source-fixture-etag'}
        return Downloader(listing,files), pt_latest, npu_latest, npu_wheel_commit

    def invoke(self, downloader, extra=()):
        config_path = self.root / 'defaults.json'
        config_path.write_text(json.dumps(self.config))
        argv = ['prepare_api_inputs.py', '--api', 'torch.Tensor.add', '--run-dir', str(self.run_dir),
                '--defaults', str(config_path), *extra]
        provider = (downloader, self.identity, self.selector)
        with patch.object(wheel_fetch, 'load_provider', return_value=provider), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = prepare.main(argv[1:])
            if result or extra:
                return result
            path = self.run_dir/'basic_information.json'
            return wheel_fetch.main(['--basic-information',str(path),'--data-root',str(self.data)])

    def test_latest_complete_pair_ignores_incomplete_and_wrong_python(self):
        first = self.urls('a' * 10)
        newer = self.urls('c' * 10)
        wrong_python = self.urls('d' * 10, py='312')
        listing = self.listing(first, newer[:1], wrong_python)
        self.assertEqual(self.select(listing)['bundle']['wheels'], first[0])
        listing[newer[1]] = {'last_modified': '2026-11-04T01:00:00Z', 'etag': 'new'}
        self.assertEqual(self.select(listing)['bundle']['wheels'], newer[0])

    def test_split_bundle_does_not_pair_across_format_or_environment(self):
        urls = self.urls()
        mismatched = urls[1].replace('-v3.tar.gz', '-v4.tar.gz')
        with self.assertRaisesRegex(ValueError, 'No complete'):
            self.select(self.listing((urls[0], mismatched)))

    def test_unconfigured_chip_os_is_rejected(self):
        self.config['chip_type'] = 'Ascend310P'
        with self.assertRaisesRegex(ValueError, 'No unique wheel profile'):
            prepare.target_profile(self.config)

    def test_torchnpu_updates_actual_mainline_in_the_same_directory(self):
        origin, _, latest = self.origin('source-origin', 'master')
        first = sources.refresh_torchnpu(str(origin),self.log,data_root=self.data)
        advanced = self.commit(origin, 'advanced')
        second = sources.refresh_torchnpu(str(origin),self.log,data_root=self.data)
        self.assertEqual(first['main_branch'], 'master')
        self.assertEqual(first['commit'], latest)
        self.assertEqual(second['commit'], advanced)
        self.assertEqual(first['path'],second['path'])
        self.assertEqual(self.git('-C',first['path'],'rev-parse','HEAD'),advanced)
        self.assertFalse((self.data/'source_code/latest').exists())

    def test_modified_snapshot_is_preserved(self):
        origin, _, _ = self.origin('source-origin', 'main')
        snapshot = sources.refresh_torchnpu(str(origin),self.log,data_root=self.data)
        local = Path(snapshot['path']) / 'local-work.txt'
        local.write_text('keep me')
        with self.assertRaisesRegex(ValueError, 'local changes'):
            sources.refresh_torchnpu(str(origin),self.log,data_root=self.data)
        self.assertEqual(local.read_text(), 'keep me')

    def test_wrong_python_and_arch_wheels_are_rejected(self):
        for py, arch in [('312', 'aarch64'), ('311', 'x86_64')]:
            with self.subTest(py=py, arch=arch):
                path = self.wheel('torch', 'a' * 40, py=py, arch=arch)
                with self.assertRaisesRegex(ValueError, 'tags do not match'):
                    wheel_fetch.wheel_info(path, self.config)

    def test_full_handoff_keeps_mainline_and_wheel_identity_separate(self):
        downloader, pt_latest, npu_latest, npu_wheel_commit = self.fixtures()
        self.assertEqual(self.invoke(downloader), 0)
        basic = json.loads((self.run_dir / 'basic_information.json').read_text())
        record = json.loads((self.run_dir / 'log/step0-preparation.json').read_text())
        self.assertEqual(basic['problem-description'], 'API `torch.Tensor.add` needs adaptation')
        self.assertNotIn('api_names', basic)
        self.assertEqual(basic['pytorch_source_commit'],self.identity(next(u for u in downloader.listing if 'wheels-pytorch-' in u))['pytorch_short'])
        self.assertEqual(basic['torch_npu_main_commit'], npu_latest)
        self.assertEqual(basic['torch_npu_commit'], npu_wheel_commit)
        self.assertEqual(self.git('-C',self.data/'source_code/torch_npu','rev-parse','HEAD'),npu_wheel_commit)
        self.assertEqual(json.loads((self.run_dir/'log/step0-source.json').read_text())['status'],'SOURCE_READY')
        self.assertNotEqual(npu_latest, npu_wheel_commit)
        self.assertNotIn('torch_npu_source', basic)
        self.assertEqual(record['status'], 'INPUTS_READY')
        self.assertEqual((self.run_dir/'requirements.txt').read_bytes(),self.default_requirements)
        self.assertEqual({p.name for p in self.run_dir.iterdir()},
                         {'basic_information.json', 'requirements.txt', 'log'})
        before = (self.run_dir / 'basic_information.json').read_bytes()
        self.assertEqual(self.invoke(downloader), 1)
        self.assertEqual((self.run_dir / 'basic_information.json').read_bytes(), before)
        self.assertEqual(json.loads((self.run_dir / 'log/step0-preparation.json').read_text())['status'], 'INPUTS_READY')

    def test_initializer_dry_run_creates_no_runtime_files(self):
        downloader, *_ = self.fixtures()
        self.assertEqual(self.invoke(downloader, ['--dry-run']), 0)
        self.assertFalse(self.run_dir.exists())
        self.assertFalse((self.data / 'source_code').exists())
        self.assertEqual(downloader.downloads, [])

    def test_incompatible_torch_requirement_fails_without_ready_handoff(self):
        downloader, *_ = self.fixtures()
        npu_url = next(url for url in downloader.files if 'wheels-torchnpu-' in url)
        short = self.identity(npu_url)['torch_npu_short']
        npu = self.wheel('torch_npu', short + 'a' * 30, requires='torch==2.15.0')
        downloader.files[npu_url] = self.bundle('bad-npu.tar.gz', npu)
        self.assertEqual(self.invoke(downloader), 1)
        partial = json.loads((self.run_dir/'basic_information.json').read_text())
        self.assertEqual(partial['preparation_status'],'FAILED')
        self.assertNotIn('pytorch_wheel',partial)
        state = json.loads((self.run_dir / 'log/step0-preparation.json').read_text())
        self.assertEqual(state['status'], 'FAILED')
        self.assertIn('incompatible torch', state['error'])

    def test_download_replaces_flat_wheels_without_archive_or_receipts(self):
        downloader,*_=self.fixtures();selection=self.select(downloader.listing)
        first=wheel_fetch.download_wheels(selection,downloader,self.config,data_root=self.data)
        folder=self.data/'wheels'
        self.assertTrue(all(p.is_file() and p.suffix=='.whl' for p in folder.iterdir()))
        original=first['torch']['sha256']
        Path(first['torch']['path']).write_bytes(b'changed local wheel')
        second=wheel_fetch.download_wheels(selection,downloader,self.config,data_root=self.data)
        self.assertEqual(second['torch']['sha256'],original)
        self.assertEqual(len(downloader.downloads),4)
        self.assertTrue(all(p.parent==folder for p in map(Path,[w['path'] for w in second.values()])))


    def seed(self):
        config_path=self.root/'defaults.json'
        config_path.write_text(json.dumps(self.config))
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(prepare.main(['--api','torch.Tensor.add','--run-dir',str(self.run_dir),'--defaults',str(config_path)]),0)
        return self.run_dir/'basic_information.json'

    def test_initializer_is_offline_and_only_writes_initial_fields(self):
        with patch.object(wheel_fetch,'resolve',side_effect=AssertionError('initializer must not query OBS')), \
                patch.object(sources,'prepare_sources',side_effect=AssertionError('initializer must not fetch sources')):
            path=self.seed()
        basic=json.loads(path.read_text())
        self.assertEqual(basic['preparation_status'],'BASIC_READY')
        self.assertNotIn('pytorch_source',basic)
        self.assertNotIn('pytorch_wheel',basic)
        self.assertFalse((self.data/'source_code').exists())
        self.assertFalse((self.data/'wheels').exists())

    def test_json_is_progressively_completed_without_losing_caller_fields(self):
        downloader,pt_latest,npu_latest,npu_wheel_commit=self.fixtures()
        path=self.seed()
        initial=json.loads(path.read_text());initial['caller_note']='keep me';initial['api_names']=['torch.Tensor.add'];path.write_text(json.dumps(initial))
        with patch.object(wheel_fetch,'load_provider',return_value=(downloader,self.identity,self.selector)),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(sources.main(['--basic-information',str(path)]),0)
        middle=json.loads(path.read_text())
        self.assertNotIn('api_names', middle)
        self.assertEqual(middle['preparation_status'],'SOURCES_READY')
        self.assertTrue((self.root / middle['pytorch_source']).is_dir())
        self.assertEqual(middle['pytorch_source'],'data/source_code/pytorch')
        self.assertNotIn('pytorch_wheel',middle)
        provider=(downloader,self.identity,self.selector)
        with patch.object(wheel_fetch,'load_provider',return_value=provider),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            argv=['--basic-information',str(path),'--data-root',str(self.data)]
            self.assertEqual(wheel_fetch.main(argv),0)
            after=path.read_bytes()
            self.assertEqual(wheel_fetch.main(argv),0)
            self.assertEqual(json.loads(path.read_text())['pytorch_source'],'data/source_code/pytorch')
        basic=json.loads(path.read_text())
        self.assertEqual(basic['preparation_status'],'INPUTS_READY')
        self.assertEqual(basic['caller_note'],'keep me')
        self.assertEqual(basic['torch_npu_main_commit'],npu_latest)
        self.assertEqual(basic['torch_npu_commit'],npu_wheel_commit)

    def test_one_fetch_completes_sources_and_wheels(self):
        downloader,*_=self.fixtures();path=self.seed()
        with patch.object(wheel_fetch,'load_provider',return_value=(downloader,self.identity,self.selector)),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(wheel_fetch.main(['--basic-information',str(path),'--data-root',str(self.data)]),0)
            self.assertEqual(json.loads(path.read_text())['preparation_status'],'INPUTS_READY')
            self.assertEqual(sources.main(['--basic-information',str(path)]),0)
        self.assertEqual(json.loads(path.read_text())['preparation_status'],'SOURCES_READY')

    def test_installer_rejects_partial_json_before_creating_environment(self):
        path=self.seed();errors=io.StringIO()
        with patch.object(installer,'DATA',self.data),patch.object(sys,'argv',['install_env.py','--run-dir',str(self.run_dir),'--dry-run']),redirect_stderr(errors):
            self.assertEqual(installer.main(),1)
        self.assertIn('incomplete',errors.getvalue())
        self.assertFalse((self.data/'venv').exists())

    def test_task_target_cannot_be_overridden_during_wheel_fetch(self):
        path=self.seed();before=path.read_bytes()
        with redirect_stderr(io.StringIO()):
            self.assertEqual(wheel_fetch.main(['--basic-information',str(path),'--data-root',str(self.data),'--python-version','3.12.0']),1)
        self.assertEqual(path.read_bytes(),before)


    def test_default_requirements_are_copied_as_a_task_snapshot(self):
        path=self.seed()
        requirements=path.parent/'requirements.txt'
        self.assertEqual(requirements.read_bytes(),self.default_requirements)
        (self.root/'requirements-default.txt').write_text('numpy==2.0.0\n')
        self.assertEqual(requirements.read_bytes(),self.default_requirements)

    def test_missing_default_requirements_creates_no_task(self):
        (self.root/'requirements-default.txt').unlink()
        config=self.root/'defaults.json';config.write_text(json.dumps(self.config))
        with redirect_stderr(io.StringIO()):
            self.assertEqual(prepare.main(['--api','torch.add','--run-dir',str(self.run_dir),'--defaults',str(config)]),1)
        self.assertFalse(self.run_dir.exists())

    def test_source_failure_keeps_json_partial_and_skips_wheel_download(self):
        downloader,*_=self.fixtures();path=self.seed()
        before=path.read_bytes()
        with patch.object(wheel_fetch,'load_provider',return_value=(downloader,self.identity,self.selector)), \
                patch.object(sources,'complete_sources',side_effect=RuntimeError('source fetch failed')),redirect_stderr(io.StringIO()):
            self.assertEqual(wheel_fetch.main(['--basic-information',str(path),'--data-root',str(self.data)]),1)
        expected=json.loads(before);expected['preparation_status']='FAILED'
        self.assertEqual(json.loads(path.read_text()),expected)
        self.assertEqual(downloader.downloads,[])
        record=json.loads((path.parent/'log/step0-preparation.json').read_text())
        self.assertEqual(record['failed_stage'],'sources')


if __name__ == '__main__':
    unittest.main(verbosity=2)
