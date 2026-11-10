#!/usr/bin/env python3
"""Resolve adaptation task identifiers to one development branch and merge its existing work."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from preparation_state import locked, write_json

ROOT = Path(__file__).resolve().parents[1]
PENDING = {'PREPARING', 'MERGE_CONFLICT', 'MERGE_PENDING', 'FAILED'}
READY = {'BRANCH_READY', 'EXISTING_BRANCHES_READY'}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_object(path):
    result = json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_object)
    if not isinstance(result, dict):
        raise ValueError(str(path) + ' must contain a JSON object')
    return result


def git(repo, *args, check=True):
    result = subprocess.run(
        ['git', '-C', str(repo), '-c', 'merge.autoStash=false', *args],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        env=dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_MERGE_AUTOEDIT='no'), timeout=900)
    if check and result.returncode:
        raise RuntimeError('git ' + ' '.join(args) + ': ' + (result.stderr or result.stdout).strip()[-3000:])
    return result


def refs(repo):
    prefixes = ['refs/heads/', 'refs/remotes/upstream/']
    output = git(repo, 'for-each-ref', '--format=%(refname) %(objectname)',
                 *prefixes).stdout
    return dict(line.split(' ', 1) for line in output.splitlines())


def has_local_branch(available, branch):
    return 'refs/heads/' + branch in available


def ancestor(repo, commit):
    result = git(repo, 'merge-base', '--is-ancestor', commit, 'HEAD', check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or 'Cannot check commit ancestry')
    return result.returncode == 0


def current_branch(repo):
    result = git(repo, 'symbolic-ref', '--quiet', '--short', 'HEAD', check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def operation_paths(repo):
    directory = Path(git(repo, 'rev-parse', '--absolute-git-dir').stdout.strip())
    return {name for name in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD',
                             'rebase-merge', 'rebase-apply', 'sequencer')
            if (directory / name).exists()}


def require_clean(repo):
    if git(repo, 'status', '--porcelain', '--untracked-files=all').stdout:
        raise ValueError('Source has uncommitted/untracked changes; preserve and finish them first')


def check_master(repo, baseline):
    current = refs(repo).get('refs/heads/master')
    if current and current != baseline:
        result = git(repo, 'merge-base', '--is-ancestor', current, baseline, check=False)
        if result.returncode:
            raise ValueError('Local master is ahead of or diverged from upstream/master; preserving local commits')
    return current


def sync_master(repo, baseline):
    if check_master(repo, baseline) != baseline:
        # The target development branch is already checked out. This only
        # fast-forwards master and also respects other worktrees using master.
        git(repo, 'branch', '--no-track', '-f', 'master', baseline)


def normalize_task_branches(value, *, allow_empty=False):
    # Legacy objects used task labels as keys; only their values identify work.
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError('task-branches must be a nonempty task-identifier array')
    if any(not isinstance(identifier, str) or not identifier.strip() for identifier in value):
        raise ValueError('task-branches requires nonempty task identifiers')
    if len(set(value)) != len(value):
        raise ValueError('task-branches must contain unique task identifiers')
    return list(value)


def validate_request(adapt):
    return normalize_task_branches(adapt.get('task-branches'))


def load_mapping(path, repo):
    mapping = read_object(path) if path.exists() else {}
    for alias, branch in mapping.items():
        if not alias.strip() or not isinstance(branch, str) or not branch.strip():
            raise ValueError('Invalid entry in branch-mapping.json: ' + alias)
    for branch in set(mapping.values()):
        if branch == 'master':
            raise ValueError('A task development branch cannot be master')
        git(repo, 'check-ref-format', '--branch', branch)
    return mapping


def plan(repo, request, mapping, available):
    master = available.get('refs/heads/master')
    if not master:
        raise ValueError('Local master is missing; finish Step 0 source preparation first')
    # Step 0 already updated master before checking out the wheel commit.
    # Compare against its cached upstream record without contacting the remote.
    prepared_master = available.get('refs/remotes/upstream/master')
    if prepared_master:
        check_master(repo, prepared_master)
    sources, existing, remaining = [], [], []
    for alias in request:
        branch = mapping.get(alias)
        if branch is not None:
            if not has_local_branch(available, branch):
                raise ValueError('Mapped development branch is missing: ' + branch)
            if branch not in sources:
                sources.append(branch)
            existing.append(alias)
        else:
            remaining.append(alias)
    aliases = sorted(set(request) | {alias for alias, branch in mapping.items() if branch in sources})
    if not remaining:
        return {
            'status': 'PREPARING', 'action': 'STOP', 'workflow-version': 2,
            'mode': 'EXISTING_ONLY', 'requested-branches': list(request),
            'existing-branches': existing, 'remaining-branches': [],
            'source-branches': sources, 'base-commit': master,
            'original-mappings': {alias: mapping.get(alias) for alias in aliases},
        }
    while True:
        target = 'adapt-set-' + uuid.uuid4().hex[:12]
        if not has_local_branch(available, target) and target not in mapping.values():
            break
    action = 'CREATE' if not sources else 'EXTEND'
    git(repo, 'check-ref-format', '--branch', target)
    return {
        'status': 'PREPARING', 'branch': target, 'action': action,
        'workflow-version': 2,
        'mode': 'HAS_NEW_TASKS' if remaining else 'EXISTING_ONLY',
        'requested-branches': list(request),
        'existing-branches': existing, 'remaining-branches': remaining,
        'source-branches': sources,
        'checkout-base': 'refs/heads/' + sources[0] if remaining and sources else 'refs/heads/master',
        'base-commit': master, 'original-mappings': {alias: mapping.get(alias) for alias in aliases},
        'checkout-done': False,
    }


def validate_mapping_snapshot(state, mapping):
    target = state['branch']
    for alias, original in state['original-mappings'].items():
        if mapping.get(alias) not in (original, target):
            raise ValueError('Task-to-development-branch mapping changed during preparation: ' + alias)
    inherited = {alias for alias, branch in mapping.items() if branch in state['source-branches']}
    if not inherited <= set(state['original-mappings']):
        raise ValueError('Source branch acquired new task mappings; finish or reconcile the pending preparation')


def save_state(path, adapt, state):
    adapt['git-control'] = state
    adapt.pop('development-branch', None)
    adapt.pop('adapt-plan', None)
    adapt.pop('dispatcher', None)
    write_json(path, adapt)


def pending_merge(repo, path, adapt, state):
    conflicts = git(repo, 'diff', '--name-only', '--diff-filter=U').stdout.splitlines()
    state['status'] = 'MERGE_CONFLICT' if conflicts else 'MERGE_PENDING'
    state['conflict-files'] = conflicts
    save_state(path, adapt, state)
    return {'status': state['status'], 'development-branch': state['branch'],
            'conflict-files': conflicts,
            'message': 'Resolve and complete the merge on this branch, then rerun the same command'}


def normalize_checkpoint(state):
    # Preserve checkpoints written before tasks were generalized beyond APIs.
    # The original mapping snapshot already contains the complete related scope.
    # Retire redundant task-list aliases from older checkpoints.
    state.pop('api-branches', None)
    state.pop('task-branches', None)
    state.pop('source-commits', None)
    state.pop('track-origin', None)
    if state.get('mode') == 'HAS_NEW_APIS':
        state['mode'] = 'HAS_NEW_TASKS'
    for field in ('dependency-tests', 'mapping-refreshed', 'mapping-refresh-plan', 'deferred-actions'):
        state.pop(field, None)
    for field in ('requested-branches', 'existing-branches', 'remaining-branches'):
        if field in state:
            state[field] = normalize_task_branches(state[field], allow_empty=True)
    sources = state['source-branches']
    state['checkout-base'] = ('refs/heads/' + sources[0]
                              if state['remaining-branches'] and sources else 'refs/heads/master')
    return state


def result_from_state(state):
    result = {
        'status': state['status'], 'stop-task': state['stop-task'],
        'task-branches': list(state['requested-branches'] if state['stop-task'] else state['remaining-branches']),
        'message': state['message'],
    }
    if not state['stop-task']:
        result['development-branch'] = state['branch']
    return result


def stop_existing_tasks(repo, path, adapt, state):
    if operation_paths(repo):
        raise ValueError('Finish the existing Git operation before ending this task')
    available = refs(repo)
    if any(not has_local_branch(available, source) for source in state['source-branches']):
        raise ValueError('A previously mapped local branch is missing')
    # Case one is a lookup-only exit: no development branch is selected.
    for key in ('branch', 'checkout-base', 'checkout-done', 'head-commit', 'error', 'conflict-files'):
        state.pop(key, None)
    state.update(
        status='EXISTING_BRANCHES_READY', action='STOP',
        **{'stop-task': True,
           'message': '全部任务已有对应本地分支，当前直接结束。未来再补充合并、测试检验、必要开发和映射刷新；本次均不执行。'})
    adapt['git-control'] = state
    adapt.pop('development-branch', None)
    adapt.pop('adapt-plan', None)
    adapt.pop('dispatcher', None)
    write_json(path, adapt)
    return result_from_state(state)


def execute(repo, path, adapt, state, mapping_path, mapping):
    if state['mode'] == 'EXISTING_ONLY':
        return stop_existing_tasks(repo, path, adapt, state)
    validate_mapping_snapshot(state, mapping)
    branch = state['branch']
    operations = operation_paths(repo)
    if operations - {'MERGE_HEAD'}:
        raise ValueError('An unrelated Git operation is in progress: ' + ', '.join(sorted(operations)))
    if 'MERGE_HEAD' in operations:
        if not state['checkout-done'] or current_branch(repo) != branch:
            raise ValueError('The pending merge does not belong to this preparation')
        return pending_merge(repo, path, adapt, state)
    require_clean(repo)
    check_master(repo, state['base-commit'])
    if state['checkout-done']:
        if current_branch(repo) != branch:
            raise ValueError('Pending preparation requires staying on branch ' + branch)
    else:
        available = refs(repo)
        if current_branch(repo) != branch:
            if 'refs/heads/' + branch in available:
                git(repo, 'checkout', branch)
            elif state['action'] == 'REUSE':
                raise ValueError('Mapped local development branch is missing: ' + branch)
            else:
                git(repo, 'checkout', '--no-track', '-b', branch, state['checkout-base'])
        state['checkout-done'] = True
        save_state(path, adapt, state)
    sync_master(repo, state['base-commit'])
    # One agent owns the repository and tasks are serial: local branches are
    # authoritative, so retries use their current local state directly.
    required_refs = ['refs/heads/master'] + ['refs/heads/' + source for source in state['source-branches']]
    for source_ref in required_refs:
        if ancestor(repo, source_ref):
            continue
        result = git(repo, 'merge', '--no-edit', source_ref, check=False)
        if result.returncode:
            if 'MERGE_HEAD' in operation_paths(repo):
                return pending_merge(repo, path, adapt, state)
            raise RuntimeError((result.stderr or result.stdout).strip()[-3000:])
    if operation_paths(repo):
        raise ValueError('Git operation is not complete')
    require_clean(repo)
    if current_branch(repo) != branch or not all(ancestor(repo, source_ref) for source_ref in required_refs):
        raise ValueError('Development branch does not include all required source commits')
    # Read again before publication; preserve unrelated task mappings.
    mapping = load_mapping(mapping_path, repo)
    validate_mapping_snapshot(state, mapping)
    state['stop-task'] = False
    mapping.update({alias: branch for alias in state['original-mappings']})
    write_json(mapping_path, mapping)
    # Keep the original selection so retry cannot misclassify newly assigned tasks.
    adapt['task-branches'] = list(state['remaining-branches'])
    state['status'] = 'BRANCH_READY'
    state['message'] = ('已准备本次开发分支，刷新映射并从task-branches移除原已对应项。'
                        '依赖分支PR中的测试按要求暂不执行，移除不代表测试通过；剩余项交给后续开发。')
    state['head-commit'] = git(repo, 'rev-parse', 'HEAD').stdout.strip()
    state.pop('error', None)
    state.pop('conflict-files', None)
    adapt['git-control'] = state
    adapt['development-branch'] = branch
    adapt.pop('adapt-plan', None)
    adapt.pop('dispatcher', None)
    write_json(path, adapt)
    return result_from_state(state)


def prepare(basic_information, *, root=ROOT):
    root = Path(root).resolve()
    basic_path = Path(basic_information)
    if not basic_path.is_absolute():
        basic_path = root / basic_path
    basic_path = basic_path.resolve()
    runs = root / 'data/run_data'
    if (basic_path.name != 'basic_information.json' or basic_path.parent == runs
            or not basic_path.is_relative_to(runs)):
        raise ValueError('basic_information.json must belong to data/run_data/<run-id>')
    basic = read_object(basic_path)
    source = basic.get('torch_npu_latest_source')
    if not isinstance(source, str) or not source:
        raise ValueError('basic_information.json requires torch_npu_latest_source')
    repo = (root / source).resolve()
    if repo != root / 'data/source_code/torch_npu' or not (repo / '.git').exists():
        raise ValueError('Use the existing fixed data/source_code/torch_npu repository from Step 0')
    path = basic_path.parent / 'adapt-information.json'
    mapping_path = root / 'data/branch-mapping.json'
    with locked(root / 'data/source_code/.torchnpu-source.lock'):
        if Path(git(repo, 'rev-parse', '--show-toplevel').stdout.strip()).resolve() != repo:
            raise ValueError('Source path is not the repository root')
        adapt = read_object(path)
        request = validate_request(adapt)
        adapt['task-branches'] = list(request)
        mapping = load_mapping(mapping_path, repo)
        previous = adapt.get('git-control')
        resumable = (isinstance(previous, dict) and previous.get('workflow-version') == 2
                     and previous.get('status') in PENDING | READY)
        if resumable:
            state = normalize_checkpoint(previous)
            if request not in (state['requested-branches'], state['remaining-branches']):
                raise ValueError('Task selection changed during a pending preparation; finish the original operation first')
        elif isinstance(previous, dict) and previous.get('status') in PENDING:
            raise ValueError('This pending preparation uses the previous workflow; preserve and reconcile it before restarting')
        else:
            if operation_paths(repo):
                raise ValueError('Finish the existing Git operation before preparing a new branch')
            require_clean(repo)
            state = plan(repo, request, mapping, refs(repo))
            save_state(path, adapt, state)
        try:
            return execute(repo, path, adapt, state, mapping_path, mapping)
        except Exception as error:
            state['status'] = 'FAILED'
            state['error'] = str(error)
            save_state(path, adapt, state)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--basic-information', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.basic_information)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] in READY else 2
    except Exception as error:
        print(json.dumps({'status': 'BRANCH_PREPARATION_FAILED', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
