from contextlib import contextmanager
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from obs_downloader import ObsDownloadError, RoutedDownloader
from obs_public import PublicObsDownloader
import fetch_nightly_wheels as fetch
import preparation_state
import check_nightly_availability as health


class PublicObsTests(unittest.TestCase):
    def downloader(self):
        return PublicObsDownloader('https://obs.cn-southwest-2.myhuaweicloud.com', 'pytorch-torchnpu')

    def xml(self, name, truncated=False, marker=''):
        return ('<ListBucketResult xmlns="http://obs.myhwclouds.com/doc/2015-06-30/">'
                '<Name>pytorch-torchnpu</Name><Prefix>7day-cache/</Prefix>'
                '<IsTruncated>%s</IsTruncated><NextMarker>%s</NextMarker>'
                '<Contents><Key>7day-cache/%s</Key><LastModified>2026-11-16T01:00:00Z</LastModified>'
                '<ETag>"etag-%s"</ETag><Size>10</Size></Contents></ListBucketResult>'
                % (str(truncated).lower(), marker, name, name)).encode()

    def test_pagination_includes_later_objects_and_passes_marker(self):
        downloader = self.downloader()
        urls = []
        @contextmanager
        def response(url, *args, **kwargs):
            urls.append(url)
            yield io.BytesIO(self.xml('old', True, '7day-cache/old') if len(urls) == 1 else self.xml('new'))
        with patch.object(downloader, 'response', response):
            result = downloader.list('obs://pytorch-torchnpu/7day-cache/')
        self.assertEqual(len(result), 2)
        self.assertIn('marker=7day-cache%2Fold', urls[1])
        self.assertEqual(result['obs://pytorch-torchnpu/7day-cache/new']['etag'], 'etag-new')

    def test_truncated_listing_without_marker_is_rejected(self):
        downloader = self.downloader()
        @contextmanager
        def response(*args, **kwargs):
            yield io.BytesIO(self.xml('old', True))
        with patch.object(downloader, 'response', response), self.assertRaisesRegex(ObsDownloadError, 'marker'):
            downloader.list('obs://pytorch-torchnpu/7day-cache/')

    def test_stat_distinguishes_missing_from_denied(self):
        downloader = self.downloader()
        @contextmanager
        def denied(*args, **kwargs):
            raise HTTPError('https://example', 403, 'Forbidden', {}, None)
            yield
        with patch.object(downloader, 'response', denied), self.assertRaises(ObsDownloadError):
            downloader.stat('obs://pytorch-torchnpu/7day-cache/file')
        @contextmanager
        def missing(*args, **kwargs):
            raise HTTPError('https://example', 404, 'Not found', {}, None)
            yield
        with patch.object(downloader, 'response', missing):
            self.assertIsNone(downloader.stat('obs://pytorch-torchnpu/7day-cache/file'))

    def test_partial_download_does_not_count_as_success(self):
        downloader = self.downloader()
        class Body(io.BytesIO):
            headers = {'Content-Length': '20'}
        @contextmanager
        def response(*args, **kwargs):
            yield Body(b'incomplete')
        with tempfile.TemporaryDirectory() as folder, patch.object(downloader, 'response', response), patch('obs_public.time.sleep'):
            with self.assertRaisesRegex(ObsDownloadError, 'length mismatch'):
                downloader.download('obs://pytorch-torchnpu/7day-cache/file', Path(folder) / 'archive')

    def test_router_keeps_bucket_credentials_separate(self):
        class Provider:
            def __init__(self, name): self.name = name
            def stat(self, url): return self.name
        router = RoutedDownloader({'obs://new/cache/': Provider('public'), 'obs://old/cache/': Provider('private')})
        self.assertEqual(router.stat('obs://new/cache/wheel'), 'public')
        self.assertEqual(router.stat('obs://old/cache/source'), 'private')
        with self.assertRaises(ObsDownloadError):
            router.stat('obs://other/cache/object')


class SplitSourceTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((fetch.ROOT / 'api-defaults.json').read_text())
        self.config['obs_prefix'] = 'obs://new/cache/'
        self.config['pytorch_source_obs_prefix'] = 'obs://old/sources/'
        self.pt = 'obs://new/cache/wheels-pytorch-aaaaaaaaaa-py311-torch-npu-builder-aarch64-image-aarch64-v3.tar.gz'
        self.npu = 'obs://new/cache/wheels-torchnpu-bbbbbbbbbb-aaaaaaaaaa-py311-torch-npu-builder-aarch64-image-aarch64-v3.tar.gz'
        self.source = 'obs://old/sources/pytorch-source-aaaaaaaaaa.tar.gz'
        metadata = {'last_modified': '2026-11-16T01:00:00Z', 'etag': 'fixture'}
        self.wheels = {self.pt: dict(metadata), self.npu: dict(metadata)}
        self.sources = {self.source: dict(metadata)}

    def provider(self):
        outer = self
        class Provider:
            def list(self, prefix):
                return outer.wheels if prefix == outer.config['obs_prefix'] else outer.sources
        from wheel_artifacts import wheel_identity, select_bundle
        return Provider(), wheel_identity, select_bundle

    def test_new_wheels_pair_with_old_bucket_source_and_health_agrees(self):
        with patch.object(fetch, 'load_provider', return_value=self.provider()):
            _, selection = fetch.resolve(self.config)
            report = health.check(self.config)
        self.assertEqual(selection['pytorch_source']['url'], self.source)
        self.assertEqual(report['status'], 'AVAILABLE')
        self.assertEqual(report['selection']['pytorch_source']['url'], self.source)

    def test_new_wheels_without_exact_source_are_rejected(self):
        self.sources = {'obs://old/sources/pytorch-source-cccccccccc.tar.gz': next(iter(self.sources.values()))}
        with patch.object(fetch, 'load_provider', return_value=self.provider()):
            with self.assertRaisesRegex(ValueError, 'No complete'):
                fetch.resolve(self.config)
            self.assertEqual(health.check(self.config)['status'], 'NO_COMPLETE_BUNDLE')

    def test_task_snapshot_retains_both_providers_without_credentials(self):
        config = copy.deepcopy(self.config)
        config['obs']['downloader']['secret'] = 'must-not-persist'
        config['pytorch_source_obs']['downloader']['secret'] = 'must-not-persist'
        snapshot = preparation_state.config_snapshot(config)
        self.assertEqual(snapshot['pytorch_source_obs_prefix'], 'obs://old/sources/')
        self.assertEqual(snapshot['obs']['downloader']['type'], 'public-obs')
        self.assertIn('endpoint', snapshot['obs']['downloader'])
        self.assertEqual(snapshot['pytorch_source_obs']['downloader']['type'], 'obsutil')
        self.assertNotIn('must-not-persist', json.dumps(snapshot))


if __name__ == '__main__':
    unittest.main()
