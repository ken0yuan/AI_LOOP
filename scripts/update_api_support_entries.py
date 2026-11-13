#!/usr/bin/env python3
"""Fill missing API fields from eleven-column JSONL and current-run reasons."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

from refresh_api_support_db import ROOT, BUSINESS_COLUMNS, has_value as text, read_json, resolve, stage_output

COLUMNS = BUSINESS_COLUMNS
DEFAULTS = {'是否支持': '未提供', 'API等级': 'L2',
            '是否对外': '是', '适配策略': '补充testcase', '本地是否适配': '是', '分类待审核': '是'}
CLASSIFICATION = ('分类', '细分模块', '细分场景')
STAGES = ('category', 'module', 'scenario')
REASONS = ('category_reason', 'module_reason', 'scenario_reason')
TYPES = {'框架类API', '算子类API', '专项适配API'}
PLACEHOLDERS = {'未知', '待分类', '待确定', '待补充', '待审核', 'null', 'none'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def definition_key(record):
    required = {'kind', 'level', 'parent', 'name', 'definition', 'apply_when',
                'do_not_apply_when', 'example_apis', 'why_existing_does_not_fit',
                'review_status', 'reference_file'}
    require(isinstance(record, dict) and set(record) == required and record['kind'] == 'definition',
            'Invalid candidate definition fields')
    require(record['level'] in STAGES and isinstance(record['parent'], list)
            and len(record['parent']) == STAGES.index(record['level'])
            and all(text(p) for p in record['parent']), 'Invalid candidate definition parent')
    for key in ('name', 'definition', 'why_existing_does_not_fit', 'reference_file'):
        require(text(record[key]), f'Missing candidate definition {key}')
    for key in ('apply_when', 'do_not_apply_when', 'example_apis'):
        value = record[key]
        require(text(value) or isinstance(value, list) and value and all(text(v) for v in value),
                f'Missing candidate definition {key}')
    require(record['review_status'] in {'待审核', '已审核'}, 'Invalid candidate review status')
    return record['level'], tuple(record['parent']), record['name']


def classification_evidence(path):
    """Read unique API reasons and unique definition records from one shared file."""
    result, definitions = {}, {}
    for record in jsonl(path):
        if isinstance(record, dict) and record.get('kind') == 'definition':
            key = definition_key(record)
            require(key not in definitions, f'Duplicate candidate definition: {key}')
            definitions[key] = record
            continue
        required = {'api', *REASONS}
        require(isinstance(record, dict) and required <= set(record)
                and set(record) <= required | {'new_label_reasons'}, 'Invalid shared classification reason fields')
        require(text(record['api']), 'Shared reason needs an exact API name')
        require(record['api'] not in result, f'Duplicate shared explanation: {record["api"]}')
        for field in REASONS:
            require(text(record[field]) and len(record[field]) <= 100,
                    f'{record["api"]}: {field} must be a short reason, at most 100 characters')
        new = record.get('new_label_reasons', {})
        require(isinstance(new, dict) and set(new) <= set(STAGES)
                and all(text(reason) and len(reason) <= 160 for reason in new.values()),
                f'{record["api"]}: invalid new-label gap explanations')
        result[record['api']] = record
    return result, definitions


def shared_reasons(path):
    return classification_evidence(path)[0]


def save_reasons(records, *, basic_information, root=ROOT):
    """Upsert the AI's short reasons without creating a separate input file."""
    save_evidence(records, [], basic_information=basic_information, root=root)


def save_definitions(records, *, basic_information, root=ROOT):
    """Store candidate definitions in classification-evidence.jsonl, never references."""
    require(isinstance(records, list), 'Definition updates must be an array')
    save_evidence([], [dict(record, kind='definition') for record in records],
                  basic_information=basic_information, root=root)


def save_evidence(records, definitions, *, basic_information, root=ROOT):
    root = Path(root).resolve()
    require(isinstance(records, list), 'Reason updates must be an array')
    if not records and not definitions:
        return
    database = root / 'data/api-support.sqlite3'
    conn = sqlite3.connect(database.as_uri() + '?mode=rw', uri=True, isolation_level=None)
    temporary = None
    try:
        conn.execute('BEGIN IMMEDIATE')
        path = resolve(basic_information, root).parent / 'api-classification/classification-evidence.jsonl'
        existing, existing_definitions = classification_evidence(path)
        # Reuse the reader's validation before publishing any update.
        names = []
        for record in records:
            require(isinstance(record, dict) and text(record.get('api')), 'Invalid reason update')
            names.append(record['api'])
        require(len(names) == len(set(names)), 'Duplicate API in reason updates')
        keys = [definition_key(record) for record in definitions]
        require(len(keys) == len(set(keys)), 'Duplicate definition in updates')
        path.parent.mkdir(parents=True, exist_ok=True)
        existing.update({record['api']: record for record in records})
        existing_definitions.update(zip(keys, definitions))
        combined = [existing_definitions[k] for k in sorted(existing_definitions)] + [existing[n] for n in sorted(existing)]
        content = ''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in combined).encode('utf-8')
        temporary = stage_output(path, content)
        classification_evidence(temporary)
        os.replace(temporary, path)
        # No DB changes here: the transaction is only the shared writer lock.
    finally:
        conn.rollback()
        conn.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def initial_adaptation_type(fields):
    return ('算子类API' if fields['分类'] == '基础计算' and fields['细分模块'] == '基础计算'
            else '框架类API')


def validate_fields(fields, original):
    name = original['API名称']
    require(isinstance(fields, dict) and set(fields) == set(COLUMNS),
            f'{name}: entry must contain exactly the eleven database columns')
    require(all(text(v) for v in fields.values()), f'{name}: empty database field')
    require(fields['API名称'] == name, f'{name}: primary key cannot change')
    classification_added = any(not text(original[c]) for c in (*CLASSIFICATION, 'API适配分类'))
    for column in COLUMNS[1:]:
        if column == '分类待审核' and classification_added:
            require(fields[column] == '是', f'{name}: completed classification needs review')
        elif text(original[column]):
            require(fields[column] == original[column], f'{name}: preserve {column}')
    for column, default in DEFAULTS.items():
        if column == '分类待审核' and classification_added:
            continue
        old = original[column]
        expected = old if text(old) else default
        require(fields[column] == expected, f'{name}: preserve {column}; missing default is {default}')
    require(fields['是否对外'] == '是', f'{name}: only public APIs belong to this step')
    require(fields['API适配分类'] in TYPES, f'{name}: invalid API适配分类')
    if not text(original['API适配分类']):
        expected = initial_adaptation_type(fields)
        require(fields['API适配分类'] == expected, f'{name}: API适配分类 must follow 分类 and 细分模块')
    for column in CLASSIFICATION:
        require(fields[column].strip().lower() not in PLACEHOLDERS, f'{name}: placeholder in {column}')
    for column in ('本地是否适配', '分类待审核'):
        require(fields[column] in {'是', '否'}, f'{name}: invalid {column}')


def validate_new_labels(fields, reason, proposals, root):
    """Check registration and review flags; AI judges the actual responsibility gap."""
    path = [fields[c] for c in CLASSIFICATION]
    explanations = reason.get('new_label_reasons', {})
    for index, level in enumerate(STAGES):
        matches = [p for p in proposals if isinstance(p, dict) and p.get('level') == level
                   and p.get('parent') == path[:index] and p.get('name') == path[index]]
        require(len(matches) <= 1, f'{fields["API名称"]}: duplicate taxonomy proposal')
        if matches:
            require(matches[0].get('review_status') in {'待审核', '已审核'},
                    f'{fields["API名称"]}: invalid proposal review status')
        pending = bool(matches) and matches[0]['review_status'] == '待审核'
        if level in explanations or matches:
            require(bool(matches), f'{fields["API名称"]}: new label not registered')
            proposal = matches[0]
            for key in ('definition', 'why_existing_does_not_fit'):
                require(text(proposal.get(key)), f'{fields["API名称"]}: missing proposal {key}')
            for key in ('apply_when', 'do_not_apply_when', 'example_apis'):
                value = proposal.get(key)
                require(text(value) or isinstance(value, list) and value and all(text(v) for v in value),
                        f'{fields["API名称"]}: missing proposal {key}')
            ref = proposal.get('reference_file')
            allowed = {'references/api-classification/' + n + '.md' for n in ('core','compute','tools','distributed','graph')}
            allowed.add('references/api-classification/rules.md')
            require(isinstance(ref, str) and ref in allowed and (root / ref).resolve().is_relative_to(root)
                    and (root / ref).is_file(), f'{fields["API名称"]}: invalid proposal target reference')
            if not pending:
                require(path[index] in (root / ref).read_text(encoding='utf-8'),
                        f'{fields["API名称"]}: approved label not synchronized to reference')
        if pending:
            require(fields['分类待审核'] == '是', f'{fields["API名称"]}: pending label needs review')
            require(level in explanations, f'{fields["API名称"]}: explain why existing {level} definitions do not fit')


def complete(basic_information, database=None, new_apis=None, entries=None, *, root=ROOT):
    root = Path(root).resolve()
    basic_path = resolve(basic_information, root)
    basic = read_json(basic_path)
    require(isinstance(basic, dict) and text(basic.get('venv')), 'Step 0 venv is required')
    require((resolve(basic['venv'], root) / 'bin/python').is_file(), 'Step 0 venv interpreter missing')
    task = basic_path.parent.resolve()
    names_path = resolve(new_apis, root) if new_apis else task / 'api-refresh/new-apis.json'
    entries_path = resolve(entries, root) if entries else task / 'api-entries.jsonl'
    for p in (names_path, entries_path):
        require(p.resolve().is_relative_to(task), 'Inputs must be inside the current run directory')
    require(entries_path.resolve() not in {basic_path.resolve(), names_path.resolve()},
            'Temporary entries must not overwrite task inputs')
    names = read_json(names_path)
    require(isinstance(names, list) and all(text(n) for n in names) and len(names) == len(set(names)),
            'new-apis.json must contain unique exact API names')
    require(bool(names), 'No new APIs: stop after refresh; do not invoke entry completion')
    records = jsonl(entries_path)
    require(all(isinstance(r, dict) and set(r) == set(COLUMNS) and text(r.get('API名称')) for r in records),
            'Temporary entries must contain only the eleven database columns')
    by_name = {r['API名称']: r for r in records}
    require(len(by_name) == len(records) and set(by_name) <= set(names),
            'Entries must use unique exact names from new-apis.json')
    evidence_path = task / 'api-classification/classification-evidence.jsonl'
    reasons, definitions = classification_evidence(evidence_path)
    proposals = list(definitions.values())
    database = resolve(database or 'data/api-support.sqlite3', root)
    require(database.is_file(), 'Existing API database required')
    require(entries_path.resolve() != database.resolve(), 'Temporary entries cannot be the database')
    conn = sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('BEGIN IMMEDIATE')
        columns = {r[1] for r in conn.execute('PRAGMA table_info("API支持数据")')}
        require(set(COLUMNS) <= columns <= set(COLUMNS) | {'api首次出现版本', 'API真正名字'},
                'Expected eleven business columns and optional version/representative columns')
        updated = 0
        for name in names:
            row = conn.execute('SELECT * FROM "API支持数据" WHERE "API名称"=?', (name,)).fetchone()
            require(row is not None, f'{name}: not registered by refresh')
            require(row['是否对外'] == '是' and row['是否支持'] != 'API删除，待下线',
                    f'{name}: only available public APIs can be completed')
            require('API真正名字' not in columns or not row['API真正名字'],
                    f'{name}: complete the representative instead of an alias')
            original = {column: row[column] for column in COLUMNS}
            fields = dict(by_name.get(name, original))
            for column, default in DEFAULTS.items():
                if not text(fields[column]):
                    fields[column] = default
            if not text(original['API适配分类']):
                fields['API适配分类'] = initial_adaptation_type(fields)
            classification_added = any(not text(original[c]) for c in (*CLASSIFICATION, 'API适配分类'))
            if classification_added and (name not in by_name or not text(fields['分类待审核'])):
                fields['分类待审核'] = '是'
            validate_fields(fields, original)
            if any(not text(original[c]) for c in CLASSIFICATION):
                require(name in reasons, f'{name}: shared three-level reasons required')
                validate_new_labels(fields, reasons[name], proposals, root)
            changed = [c for c in COLUMNS[1:] if fields[c] != original[c]]
            if changed:
                assignments = ','.join('"' + c + '"=?' for c in changed)
                conn.execute('UPDATE "API支持数据" SET ' + assignments + ' WHERE "API名称"=?',
                             [fields[c] for c in changed] + [name])
                check = conn.execute('SELECT * FROM "API支持数据" WHERE "API名称"=?', (name,)).fetchone()
                require({column: check[column] for column in COLUMNS} == fields, f'{name}: database readback mismatch')
                updated += 1
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    entries_path.unlink(missing_ok=True)
    return {'status': 'API_ENTRIES_READY', 'completed': len(names), 'updated': updated,
            'reused': len(names) - updated, 'database': str(database), 'classification': str(evidence_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--basic-information', required=True, type=Path)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--new-apis', type=Path)
    parser.add_argument('--entries', type=Path, help='Eleven-column JSONL; removed on success')
    args = parser.parse_args()
    try:
        print(json.dumps(complete(args.basic_information, args.database, args.new_apis, args.entries), ensure_ascii=False))
        return 0
    except Exception as error:
        print('API_ENTRIES_FAILED: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
