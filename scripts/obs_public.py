"""Anonymous HTTPS reads for an explicitly configured public OBS bucket."""
from contextlib import contextmanager, nullcontext
from email.utils import parsedate_to_datetime
import http.client
from pathlib import Path
import re
import shutil
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
import xml.etree.ElementTree as ET

from obs_downloader import DownloadedObject, ObsDownloadError


class PublicObsDownloader:
    def __init__(self, endpoint, bucket, *, regional_proxy_endpoint=None):
        endpoint = endpoint if '://' in endpoint else 'https://' + endpoint
        parsed = urlsplit(endpoint)
        if (parsed.scheme != 'https' or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                or parsed.port not in (None, 443)
                or not re.fullmatch(r'obs\.[a-z0-9-]+\.myhuaweicloud\.com', parsed.hostname or '')
                or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', bucket or '')):
            raise ValueError('Public OBS requires one bucket and an official HTTPS endpoint')
        self.bucket, self.endpoint = bucket, parsed.hostname
        self.base = 'https://' + bucket + '.' + parsed.hostname
        self.regional_proxy_endpoint = regional_proxy_endpoint

    def key(self, url):
        parsed = urlsplit(url)
        if (parsed.scheme != 'obs' or parsed.netloc != self.bucket or parsed.query
                or parsed.fragment or '..' in parsed.path.split('/')):
            raise ObsDownloadError('Object is outside the configured public OBS bucket: ' + url)
        return parsed.path.lstrip('/')

    @contextmanager
    def response(self, url, method='GET', timeout=120):
        if self.regional_proxy_endpoint:
            from obs_transport import ObsRegionalProxy
            route = ObsRegionalProxy(self.regional_proxy_endpoint, self.bucket)
        else:
            route = nullcontext(None)
        with route as proxy:
            opener = build_opener(ProxyHandler({'https': proxy}) if proxy else ProxyHandler({}))
            for attempt in range(2):
                try:
                    response = opener.open(Request(url, method=method), timeout=timeout)
                    break
                except HTTPError as error:
                    retry = error.code == 429 or error.code >= 500
                    if not retry or attempt:
                        raise
                except (URLError, TimeoutError, OSError):
                    if attempt:
                        raise
                time.sleep(1)
            with response:
                yield response

    def list(self, prefix_url):
        prefix, marker, result = self.key(prefix_url), '', {}
        for page in range(100):
            params = {'prefix': prefix, 'max-keys': 1000}
            if marker:
                params['marker'] = marker
            url = self.base + '/?' + urlencode(params)
            try:
                with self.response(url) as response:
                    raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ObsDownloadError('Public OBS listing response is too large')
                tree = ET.fromstring(raw)
                for element in tree.iter():
                    element.tag = element.tag.rsplit('}', 1)[-1]
                if tree.findtext('Name') != self.bucket or tree.findtext('Prefix') != prefix:
                    raise ObsDownloadError('Public OBS listing bucket/prefix mismatch')
                for item in tree.findall('Contents'):
                    key = item.findtext('Key')
                    if not key or not key.startswith(prefix):
                        raise ObsDownloadError('Public OBS returned an outside-prefix object')
                    if key.endswith('/'):
                        continue
                    result['obs://' + self.bucket + '/' + key] = {
                        'last_modified': item.findtext('LastModified'),
                        'etag': (item.findtext('ETag') or '').strip('"'),
                        'size': int(item.findtext('Size'))}
                if tree.findtext('IsTruncated', '').lower() != 'true':
                    return result
                next_marker = tree.findtext('NextMarker') or ''
                if not next_marker or next_marker <= marker:
                    raise ObsDownloadError('Public OBS truncated listing lacks a progressing marker')
                marker = next_marker
            except (HTTPError, URLError, TimeoutError, OSError, ET.ParseError, ValueError) as error:
                raise ObsDownloadError('Public OBS list failed: ' + str(error)) from error
        raise ObsDownloadError('Public OBS listing exceeded 100 pages; refusing a truncated snapshot')

    def stat(self, url):
        address = self.base + '/' + quote(self.key(url), safe='/')
        try:
            with self.response(address, 'HEAD') as response:
                return {'etag': response.headers['ETag'].strip('"'),
                        'last_modified': parsedate_to_datetime(response.headers['Last-Modified']).isoformat(),
                        'size': int(response.headers['Content-Length'])}
        except HTTPError as error:
            if error.code == 404:
                return None
            raise ObsDownloadError('Public OBS stat failed: ' + str(error)) from error
        except (URLError, TimeoutError, OSError, KeyError, TypeError, ValueError) as error:
            raise ObsDownloadError('Public OBS stat failed: ' + str(error)) from error

    def download(self, url, dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        address = self.base + '/' + quote(self.key(url), safe='/')
        for attempt in range(2):
            try:
                with self.response(address, timeout=1800) as response, dest.open('wb') as stream:
                    expected = response.headers.get('Content-Length')
                    shutil.copyfileobj(response, stream, length=1024 * 1024)
                if not dest.stat().st_size or (expected is not None and dest.stat().st_size != int(expected)):
                    raise ObsDownloadError('Public OBS download length mismatch')
                return DownloadedObject(url, dest, dest.stat().st_size)
            except HTTPError as error:
                if error.code not in (408, 429) and error.code < 500:
                    raise ObsDownloadError('Public OBS download failed: ' + str(error)) from error
                last = error
            except (URLError, TimeoutError, OSError, http.client.HTTPException, ObsDownloadError) as error:
                last = error
            if not attempt:
                time.sleep(1)
        raise ObsDownloadError('Public OBS download failed: ' + str(last)) from last
