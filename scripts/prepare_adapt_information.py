#!/usr/bin/env python3
"""Prepare an adaptation task identifier array from APIs in the problem description."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

from refresh_api_support_db import ROOT, match_api_name, read_json, resolve, stage_output

TOKENS = re.compile(r'(?<![A-Za-z0-9_.])[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*')
QUOTED_API = re.compile(r'`\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*`')


def extract_apis(description, rows):
    """Match complete identifiers, never a parent prefix of a longer API name."""
    description = re.sub(r'https?://[^\s`<>]+', ' ', description)
    explicit_bare = {m.group(1) for m in QUOTED_API.finditer(description)}
    api_label = re.compile(r'\bAPI\b\s*[:：]?\s*`?([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)', re.I)
    labelled = {m.group(1) for m in api_label.finditer(description)}
    explicit_bare.update(labelled)
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', description.strip()):
        explicit_bare.add(description.strip())
    names, missing = [], []
    for token in TOKENS.finditer(description):
        query = token.group()
        if '.' not in query and query not in explicit_bare:
            continue
        name = match_api_name(query, rows)
        if name is not None:
            if name not in names:
                names.append(name)
        elif query.startswith(('torch.', 'torch_npu.', 'Tensor.')) or query in labelled:
            if query not in missing:
                missing.append(query)
    if missing:
        raise ValueError('APIs not registered in database: ' + ', '.join(missing))
    if not names:
        raise ValueError('No registered API found in problem-description; use explicit full API names')
    return names


def prepare(basic_information, database=None, *, root=ROOT):
    root = Path(root).resolve()
    basic_path = resolve(basic_information, root)
    basic = read_json(basic_path)
    description = basic.get('problem-description') if isinstance(basic, dict) else None
    if not isinstance(description, str) or not description.strip():
        raise ValueError('basic_information.json requires a nonempty problem-description')
    database = resolve(database or 'data/api-support.sqlite3', root)
    if not database.is_file():
        raise ValueError(f'Existing API database missing: {database}')
    output = basic_path.parent / 'adapt-information.json'
    if output.resolve() in {database.resolve(), basic_path.resolve()} or output.resolve().parent != basic_path.parent.resolve():
        raise ValueError('adapt-information.json must be inside this run directory')
    conn = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        columns = {r[1] for r in conn.execute('PRAGMA table_info("API支持数据")')}
        if not {'API名称', '本地是否适配'} <= columns:
            raise ValueError('Database requires API名称 and 本地是否适配 columns')
        records = conn.execute('SELECT "API名称","本地是否适配" FROM "API支持数据"').fetchall()
    finally:
        conn.close()
    rows = dict(records)
    if len(rows) != len(records):
        raise ValueError('API名称 must be unique in the support database')
    names = extract_apis(description, rows)
    branches, skipped = [], []
    for name in names:
        if rows[name] == '是':
            branches.append('adapt-' + name)
        elif rows[name] == '否':
            skipped.append(name)
        else:
            raise ValueError(f'{name}: 本地是否适配 must be 是 or 否')
    if not branches:
        return {'status': 'NO_ADAPTATION_REQUIRED', 'matched': len(names), 'skipped_apis': skipped}
    result = read_json(output) if output.exists() else {}
    if not isinstance(result, dict):
        raise ValueError('Existing adapt-information.json must be a JSON object')
    previous = result.get('git-control')
    original_request = (previous.get('requested-branches') if isinstance(previous, dict)
                        and previous.get('workflow-version') == 2 else None)
    current_request = result.get('task-branches')
    if isinstance(current_request, dict):
        current_request = list(current_request.values())
    if isinstance(original_request, dict):
        original_request = list(original_request.values())
    if current_request != branches and original_request != branches:
        if isinstance(previous, dict) and previous.get('status') in {
                'PREPARING', 'MERGE_CONFLICT', 'MERGE_PENDING', 'FAILED'}:
            raise ValueError('Finish the pending Step 3 preparation before changing the API selection')
        result.pop('development-branch', None)
        result.pop('git-control', None)
        result.pop('adapt-plan', None)
        result.pop('dispatcher', None)
    result['task-branches'] = branches
    content = (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    temporary = stage_output(output, content)
    try:
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {'status': 'ADAPT_INFORMATION_READY', 'matched': len(names), 'branches': len(branches),
            'skipped_apis': skipped, 'output': str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--basic-information', required=True, type=Path)
    parser.add_argument('--database', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.basic_information, args.database), ensure_ascii=False))
        return 0
    except Exception as error:
        print('ADAPT_INFORMATION_FAILED: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
