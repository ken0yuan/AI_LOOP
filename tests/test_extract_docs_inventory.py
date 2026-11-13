"""Inventory-only extraction: no HTML requests and unchanged API name format."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import extract_docs_inventory as crawler
from api_first_seen import capture_document_version, read_document_version


def inventory(rows=None, version='main (2.16.0a0+gitfixture)'):
    rows = rows if rows is not None else [
        ('torch.b', 'py:function', 'generated/torch.b.html#$'),
        ('torch.a', 'py:function', 'generated/torch.a.html#$'),
    ]
    header = ('# Sphinx inventory version 2\n# Project: PyTorch\n'
              f'# Version: {version}\n# The remainder is compressed using zlib.\n')
    body = ''.join(f'{name} {role} 1 {uri} -\n' for name, role, uri in rows)
    return header.encode() + zlib.compress(body.encode())


class InventoryExtractionTests(unittest.TestCase):
    def setUp(self):
        parent = Path(os.environ.get('INVENTORY_TEST_TMPDIR', tempfile.gettempdir())).resolve()
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temporary.name).resolve()
        assert self.root.is_relative_to(parent)
        self.addCleanup(self.temporary.cleanup)
        self.output = self.root / 'docs-api.json'
        self.cache = self.root / 'docs-cache'
        self.data = inventory()
        self.calls = []
        self.responses = []

    def invoke(self, version='main'):
        def urlopen(request, timeout=30):
            url = request.full_url
            self.calls.append(url)
            if not url.endswith('/objects.inv'):
                raise AssertionError('HTML must never be requested: ' + url)
            response = self.responses.pop(0) if self.responses else self.data
            if isinstance(response, BaseException):
                raise response
            return io.BytesIO(response)
        argv = ['extract_docs_inventory.py', version, str(self.output),
                '--cache-dir', str(self.cache)]
        with patch.object(sys, 'argv', argv), patch('urllib.request.urlopen', side_effect=urlopen), \
                patch('time.sleep') as sleep, contextlib.redirect_stdout(io.StringIO()):
            crawler.main()
        return sleep

    def test_only_inventory_is_requested_and_metadata_maps_are_empty(self):
        self.invoke()
        self.assertEqual(self.calls, ['https://docs.pytorch.org/docs/main/objects.inv'])
        self.assertEqual(json.loads(self.output.read_text()),
                         {'torch': ['a', 'b'], '_signatures': {}, '_sources': {}})

    def test_all_api_roles_private_names_and_directory_links_are_retained(self):
        self.data = inventory([
            ('torch.fn', 'py:function', 'generated/torch.fn.html#$'),
            ('torch.C', 'py:class', 'generated/torch.C.html#$'),
            ('torch.E', 'py:exception', 'generated/torch.E.html#$'),
            ('torch.C.method', 'py:method', 'generated/torch.C.html#$'),
            ('torch.C.attr', 'py:attribute', 'generated/torch.C.html#$'),
            ('torch.C.prop', 'py:property', 'generated/torch.C.html#$'),
            ('torch._local.setting', 'py:data', 'notes/local.html#$'),
            ('Tensor.backward', 'py:method', 'generated/torch.Tensor.html#$'),
            ('torch.torch.setting', 'py:data', 'torch.html#$'),
            ('torch.distributed.elastic.fn', 'py:function', 'elastic/generated/fn.html#$'),
            ('torch.module', 'py:module', 'module.html#$'),
            ('torch.chapter', 'std:label', 'notes.html#$'),
        ])
        self.invoke()
        data = json.loads(self.output.read_text())
        names = {module + '.' + name for module, values in data.items()
                 if not module.startswith('_') for name in values}
        self.assertEqual(names, {'torch.fn', 'torch.C', 'torch.E', 'torch.C.method',
                                'torch.C.attr', 'torch.C.prop', 'torch._local.setting',
                                'torch.Tensor.backward', 'torch.setting',
                                'torch.distributed.elastic.fn'})
        self.assertEqual(len(self.calls), 1)

    def test_multiple_index_names_for_same_target_are_preserved(self):
        self.data = inventory([
            ('torch.a', 'py:function', 'generated/torch.a.html#$'),
            ('torch.alias', 'py:function', 'generated/torch.a.html#torch.a'),
        ])
        self.invoke()
        self.assertEqual(json.loads(self.output.read_text())['torch'], ['a', 'alias'])

    def test_repeated_extraction_has_identical_json(self):
        self.invoke()
        previous = self.output.read_bytes()
        self.invoke()
        self.assertEqual(self.output.read_bytes(), previous)
        self.assertEqual(len(self.calls), 2)

    def test_main_rereads_current_inventory_on_each_extraction(self):
        self.invoke()
        self.data = inventory([('torch.new', 'py:function', 'generated/torch.new.html#$')])
        self.invoke()
        self.assertEqual(json.loads(self.output.read_text())['torch'], ['new'])
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.cache / 'main').exists())

    def test_numeric_version_normalization_and_inventory_cache(self):
        self.data = inventory(version='2.14')
        self.invoke('2.14.0')
        self.assertEqual(self.calls, ['https://docs.pytorch.org/docs/2.14/objects.inv'])
        self.assertEqual((self.cache / '2.14/objects.inv').read_bytes(), self.data)
        self.calls.clear()
        self.invoke('2.14.0')
        self.assertEqual(self.calls, [])

    def test_transient_inventory_failure_keeps_bounded_retry(self):
        self.responses = [TimeoutError('first'), TimeoutError('second'), self.data]
        sleep = self.invoke()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    def test_http_failure_does_not_replace_previous_output(self):
        previous = b'{"previous": true}'
        self.output.write_bytes(previous)
        self.responses = [urllib.error.HTTPError('inventory', 503, 'fixture unavailable', {}, None)]
        with self.assertRaises(urllib.error.HTTPError):
            self.invoke()
        self.assertEqual(self.output.read_bytes(), previous)
        self.assertEqual(len(self.calls), 1)

    def test_malformed_inventory_does_not_replace_previous_output(self):
        good_header = self.data.split(b'\n', 4)[:4]
        header = b'\n'.join(good_header) + b'\n'
        for invalid in [b'not an inventory', header + b'not zlib',
                        header + zlib.compress(b'bad inventory record\n')]:
            with self.subTest(invalid=invalid[:20]):
                self.data = invalid
                self.output.write_text('previous')
                with self.assertRaises((ValueError, zlib.error)):
                    self.invoke()
                self.assertEqual(self.output.read_text(), 'previous')

    def test_inventory_without_api_targets_is_rejected(self):
        self.data = inventory([('torch', 'py:module', 'torch.html#module-torch')])
        with self.assertRaisesRegex(ValueError, 'no documented PyTorch APIs'):
            self.invoke()
        self.assertFalse(self.output.exists())

    def test_conflicting_targets_for_same_api_are_rejected(self):
        self.data = inventory([
            ('torch.a', 'py:function', 'one.html#torch.a'),
            ('torch.a', 'py:function', 'two.html#torch.a'),
        ])
        with self.assertRaisesRegex(ValueError, 'Conflicting declaration pages'):
            self.invoke()
        self.assertFalse(self.output.exists())

    def test_first_seen_version_capture_uses_same_inventory_request(self):
        with capture_document_version(crawler, self.root):
            self.invoke()
        self.assertEqual(self.calls, ['https://docs.pytorch.org/docs/main/objects.inv'])
        record = json.loads((self.root / 'docs-version.json').read_text())
        self.assertEqual(read_document_version(self.root), '2.16')
        self.assertEqual(record['inventory_sha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(record['docs_sha256'], hashlib.sha256(self.output.read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main(verbosity=2)
