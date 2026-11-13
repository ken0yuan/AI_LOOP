#!/usr/bin/env python3
"""Refresh the public API database from main docs using the Step 0 venv."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
from api_refresh_runtime import RefreshRecovery, WheelDependencyError
from api_first_seen import COLUMN as FIRST_SEEN_COLUMN, capture_document_version, read_document_version, stamp_new_apis

ROOT = Path(__file__).resolve().parents[1]
NORMAL_STATES = {'支持', '不支持', '未提供'}
OFFLINE_STATES = {'API删除，待下线'}
REMOVAL_MARKERS = OFFLINE_STATES | {'文档删除，待下线'}
ALIAS_COLUMN = 'API真正名字'
EXISTS = {'resolved', 'declared_field', 'documented_alias'}
MAIN = 'https://docs.pytorch.org/docs/main'
BUSINESS_COLUMNS = ('API名称', '是否支持', 'API等级', 'API适配分类', '是否对外',
                    '分类', '细分模块', '细分场景', '适配策略', '本地是否适配', '分类待审核')


def has_value(value):
    return isinstance(value, str) and bool(value.strip())


def missing_fields(row):
    return [column for column in BUSINESS_COLUMNS if not has_value(row.get(column))]


def allow_unclassified_entries(conn):
    """Allow new rows to await classification without changing stored values."""
    info = conn.execute('PRAGMA table_info("API支持数据")').fetchall()
    column = next(row for row in info if row[1] == 'API适配分类')
    if not column[3] and column[4] is None:
        return
    schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='API支持数据'").fetchone()[0]
    revised = re.sub(r'("API适配分类"\s+TEXT)(?:\s+NOT\s+NULL)?(?:\s+DEFAULT\s+\'框架类API\')?',
                     r'\1', schema, count=1, flags=re.IGNORECASE)
    if revised == schema:
        raise ValueError('Cannot remove the old API适配分类 default from the database schema')
    objects = conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name='API支持数据' "
                           "AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
    revised = re.sub(r'CREATE\s+TABLE\s+"API支持数据"',
                     'CREATE TABLE "__api_support_unclassified"', revised, count=1, flags=re.IGNORECASE)
    conn.execute(revised)
    columns = ','.join('"' + row[1] + '"' for row in info)
    conn.execute(f'INSERT INTO "__api_support_unclassified" ({columns}) SELECT {columns} FROM "API支持数据"')
    conn.execute('DROP TABLE "API支持数据"')
    conn.execute('ALTER TABLE "__api_support_unclassified" RENAME TO "API支持数据"')
    for (sql,) in objects:
        conn.execute(sql)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f'Cannot load existing tool: {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def resolve(path, root):
    path = Path(path)
    return Path(os.path.abspath(path if path.is_absolute() else root / path))


def names_from_document(data):
    """Read the crawler's module/name format, with no alias or name normalization."""
    if not isinstance(data, dict):
        raise ValueError('Documentation extractor must return its module-to-names object')
    names = []
    for module, values in data.items():
        if module in {'_signatures', '_sources'}:
            continue
        if not isinstance(values, list) or not all(isinstance(n, str) for n in values):
            raise ValueError(f'Malformed documentation names for {module}')
        # This is the original crawler's own module + short-name format.
        # In particular, do not remove .__init__. or add alternative spellings.
        names.extend(name if name.startswith(module) else f'{module}.{name}' for name in values)
    if not names:
        raise ValueError('Documentation extraction returned an empty API list')
    if len(names) != len(set(names)):
        raise ValueError('Original document output contains duplicate API names; refusing to change its count')
    return set(names)


def collect_main_apis(scripts, work, *, crawler=None):
    """Run the inventory-based extractor; preserve its module/name output format."""
    crawler_path = scripts / 'extract_docs_inventory.py'
    crawler = crawler or load_module(crawler_path, '_refresh_docs_crawler')
    if crawler.BASE_URL.format(version='main') != MAIN:
        raise ValueError('The documentation extractor must target official main documentation')
    output = work / 'docs-api.json'
    original_argv = sys.argv
    try:
        sys.argv = [str(crawler_path), 'main', str(output),
                    '--cache-dir', str(work / 'docs-cache')]
        with redirect_stdout(io.StringIO()), capture_document_version(crawler, work):
            crawler.main()
    finally:
        sys.argv = original_argv
    return names_from_document(read_json(output))


def database_snapshot(database):
    conn = sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True)
    try:
        return snapshot(conn)
    finally:
        conn.close()


def snapshot(conn):
    columns = [r[1] for r in conn.execute('PRAGMA table_info("API支持数据")')]
    if not {'API名称', '是否支持', 'API适配分类', '是否对外', '分类待审核'} <= set(columns):
        raise ValueError('Missing API支持数据 table or required columns')
    rows = conn.execute('SELECT * FROM "API支持数据" ORDER BY "API名称"').fetchall()
    if len({row[columns.index('API名称')] for row in rows}) != len(rows):
        raise ValueError('API名称 must be unique')
    return columns, rows


def canonical(name):
    # The established 2.14 baseline uses Tensor.* for torch.Tensor.* members.
    # Match these verified aliases without renaming the original primary keys.
    return 'torch.' + name if name == 'Tensor' or name.startswith('Tensor.') else name


def match_api_name(query, stored_names):
    """Match exact names first, then only the verified Tensor class/member aliases."""
    if query in stored_names:
        return query
    full = canonical(query)
    if full in stored_names:
        return full
    if full == 'torch.Tensor' or full.startswith('torch.Tensor.'):
        short = full[len('torch.'):]
        if short in stored_names:
            return short
    return None


def candidates(doc_names, state):
    columns, rows = state
    current = {row[columns.index('API名称')]: dict(zip(columns, row)) for row in rows}
    public = {name for name, row in current.items() if row['是否对外'] == '是'}
    # An internal row is matched only by its exact name. It is then promoted,
    # never inserted again; other internal rows are outside refresh scope.
    additions = {name for name in doc_names
                 if name not in current and match_api_name(name, public) is None}
    removals = {name for name, row in current.items()
                if row['是否对外'] == '是' and match_api_name(name, doc_names) is None}
    return additions, removals


def alias_updates(report, doc_to_row, results, current):
    """Use only checked public entries; internal objects never enter grouping."""
    updates = {row_name: None for row_name in doc_to_row.values()}
    inherited = {}
    for group in report.get('identity_groups', []):
        active = [name for name in group if name in doc_to_row and results[name]]
        if len(active) < 2:
            continue
        winner = min(active, key=lambda n: (len(canonical(n)), canonical(n), n))
        target = doc_to_row[winner]
        members = {doc_to_row[name] for name in active}
        for name in members:
            updates[name] = None if name == target else target
        # When a newly documented shorter name becomes the representative,
        # retain the existing object's business information on the new row.
        if target not in current or current[target]['是否对外'] != '是':
            existing = [name for name in members if name in current
                        and current[name]['是否对外'] == '是']
            if existing:
                inherited[target] = min(existing, key=lambda n: (
                    bool(current[n].get(ALIAS_COLUMN)), len(canonical(n)), canonical(n), n))
    # Redirect existing public references when their old representative becomes
    # an alias, including historical public spellings absent from today's docs.
    for name, row in current.items():
        old_target = row.get(ALIAS_COLUMN)
        if row['是否对外'] == '是' and name not in updates and updates.get(old_target):
            updates[name] = updates[old_target]
    return updates, inherited


def check_in_wheel(python, scanner, names, work, *, timeout=1800):
    if not names:
        return {}
    inventory = work / 'check-apis.jsonl'
    output = work / 'wheel-api-audit.json'
    inventory.write_text(''.join(json.dumps({'name': name, 'role': 'py:object', 'url': ''},
                                            ensure_ascii=False) + '\n' for name in sorted(names)),
                         encoding='utf-8')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONSAFEPATH='1',
               TORCH_DEVICE_BACKEND_AUTOLOAD='0')
    # Name lookup must use the installed wheel, not a source tree on PYTHONPATH.
    env.pop('PYTHONPATH', None)
    result = subprocess.run([str(python), '-B', str(scanner), '--inventory', str(inventory),
                             '--output', str(output)], cwd=work, env=env, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if result.returncode:
        raise RuntimeError('Wheel checker failed: ' + (result.stderr or result.stdout)[-2000:])
    report = read_json(output)
    rows = report['rows']
    if len(rows) != len(names) or {row['name'] for row in rows} != names:
        raise ValueError('Wheel checker results do not correspond one-to-one to the requested names')
    actual = Path(report['provenance']['python_executable']).absolute()
    if actual != python.absolute():
        raise ValueError('Wheel checker did not use basic_information.venv')
    checked = {}
    for row in rows:
        if row['status'] in EXISTS:
            checked[row['name']] = True
        elif row['status'] == 'unresolved' and row.get('error_type') == 'AttributeError':
            checked[row['name']] = False
        elif row['status'] == 'missing_dependency':
            raise WheelDependencyError(report, EXISTS)
        else:
            raise RuntimeError(f'Wheel lookup failed for {row["name"]}: '
                               f'{row["status"]}: {row.get("message", "")}')
    return checked


def stage_output(path, content):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.new-apis-', suffix='.tmp')
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def publish(database, initial_state, additions, changes, output, *, first_version=None,
            promotions=(), aliases=None, ready=None, inherited=None):
    """Write only names/statuses, preserving the rest of each stored row."""
    output.parent.mkdir(parents=True, exist_ok=True)
    old_output = output.read_bytes() if output.exists() else None
    previous = json.loads(old_output) if old_output is not None else []
    if not isinstance(previous, list) or not all(isinstance(n, str) for n in previous):
        raise ValueError('Existing new-apis.json must be an API name array')
    columns, rows = initial_state
    old_public = {row[columns.index('API名称')] for row in rows
                  if row[columns.index('是否对外')] == '是'}
    if not set(previous) <= old_public:
        raise ValueError('Existing handoff contains names not present as public APIs in the database')
    # Re-running keeps unfinished work, then checks the final representative.
    aliases = aliases or {}
    inherited = inherited or {}
    eligible = set(additions) if ready is None else set(ready)
    temporary = None
    replaced = False
    conn = None
    try:
        conn = sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True,
                               timeout=30, isolation_level=None)
        conn.execute('BEGIN IMMEDIATE')
        if snapshot(conn) != initial_state:
            raise RuntimeError('Database changed during refresh; retry without overwriting concurrent edits')
        if additions:
            allow_unclassified_entries(conn)
        for name in sorted(additions):
            conn.execute('INSERT INTO "API支持数据" ("API名称","API适配分类","是否对外","分类待审核") '
                         'VALUES (?, ?, ?, ?)', (name, None, '是', '是'))
        for name in promotions:
            conn.execute('UPDATE "API支持数据" SET "是否对外"=\'是\' WHERE "API名称"=?', (name,))
        business = [column for column in columns if column not in
                    {'API名称', '是否对外', FIRST_SEEN_COLUMN, ALIAS_COLUMN}]
        original = {row[columns.index('API名称')]: dict(zip(columns, row)) for row in rows}
        for name, source in inherited.items():
            values = {c: original[source][c] for c in business
                      if has_value(original[source][c]) and not has_value(original.get(name, {}).get(c))}
            if values.get('是否支持') in REMOVAL_MARKERS:
                values['是否支持'] = '未提供'
            if values:
                assignments = ','.join('"' + c + '"=?' for c in values)
                conn.execute('UPDATE "API支持数据" SET ' + assignments + ' WHERE "API名称"=?',
                             [*values.values(), name])
        stamp_new_apis(conn, additions, first_version)
        for name, status in sorted(changes.items()):
            conn.execute('UPDATE "API支持数据" SET "是否支持"=? WHERE "API名称"=?', (status, name))
        if ALIAS_COLUMN in columns:
            conn.executemany('UPDATE "API支持数据" SET "API真正名字"=? WHERE "API名称"=?',
                             [(target, name) for name, target in aliases.items()])
        final_columns, final_rows = snapshot(conn)
        final = {row[final_columns.index('API名称')]: dict(zip(final_columns, row)) for row in final_rows}
        handoff = set()
        for name in set(previous) | eligible:
            row = final.get(name)
            if not row or row['是否对外'] != '是' or row['是否支持'] in OFFLINE_STATES:
                continue
            target = row.get(ALIAS_COLUMN) or name
            representative = final.get(target)
            if (representative and representative['是否对外'] == '是'
                    and representative['是否支持'] not in OFFLINE_STATES
                    and missing_fields(representative)):
                handoff.add(target)
        handoff = sorted(handoff)
        content = (json.dumps(handoff, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        temporary = stage_output(output, content)
        os.replace(temporary, output)
        replaced = True
        conn.commit()
    except BaseException:
        if conn is not None:
            conn.rollback()
        if replaced:
            if old_output is None:
                output.unlink(missing_ok=True)
            else:
                restored = stage_output(output, old_output)
                try:
                    os.replace(restored, output)
                finally:
                    restored.unlink(missing_ok=True)
        raise
    finally:
        if conn is not None:
            conn.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return handoff


def refresh(basic_information, database=None, new_apis=None, *, root=ROOT,
            extractor=None, checker=None, timeout=1800):
    root = Path(root).resolve()
    basic_information = resolve(basic_information, root)
    basic = read_json(basic_information)
    if not isinstance(basic.get('venv'), str) or not basic['venv'].strip():
        raise ValueError('basic_information.json must contain the Step 0 venv path')
    venv = resolve(basic['venv'], root)
    python = venv / 'bin/python'
    if not python.is_file():
        raise ValueError(f'Step 0 venv interpreter missing: {python}')
    database = resolve(database or 'data/api-support.sqlite3', root)
    output = resolve(new_apis, root) if new_apis else basic_information.parent / 'api-refresh/new-apis.json'
    if not database.is_file():
        raise ValueError(f'Existing API database missing: {database}')
    if output.resolve() == database.resolve() or output.resolve() == basic_information.resolve():
        raise ValueError('Output must not overwrite the database or basic_information')
    if not output.resolve().is_relative_to(basic_information.parent.resolve()):
        raise ValueError('new-apis output must be inside this task directory')
    scripts = root / 'scripts'
    with RefreshRecovery(basic_information, scripts, python, root, timeout, MAIN) as recovery:
        work = recovery.work
        doc_names = recovery.documents(lambda: extractor(work) if extractor else collect_main_apis(scripts, work))
        if not isinstance(doc_names, set) or not doc_names:
            raise ValueError('Expected a non-empty extracted API name set')
        state = database_snapshot(database)
        columns, rows = state
        current = {row[columns.index('API名称')]: dict(zip(columns, row)) for row in rows}
        public = {name for name, row in current.items() if row['是否对外'] == '是'}
        additions, removals = candidates(doc_names, state)
        promotions = {name for name in doc_names if name in current
                      and current[name]['是否对外'] != '是'}
        doc_to_row = {name: name if name in current else
                      (match_api_name(name, public) or name) for name in doc_names}
        required = doc_names | {canonical(name) for name in removals}
        results = (checker(python, scripts / 'scan_documented_apis.py', required, work)
                   if checker else recovery.check(check_in_wheel, required))
        if set(results) != required or any(type(value) is not bool for value in results.values()):
            raise ValueError('Incomplete wheel existence results')
        inserted = additions
        changes = {name: '文档删除，待下线' if results[canonical(name)] else 'API删除，待下线'
                   for name in removals}
        for name, row_name in doc_to_row.items():
            if not results[name]:
                changes[row_name] = 'API删除，待下线'
            elif row_name in current and current[row_name]['是否支持'] in REMOVAL_MARKERS:
                changes[row_name] = '未提供'
        report_path = work / 'wheel-api-audit.json'
        report = read_json(report_path) if report_path.is_file() else {}
        # Dependency recovery may have checked subsets in separate processes.
        # Regroup the complete public input in one process after recovery.
        if checker is None and {r['name'] for r in report.get('rows', [])} != required:
            repeated = check_in_wheel(python, scripts / 'scan_documented_apis.py', required,
                                      work, timeout=timeout)
            if repeated != results:
                raise RuntimeError('Wheel API exposure changed during refresh; retry')
            report = read_json(report_path)
        wheel_to_row = {canonical(name): name for name in public}
        wheel_to_row.update(doc_to_row)
        aliases, inherited = alias_updates(report, wheel_to_row, results, current)
        ready = {row_name for name, row_name in doc_to_row.items() if results[name]
                 and (row_name in inserted or row_name in promotions
                      or current.get(row_name, {}).get('是否支持') in REMOVAL_MARKERS
                      or (current.get(row_name, {}).get(ALIAS_COLUMN)
                          and aliases.get(row_name) is None))}
        first_version = read_document_version(work) if inserted and FIRST_SEEN_COLUMN in state[0] else None
        handoff = publish(database, state, inserted, changes, output, first_version=first_version,
                          promotions=promotions, aliases=aliases, ready=ready, inherited=inherited)
        recovery.complete(results)
    return {'status': 'DATABASE_REFRESHED', 'documented': len(doc_names), 'checked': len(required),
            'added': len(inserted), 'promoted': len(promotions),
            'aliases': sum(target is not None for target in aliases.values()),
            'api_deleted': sum(value == 'API删除，待下线' for value in changes.values()),
            'documentation_deleted': sum(value == '文档删除，待下线' for value in changes.values()),
            'handoff_count': len(handoff), 'new_apis': str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--basic-information', required=True, type=Path)
    parser.add_argument('--database', type=Path, default=Path('data/api-support.sqlite3'))
    parser.add_argument('--new-apis', type=Path, help='Default: task/api-refresh/new-apis.json')
    parser.add_argument('--timeout', type=int, default=1800, help='Wheel checker timeout in seconds')
    args = parser.parse_args()
    try:
        if args.timeout <= 0:
            raise ValueError('--timeout must be positive')
        result = refresh(args.basic_information, args.database, args.new_apis, timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (Exception, SystemExit) as error:
        print('REFRESH_FAILED: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
