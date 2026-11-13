#!/usr/bin/env python3
"""Synchronize merged PR results after Step 0 environment preparation."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from preparation_state import locked, write_json
from prepare_development_branch import read_object

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = {'补充testcase': '补充testcase', '待适配': '待适配', '支持': '已支持'}


def replace_text(path, text):
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(text)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def classification_closeout(conn, database, run, rows):
    """Prepare approved definitions and remove only this PR's API explanations."""
    root = database.parent.parent
    names = {row['API名称'] for row in rows if row['是否对外'] == '是'}
    paths = {(run / 'api-classification/classification-evidence.jsonl'),
             (database.parent / 'api-classification/classification-evidence.jsonl')}
    levels = ['category', 'module', 'scenario']
    classifications = [[row[key] for key in ['分类', '细分模块', '细分场景']]
                       for row in rows if row['API名称'] in names]
    references, evidence, removed = {}, {}, 0
    for path in sorted(paths):
        if not path.exists():
            continue
        records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        remaining = []
        for record in records:
            if record.get('kind') == 'definition':
                level = record.get('level')
                relevant = (level in levels and any(
                    classification[:levels.index(level)] == record.get('parent')
                    and classification[levels.index(level)] == record.get('name')
                    for classification in classifications))
                if relevant:
                    if record.get('review_status') != '已审核':
                        raise ValueError('新分类尚未批准，保留分类理由: ' + record['name'])
                    target = Path(record['reference_file'])
                    allowed = {'references/api-classification/' + name + '.md'
                               for name in ['rules', 'core', 'compute', 'tools', 'distributed', 'graph']}
                    if target.as_posix() not in allowed or not (root / target).is_file():
                        raise ValueError('新分类定义文件无效: ' + str(target))
                    target = root / target
                    text = references.get(target, target.read_text(encoding='utf-8'))
                    marker = '<!-- approved-classification: ' + json.dumps(
                        [level, record['parent'], record['name']], ensure_ascii=False) + ' -->'
                    if marker not in text and not (record['name'] in text and record['definition'] in text):
                        def display(value):
                            return '；'.join(value) if isinstance(value, list) else value
                        text = text.rstrip() + '\n\n' + marker + '\n### ' + record['name'] + '\n\n'
                        text += '- 层级：' + level + '\n- 父级：' + (' / '.join(record['parent']) or '顶层') + '\n'
                        for key, label in [('definition', '职责定义'), ('apply_when', '适用条件'),
                                           ('do_not_apply_when', '不适用条件'), ('example_apis', '示例API')]:
                            text += '- ' + label + '：' + display(record[key]) + '\n'
                    references[target] = text
                remaining.append(record)
                continue
            api = record.get('api')
            if api:
                try:
                    canonical = resolve_rows(conn, [api])[api]['API名称']
                except ValueError:
                    canonical = api
                if canonical in names:
                    removed += 1
                    continue
            remaining.append(record)
        if remaining != records:
            evidence[path] = ''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in remaining)
    return references, evidence, removed


def view_pr(record):
    command = ['gitcode', 'pr', 'view', str(record['number']), '-R', record['repository'], '--json']
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=90)
    try:
        value = json.loads(result.stdout)
    except ValueError as error:
        raise ValueError('PR状态查询失败: ' + record['repository'] + '#' + str(record['number'])) from error
    # The CLI can return useful PR data with a failed enrichment exit status.
    if not isinstance(value, dict) or 'merged' not in value or not value.get('head') or not value.get('base'):
        raise ValueError('PR状态响应不完整: ' + str(record['number']))
    return value


def resolve_rows(conn, names):
    rows = {}
    for name in names:
        row = conn.execute('SELECT * FROM "API支持数据" WHERE "API名称"=?', (name,)).fetchone()
        if row is None:
            alternate = ('torch.' + name if name == 'Tensor' or name.startswith('Tensor.')
                         else name[len('torch.'):] if name.startswith('torch.Tensor') else name)
            row = conn.execute('SELECT * FROM "API支持数据" WHERE "API名称"=?', (alternate,)).fetchone()
        if row is None:
            raise ValueError('PR状态对应的API未登记: ' + name)
        if row['API真正名字']:
            row = conn.execute('SELECT * FROM "API支持数据" WHERE "API名称"=?',
                               (row['API真正名字'],)).fetchone()
        if row is None or row['API真正名字']:
            raise ValueError('PR状态的正名指向无效: ' + name)
        rows[name] = row
    return rows


def finish_merged(database, mapping_path, record_path, record, remote):
    head, base = remote['head'], remote['base']
    branch = record['head']
    expected = record['commit']
    if (head['ref'] != branch or head['sha'] != expected
            or head['repo']['full_name'].lower() != record['fork'].lower()
            or base['repo']['full_name'].lower() != record['repository'].lower()):
        raise ValueError('PR来源、提交或目标与本地发布记录不一致')
    adapt_path = record_path.parent / 'adapt-information.json'
    statuses = record.get('api-status')
    if statuses is None and adapt_path.is_file():
        statuses = read_object(adapt_path).get('api-status')
    if not isinstance(statuses, dict) or not statuses:
        raise ValueError('已合入PR缺少逐API状态，不能回填或清理')
    if any(status not in STRATEGIES for status in statuses.values()):
        raise ValueError('PR包含无效API状态')
    with closing(sqlite3.connect(database.as_uri() + '?mode=rw', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN IMMEDIATE')
        rows = resolve_rows(conn, statuses)
        canonical_status = {}
        for name, row in rows.items():
            canonical = row['API名称']
            if canonical in canonical_status and canonical_status[canonical] != statuses[name]:
                raise ValueError('同一正名出现相互冲突的状态: ' + canonical)
            canonical_status[canonical] = statuses[name]
        references, evidence, reasons_removed = classification_closeout(
            conn, database, record_path.parent, list(rows.values()))
        for name, status in canonical_status.items():
            row = next(row for row in rows.values() if row['API名称'] == name)
            if row['是否对外'] != '是':
                continue
            if status == '支持' and row['是否支持'] != 'API删除，待下线':
                conn.execute('UPDATE "API支持数据" SET "适配策略"=?,"是否支持"=?,"分类待审核"=? WHERE "API名称"=?',
                             (STRATEGIES[status], '支持', '否', name))
            else:
                conn.execute('UPDATE "API支持数据" SET "适配策略"=?,"分类待审核"=? WHERE "API名称"=?',
                             (STRATEGIES[status], '否', name))
        conn.commit()
    for path, text in references.items():
        if path.read_text(encoding='utf-8') != text:
            replace_text(path, text)
    for path, text in evidence.items():
        if text:
            replace_text(path, text)
        else:
            path.unlink(missing_ok=True)
    latest_mapping = read_object(mapping_path) if mapping_path.exists() else {}
    completed_tasks = {'adapt-' + name for name in canonical_status} | {'adapt-' + name for name in statuses}
    removed = [task for task, value in latest_mapping.items()
               if value == branch and task in completed_tasks]
    for task in removed:
        del latest_mapping[task]
    write_json(mapping_path, latest_mapping)
    record_path.unlink(missing_ok=True)
    return {'pr': record['number'], 'branch': branch, 'api-status': canonical_status,
            'removed_tasks': removed, 'classification_reasons_removed': reasons_removed,
            'status': 'MERGED_PR_RECONCILED'}


def reconcile(basic_information, *, root=ROOT, viewer=view_pr):
    root = Path(root).resolve()
    basic_path = Path(basic_information)
    if not basic_path.is_absolute():
        basic_path = root / basic_path
    read_object(basic_path)
    database = root / 'data/api-support.sqlite3'
    mapping = root / 'data/branch-mapping.json'
    done, pending = [], []
    with locked(root / 'data/source_code/.torchnpu-source.lock'):
        for path in sorted((root / 'data/run_data').glob('*/pr.json')):
            record = read_object(path)
            if record.get('cleanup-completed') is True:
                continue
            try:
                remote = viewer(record)
                if remote.get('merged') is True:
                    done.append(finish_merged(database, mapping, path, record, remote))
            except (ValueError, KeyError, TypeError, OSError, RuntimeError, sqlite3.Error) as error:
                pending.append({'pr_record': str(path), 'reason': str(error)})
    # Upstream testcase matching is deferred; default to no matches.
    overlaps = []
    if pending:
        return {'status': 'PR_RECONCILIATION_INCOMPLETE', 'complete': False,
                'merged': done, 'pending': pending, 'upstream_test_overlaps': overlaps,
                'message': '先处理未完成的PR状态同步，再进入Step1。'}
    return {'status': 'PR_RECONCILIATION_READY', 'complete': True, 'merged': done,
            'pending': [], 'upstream_test_overlaps': []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--basic-information', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = reconcile(args.basic_information)
    except Exception as error:
        print(json.dumps({'status': 'PR_RECONCILIATION_FAILED', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['complete'] else 2


if __name__ == '__main__':
    sys.exit(main())
