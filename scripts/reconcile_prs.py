#!/usr/bin/env python3
"""Synchronize merged PR results and list open tracked PRs for review checks."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from preparation_state import locked, write_json, relative_path
from pr_tracking import file_lock, load_index, receipt_url
from prepare_development_branch import read_object
from branch_mapping import grouped_mapping
from refresh_api_support_db import NIGHTLY_TABLE

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = {'补充testcase': '补充testcase', '待适配': '待适配', '支持': '已支持'}
OPEN_STATES = {'open', 'opened'}


def replace_text(path, text):
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(text)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def classification_closeout(conn, database, rows, group, table):
    """Prepare approved definitions and remove only this PR's API explanations."""
    if group != 'nightly':  # Versioned tables already contain classified APIs.
        return {}, {}, 0
    root = database.parent.parent
    names = {row['API名称'] for row in rows if row['是否对外'] == '是'}
    paths = [database.parent / 'api-classification/classification-evidence.jsonl']
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
                    canonical = resolve_rows(conn, [api], table)[api]['API名称']
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


def open_review_entry(path, record, remote, root):
    """Describe an identity-verified open PR whose review comments need checking.

    Keep both SHAs so the agent can reconcile later changes before editing.
    """
    head, base = remote['head'], remote['base']
    if (int(remote['number']) != int(record['number'])
            or base['ref'] != record.get('base', 'master')
            or head['ref'] != record['head']
            or head['repo']['full_name'].lower() != record['fork'].lower()
            or base['repo']['full_name'].lower() != record['repository'].lower()):
        raise ValueError('开放PR来源或目标与本地发布记录不一致')
    commit = record.get('commit')
    return {'run_id': path.parent.name,
            'run_dir': relative_path(path.parent, root),
            'pr_record': relative_path(path, root),
            'pr': int(record['number']), 'repository': record['repository'],
            'fork': record['fork'], 'branch': head['ref'],
            'commit': commit, 'head_sha': head['sha'],
            'head_updated': bool(commit and head['sha'] != commit)}


def read_review_comments(path, *, root=ROOT):
    """Save one complete GitCode discussion snapshot in the PR's run directory."""
    path = (root / path).resolve()
    if path.name != 'pr.json' or path.parent.parent != (root / 'data/run_data').resolve():
        raise ValueError('采集评审需要本批 data/run_data/<run-id>/pr.json')
    record = read_object(path)
    remote = view_pr(record)
    if remote.get('merged') or remote.get('state') not in OPEN_STATES:
        return {'status': 'PR_NO_LONGER_OPEN', 'complete': True, 'review_file': None, 'candidate_count': 0}
    entry = open_review_entry(path, record, remote, root)
    ledger = path.with_name('review-handling.json')
    handling = read_object(ledger) if ledger.exists() else {}
    processed = {(str(item['id']), item['updated_at']) for item in handling.get('processed', [])}
    sent_replies = {str(item['reply_id']) for key in ('processed', 'needs-confirmation')
                    for item in handling.get(key, []) if item.get('reply_id') is not None}
    endpoint = f"repos/{entry['repository']}/pulls/{entry['pr']}/comments"
    page = 1
    discussions = []
    seen = set()
    while True:
        raw = subprocess.check_output(['gitcode', 'api', f'{endpoint}?page={page}&per_page=100&direction=asc'],
                                      encoding='utf-8', timeout=90)
        comments = json.loads(raw)
        if not isinstance(comments, list):
            raise ValueError('PR评论响应应为列表')
        new_ids = {str(note['id']) for note in comments} - seen
        if comments and not new_ids:
            raise ValueError('PR评论分页未前进，请重试')
        for note in comments:
            if str(note['id']) in seen:
                continue
            seen.add(str(note['id']))
            ids = []
            if note.get('resolved') is not True:
                ids = [msg['id'] for msg in (note, *(note.get('reply') or []))
                       if str(msg['id']) not in sent_replies
                       and (str(msg['id']), msg['updated_at']) not in processed]
            discussions.append({'candidate_ids': ids, 'discussion': note})
        if len(comments) < 100:
            break
        page += 1
    review_file = path.with_name('pr-review-comments.json')
    write_json(review_file, {**entry, 'pr_url': receipt_url(record), 'discussions': discussions})
    return {'status': 'PR_REVIEW_COMMENTS_READY', 'complete': True, **entry,
            'review_file': relative_path(review_file, root), 'discussion_count': len(discussions),
            'candidate_count': sum(len(thread['candidate_ids']) for thread in discussions)}


def resolve_rows(conn, names, table):
    rows = {}
    for name in names:
        row = conn.execute(f'SELECT * FROM "{table}" WHERE "API名称"=?', (name,)).fetchone()
        if row is None:
            alternate = ('torch.' + name if name == 'Tensor' or name.startswith('Tensor.')
                         else name[len('torch.'):] if name.startswith('torch.Tensor') else name)
            row = conn.execute(f'SELECT * FROM "{table}" WHERE "API名称"=?', (alternate,)).fetchone()
        if row is None:
            raise ValueError('PR状态对应的API未登记: ' + name)
        if row['API真正名字']:
            row = conn.execute(f'SELECT * FROM "{table}" WHERE "API名称"=?',
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
    versioned = re.fullmatch(r'adapt-(nightly|[0-9]+\.[0-9]+)-set-[0-9a-f]{12}', branch)
    group = versioned[1] if versioned else 'nightly'
    table = NIGHTLY_TABLE if group == 'nightly' else f'{NIGHTLY_TABLE}_{group}'
    adapt_path = record_path.parent / 'adapt-information.json'
    statuses = record.get('api-status')
    scope = read_object(adapt_path) if adapt_path.is_file() else {}
    if scope.get('withdrawing-api') or (statuses is None and scope.get('withdrawn-apis')):
        raise ValueError('撤项后需要核实本次合入的 PR 收据才能回填')
    if statuses is None and adapt_path.is_file():
        statuses = scope.get('api-status')
    if not isinstance(statuses, dict) or not statuses:
        raise ValueError('已合入PR缺少逐API状态，不能回填或清理')
    if any(status not in STRATEGIES for status in statuses.values()):
        raise ValueError('PR包含无效API状态')
    with closing(sqlite3.connect(database.as_uri() + '?mode=rw', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN IMMEDIATE')
        rows = resolve_rows(conn, statuses, table)
        canonical_status = {}
        for name, row in rows.items():
            canonical = row['API名称']
            if canonical in canonical_status and canonical_status[canonical] != statuses[name]:
                raise ValueError('同一正名出现相互冲突的状态: ' + canonical)
            canonical_status[canonical] = statuses[name]
        if set(canonical_status).intersection(scope.get('withdrawn-apis', [])):
            raise ValueError('发布收据仍涉及本批撤项，须核对实际合入范围后再回填')
        references, evidence, reasons_removed = classification_closeout(
            conn, database, list(rows.values()), group, table)
        for name, status in canonical_status.items():
            row = next(row for row in rows.values() if row['API名称'] == name)
            if row['是否对外'] != '是':
                continue
            if status == '支持' and row['是否支持'] != 'API删除，待下线':
                conn.execute(f'UPDATE "{table}" SET "适配策略"=?,"是否支持"=?,"分类待审核"=? WHERE "API名称"=?',
                             (STRATEGIES[status], '支持', '否', name))
            else:
                conn.execute(f'UPDATE "{table}" SET "适配策略"=?,"分类待审核"=? WHERE "API名称"=?',
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
    groups = grouped_mapping(read_object(mapping_path) if mapping_path.exists() else {})
    latest_mapping = groups.get(group, {})
    completed_tasks = {'adapt-' + name for name in canonical_status} | {'adapt-' + name for name in statuses}
    removed = [task for task, value in latest_mapping.items()
               if value == branch and task in completed_tasks]
    for task in removed:
        del latest_mapping[task]
    write_json(mapping_path, groups)
    write_json(record_path, dict(record, **{'cleanup-completed': True}))
    return {'pr': record['number'], 'branch': branch, 'api-status': canonical_status,
            'removed_tasks': removed, 'classification_reasons_removed': reasons_removed,
            'status': 'MERGED_PR_RECONCILED'}


def reconcile(*, root=ROOT, viewer=view_pr):
    root = Path(root).resolve()
    database = root / 'data/api-support.sqlite3'
    mapping = root / 'data/branch-mapping.json'
    done, pending, open_reviews = [], [], []
    data = root / 'data'
    with locked(data / 'source_code/.torchnpu-source.lock'), file_lock(data / '.pr-tracking.lock', wait=True):
        index = load_index(data)
        updated = [entry.copy() for entry in index.values()]
        for current in updated:
            if current['pr_status'] == 'merged':
                continue
            path = root / current['run_data'] / 'pr.json'
            try:
                record = read_object(path)
                if receipt_url(record).lower() != current['pr_url'].lower():
                    raise ValueError('PR索引与批次发布记录不一致')
                if record.get('cleanup-completed') is True:
                    current['pr_status'] = 'merged'
                    continue
                remote = viewer(record)
                if int(remote['number']) != int(record['number']):
                    raise ValueError('远端PR编号与发布记录不一致')
                if remote.get('merged') is True:
                    if current['pr_status'] != 'merged_pending':
                        current['pr_status'] = 'merged_pending'
                        write_json(data / 'pr-tracking.json', updated)
                    done.append(finish_merged(database, mapping, path, record, remote))
                    current['pr_status'] = 'merged'
                elif current['pr_status'] == 'merged_pending':
                    raise ValueError('已确认合入的PR与远端状态不一致')
                elif remote.get('state') in OPEN_STATES:
                    open_reviews.append(open_review_entry(path, record, remote, root))
                    current['pr_status'] = 'open'
                elif remote.get('state') == 'closed':
                    current['pr_status'] = 'closed'
                else:
                    raise ValueError('无法识别远端PR状态')
            except (ValueError, KeyError, TypeError, OSError, RuntimeError, sqlite3.Error) as error:
                pending.append({'pr_record': str(path), 'reason': str(error)})
        if updated != list(index.values()):
            write_json(data / 'pr-tracking.json', updated)
    # Upstream testcase matching is deferred; default to no matches.
    overlaps = []
    if pending:
        return {'status': 'PR_RECONCILIATION_INCOMPLETE', 'complete': False,
                'merged': done, 'pending': pending, 'open_pr_reviews': open_reviews,
                'upstream_test_overlaps': overlaps,
                'message': '部分PR状态同步未完成，详见pending。'}
    return {'status': 'PR_RECONCILIATION_READY', 'complete': True, 'merged': done,
            'pending': [], 'open_pr_reviews': open_reviews, 'upstream_test_overlaps': [],
            'message': 'PR状态同步完成。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-comments', type=Path, metavar='PR_JSON', help='将该 PR 的评审讨论保存到原批次目录')
    args = parser.parse_args()
    try:
        result = (read_review_comments(args.review_comments) if args.review_comments
                  else reconcile())
    except Exception as error:
        print(json.dumps({'status': 'PR_RECONCILIATION_FAILED', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['complete'] else 2


if __name__ == '__main__':
    sys.exit(main())
