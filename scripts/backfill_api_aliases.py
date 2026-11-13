"""Audit API object identity and backfill the final API真正名字 column."""
from pathlib import Path
import argparse
import collections
import hashlib
import importlib
import inspect
import json
import os
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data/api-support.sqlite3'
COLUMN = 'API真正名字'
SHORT_ROOTS = {
    'Tensor': 'torch.Tensor',
    'Function': 'torch.autograd.Function',
    'FunctionCtx': 'torch.autograd.function.FunctionCtx',
    'Node': 'torch.autograd.graph.Node',
    'Optimizer': 'torch.optim.Optimizer',
    'profile': 'torch.autograd.profiler.profile',
}
TENSOR_MEMBERS = {
    '__getstate__', '__setstate__', 'align_as', 'align_to', 'flatten',
    'names', 'refine_names', 'rename', 'rename_', 'to', 'to_sparse', 'view',
}


def read_database():
    with sqlite3.connect(DB.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        columns = [r[1] for r in conn.execute('PRAGMA table_info("API支持数据")')]
        rows = conn.execute('SELECT * FROM "API支持数据" ORDER BY "API名称"').fetchall()
    return columns, rows


def input_digest(columns, rows):
    payload = json.dumps([columns, rows], ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def full_name(name):
    first, sep, rest = name.partition('.')
    if first in SHORT_ROOTS:
        return SHORT_ROOTS[first] + (sep + rest if sep else '')
    if name in TENSOR_MEMBERS:
        return 'torch.Tensor.' + name
    return name


def audit(directory):
    os.environ['TORCH_DEVICE_BACKEND_AUTOLOAD'] = '0'
    sys.path.insert(0, str(ROOT / 'scripts'))
    import torch
    from scan_documented_apis import Resolver
    columns, rows = read_database()
    original_columns = [c for c in columns if c != COLUMN]
    original_rows = [[r[columns.index(c)] for c in original_columns] for r in rows]
    records = [dict(zip(columns, r)) for r in rows]
    resolver = Resolver()
    groups = {}
    results = []
    started = time.monotonic()
    for index, record in enumerate(records, 1):
        name = record['API名称']
        lookup = full_name(name)
        row = {'api': name, 'lookup': lookup, 'public': record['是否对外'] == '是'}
        if not row['public']:
            row.update(status='internal_ignored')
        elif not lookup.startswith('torch.'):
            row.update(status='outside_pytorch', reason='No pure PyTorch entry')
        else:
            try:
                resolution = resolver.resolve(lookup)
                row['status'] = resolution['status']
                if resolution['status'] == 'resolved':
                    obj = resolver.cache.get(lookup)
                    owner_name, _, attribute = lookup.rpartition('.')
                    owner = resolver.cache.get(owner_name)
                    if owner is None:
                        owner = resolver.import_cache.get(owner_name)
                    # Keep the declared classmethod descriptor, rather than
                    # merging different bindings that share an underlying func.
                    if inspect.isclass(owner):
                        descriptor = inspect.getattr_static(owner, attribute)
                        if isinstance(descriptor, classmethod):
                            obj = descriptor
                    if obj is None or isinstance(obj, (str, bytes, int, float, complex, bool)):
                        row.update(status='non_api_value', reason=type(obj).__name__)
                    else:
                        key = id(obj)
                        if key in groups:
                            assert groups[key]['object'] is obj
                        else:
                            groups[key] = {'object': obj, 'members': []}
                        groups[key]['members'].append(row)
                        row['object_kind'] = resolution['object_kind']
                else:
                    for key in ('error_type', 'message', 'failed_path', 'field_evidence'):
                        if key in resolution:
                            row[key] = resolution[key]
            except (Exception, SystemExit) as exc:
                row.update(status='lookup_error', error_type=type(exc).__name__, message=str(exc))
        results.append(row)
        if index % 250 == 0 or index == len(records):
            print(json.dumps({'processed': index, 'total': len(records),
                              'seconds': round(time.monotonic()-started, 1)}, ensure_ascii=False), flush=True)
    aliases = {}
    duplicate_groups = []
    for group in groups.values():
        members = group['members']
        public = [r for r in members if r['public']]
        if not public:
            continue
        representative = min(public, key=lambda r: (len(r['lookup']), r['lookup'], r['api']))
        canonical = representative['api']
        for member in members:
            if member['api'] != canonical:
                aliases[member['api']] = canonical
        if len(members) > 1:
            duplicate_groups.append({'canonical': canonical, 'members': [r['api'] for r in members]})
    summary = {
        'rows': len(rows), 'resolved': sum(r['status'] == 'resolved' for r in results),
        'status_counts': dict(collections.Counter(r['status'] for r in results)),
        'public_status_counts': dict(collections.Counter(r['status'] for r in results if r['public'])),
        'duplicate_groups': len(duplicate_groups), 'alias_rows': len(aliases),
        'torch_version': torch.__version__, 'torch_commit': torch.version.git_version,
        'python': sys.executable,
    }
    report = {'summary': summary, 'aliases': aliases, 'groups': duplicate_groups,
              'results': results, 'original_columns': original_columns,
              'original_digest': input_digest(original_columns, original_rows)}
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'identity-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('SUMMARY ' + json.dumps(summary, ensure_ascii=False), flush=True)


def apply(directory):
    report = json.loads((directory / 'identity-audit.json').read_text())
    columns, rows = read_database()
    original_columns = report['original_columns']
    actual_rows = [[r[columns.index(c)] for c in original_columns] for r in rows]
    assert input_digest(original_columns, actual_rows) == report['original_digest']
    names = {r[columns.index('API名称')] for r in rows}
    public_index = columns.index('是否对外')
    public = {r[columns.index('API名称')] for r in rows if r[public_index] == '是'}
    aliases = {name: target for name, target in report['aliases'].items()
               if name in public and target in public}
    assert all(a in names and b in names and a != b and b not in aliases for a, b in aliases.items())
    with sqlite3.connect(DB, isolation_level=None, timeout=30) as conn:
        backup = sqlite3.connect(directory / 'api-support.before.sqlite3')
        conn.backup(backup)
        backup.close()
        conn.execute('BEGIN IMMEDIATE')
        try:
            current_columns = [r[1] for r in conn.execute('PRAGMA table_info("API支持数据")')]
            current_rows = conn.execute('SELECT * FROM "API支持数据" ORDER BY "API名称"').fetchall()
            original_current = [[r[current_columns.index(c)] for c in original_columns] for r in current_rows]
            assert input_digest(original_columns, original_current) == report['original_digest']
            if COLUMN not in current_columns:
                conn.execute('ALTER TABLE "API支持数据" ADD COLUMN "API真正名字" TEXT')
            else:
                assert current_columns[-1] == COLUMN
            conn.execute('UPDATE "API支持数据" SET "API真正名字"=NULL WHERE "是否对外"=\'是\'')
            conn.executemany('UPDATE "API支持数据" SET "API真正名字"=? WHERE "API名称"=?',
                             [(canonical, name) for name, canonical in aliases.items()])
            updated_columns = [r[1] for r in conn.execute('PRAGMA table_info("API支持数据")')]
            updated_rows = conn.execute('SELECT * FROM "API支持数据" ORDER BY "API名称"').fetchall()
            assert updated_columns == original_columns + [COLUMN]
            assert [tuple(r[:-1]) for r in updated_rows] == [tuple(r) for r in actual_rows]
            public_index = updated_columns.index('是否对外')
            actual_aliases = {r[0]: r[-1] for r in updated_rows
                              if r[-1] is not None and r[public_index] == '是'}
            assert actual_aliases == aliases
            assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    verification = dict(report['summary'], alias_rows=len(aliases), last_column=COLUMN,
                        preserved_original_rows_and_fields=True, integrity_check='ok',
                        canonical_and_unconfirmed_rows_null=len(rows)-len(aliases),
                        database_sha256=hashlib.sha256(DB.read_bytes()).hexdigest())
    (directory / 'verification.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2))
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['audit', 'apply', 'all'])
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    if args.mode == 'all':
        audit(args.directory)
        apply(args.directory)
    else:
        (audit if args.mode == 'audit' else apply)(args.directory)
