"""Record the main documentation version without another network request."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

COLUMN = 'api首次出现版本'


def release_version(value):
    """Store major.minor, including inventory headers such as main (2.16.0a0)."""
    match = re.search(r'(?<![\w.])(\d+)\.(\d+)(?=\.|\s|\)|$)', value or '')
    if not match:
        raise ValueError('Documentation version must identify a numeric major.minor release')
    return '.'.join(str(int(part)) for part in match.groups())


def inventory_version(data):
    header = data.split(b'\n', 4)
    if (len(header) != 5 or header[0].rstrip(b'\r') != b'# Sphinx inventory version 2'
            or header[1].rstrip(b'\r') != b'# Project: PyTorch'
            or not header[2].startswith(b'# Version: ')):
        raise ValueError('Missing PyTorch documentation version in objects.inv')
    return release_version(header[2].decode('utf-8').removeprefix('# Version: '))


@contextmanager
def capture_document_version(crawler, work):
    """Observe the exact inventory already fetched by the existing collector."""
    work = Path(work)
    record = work / 'docs-version.json'
    record.unlink(missing_ok=True)
    original = crawler.fetch_inventory
    captured = {}

    def fetch(base_url):
        data = original(base_url)
        captured.update(version=inventory_version(data),
                        inventory_sha256=hashlib.sha256(data).hexdigest())
        return data

    crawler.fetch_inventory = fetch
    temporary = None
    try:
        yield
        if not captured:
            raise ValueError('Documentation extraction did not provide its inventory version')
        captured['docs_sha256'] = hashlib.sha256((work / 'docs-api.json').read_bytes()).hexdigest()
        fd, name = tempfile.mkstemp(dir=work, prefix='.docs-version-', suffix='.tmp')
        temporary = Path(name)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(captured, stream, ensure_ascii=False)
            stream.write('\n')
        os.replace(temporary, record)
    finally:
        crawler.fetch_inventory = original
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_document_version(work):
    """A resumed wheel audit must keep the version of its saved document list."""
    work = Path(work)
    try:
        record = json.loads((work / 'docs-version.json').read_text(encoding='utf-8'))
        actual = hashlib.sha256((work / 'docs-api.json').read_bytes()).hexdigest()
        if record.get('docs_sha256') != actual:
            raise ValueError('Documentation version does not match the saved API list')
        return release_version(record.get('version'))
    except FileNotFoundError as error:
        raise ValueError('Documentation version is unavailable; re-extract the document list before insertion') from error


def stamp_new_apis(conn, names, version):
    """Called inside the refresh transaction; never change an existing version."""
    if not names or COLUMN not in {row[1] for row in conn.execute('PRAGMA table_info("API支持数据")')}:
        return
    version = release_version(version)
    conn.executemany('UPDATE "API支持数据" SET "api首次出现版本"=? '
                     'WHERE "API名称"=? AND ("api首次出现版本" IS NULL OR "api首次出现版本"=\'\')',
                     [(version, name) for name in sorted(names)])
