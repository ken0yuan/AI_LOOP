#!/usr/bin/env python3
"""Add an adaptation plan to adapt-information.json without executing agents."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from preparation_state import locked, write_json
from prepare_development_branch import ROOT, read_object, git, current_branch, require_clean

API_TASK = re.compile(r'adapt-([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)', re.ASCII)


def validate_tasks(tasks):
    if not isinstance(tasks, list):
        raise ValueError('task-branches must be a task-identifier array produced by Step 3')
    if any(not isinstance(task, str) or not task.strip() for task in tasks):
        raise ValueError('task-branches requires nonempty task identifiers')
    if len(set(tasks)) != len(tasks):
        raise ValueError('task-branches must contain unique task identifiers')
    return list(tasks)


def dispatch(basic_information, adapt_information, *, root=ROOT):
    root = Path(root).resolve()
    basic_path = (root / basic_information).resolve()
    adapt_path = (root / adapt_information).resolve()
    runs = root / 'data/run_data'
    if (basic_path.name != 'basic_information.json' or basic_path.parent == runs
            or not basic_path.is_relative_to(runs)):
        raise ValueError('basic_information.json must belong to data/run_data/<run-id>')
    if adapt_path != basic_path.parent / 'adapt-information.json':
        raise ValueError('adapt-information.json must belong to the same run-id as basic_information.json')
    basic = read_object(basic_path)
    with locked(root / 'data/source_code/.torchnpu-source.lock'):
        adapt = read_object(adapt_path)
        # A failed replan must not leave an old plan available for execution.
        if 'adapt-plan' in adapt or 'dispatcher' in adapt:
            adapt.pop('adapt-plan', None)
            adapt.pop('dispatcher', None)
            write_json(adapt_path, adapt)
        control = adapt.get('git-control')
        if not isinstance(control, dict):
            raise ValueError('Step 3 git-control result is required before planning')
        if control.get('status') == 'EXISTING_BRANCHES_READY' and control.get('stop-task') is True:
            return {'status': 'DISPATCH_STOPPED', 'message': '全部任务已有对应本地分支，本次直接结束，不生成开发计划。'}
        if control.get('status') != 'BRANCH_READY' or control.get('stop-task') is not False:
            raise ValueError('Planning requires Step 3 BRANCH_READY with stop-task=false')
        tasks = validate_tasks(adapt.get('task-branches'))
        if tasks != control.get('remaining-branches'):
            raise ValueError('task-branches differs from the remaining tasks prepared by Step 3')
        if not tasks:
            return {'status': 'NO_TASKS_TO_DISPATCH', 'message': '没有剩余任务，不生成开发计划。'}
        unsupported = [task for task in tasks if API_TASK.fullmatch(task) is None]
        if unsupported:
            return {'status': 'DISPATCH_RULE_REQUIRED', 'unsupported-task-branches': unsupported,
                    'message': '当前仅支持整批adapt-<API>任务；需补充编排规则，本次不生成部分计划。'}
        branch = adapt.get('development-branch')
        if not isinstance(branch, str) or not branch or branch != control.get('branch'):
            raise ValueError('development-branch must match the prepared Step 3 branch')
        source = basic.get('torch_npu_latest_source')
        if not isinstance(source, str) or not source:
            raise ValueError('basic_information.json requires torch_npu_latest_source')
        repo = (root / source).resolve()
        if repo != root / 'data/source_code/torch_npu' or not (repo / '.git').exists():
            raise ValueError('Use the prepared data/source_code/torch_npu repository')
        if current_branch(repo) != branch:
            raise ValueError('Current checkout is not development-branch; do not plan for another branch')
        require_clean(repo)
        if git(repo, 'rev-parse', 'HEAD').stdout.strip() != control.get('head-commit'):
            raise ValueError('Development branch changed since Step 3; reconcile its prepared result first')
        adapt['adapt-plan'] = {'step1': {'agentname': 'api-adaptation', 'task-branches': tasks}}
        write_json(adapt_path, adapt)
        return {'status': 'DISPATCH_READY', 'message': '已写入adapt-plan；整批API任务由step1的一个API agent执行，尚未启动开发。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--basic-information', required=True, type=Path)
    parser.add_argument('--adapt-information', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = dispatch(args.basic_information, args.adapt_information)
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result['status'] == 'DISPATCH_RULE_REQUIRED' else 0
    except Exception as error:
        print(json.dumps({'status': 'DISPATCH_FAILED', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
