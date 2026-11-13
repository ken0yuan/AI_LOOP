"""Real-Git Step 3 checks against temporary local repositories; no network/NPU."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import prepare_development_branch as step3


class DevelopmentBranchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='test-step3-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.origin = self.root / 'origin.git'
        self.fork = self.root / 'fork.git'
        self.seed = self.root / 'seed'
        self.repo = self.root / 'data/source_code/torch_npu'
        self.repo.parent.mkdir(parents=True)
        self.g('init', '--bare', '--initial-branch=master', str(self.origin), repo=self.root)
        self.g('clone', str(self.origin), str(self.seed), repo=self.root)
        self.identity(self.seed)
        (self.seed / 'shared.txt').write_text('base\n')
        self.commit(self.seed, 'initial')
        self.g('push', 'origin', 'master', repo=self.seed)
        self.g('clone', '--bare', str(self.origin), str(self.fork), repo=self.root)
        self.g('clone', '--single-branch', '--origin', 'upstream', str(self.origin), str(self.repo), repo=self.root)
        self.g('remote', 'add', 'origin', str(self.fork))
        self.identity(self.repo)
        self.g('checkout', '--detach', repo=self.repo)
        self.mapping = self.root / 'data/branch-mapping.json'
        step3.write_json(self.mapping, {})

    def g(self, *args, repo=None):
        result = subprocess.run(['git', '-C', str(repo or self.repo), *args],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result.stdout.strip()

    def identity(self, repo):
        self.g('config', 'user.name', 'Step3 fixture', repo=repo)
        self.g('config', 'user.email', 'step3@example.invalid', repo=repo)
        self.g('config', 'commit.gpgsign', 'false', repo=repo)
        self.g('config', 'rerere.enabled', 'false', repo=repo)

    def commit(self, repo, message):
        self.g('add', '-A', repo=repo)
        self.g('commit', '-m', message, repo=repo)
        return self.g('rev-parse', 'HEAD', repo=repo)

    def task(self, *apis, run='one'):
        directory = self.root / 'data/run_data' / run
        directory.mkdir(parents=True, exist_ok=True)
        basic = directory / 'basic_information.json'
        step3.write_json(basic, {'torch_npu_latest_source': 'data/source_code/torch_npu',
                                 'torch_npu_commit': 'wheel-identity-must-stay'})
        step3.write_json(directory / 'adapt-information.json', {
            'task-branches': ['adapt-' + api for api in apis], 'unrelated': 'preserve'})
        return basic

    def adapt(self, basic):
        return step3.read_object(basic.parent / 'adapt-information.json')

    def run_step3(self, basic):
        result = step3.prepare(basic, root=self.root)
        if result['status'] in step3.READY:
            expected = {'status', 'stop-task', 'task-branches', 'message'}
            if not result['stop-task']:
                expected.add('development-branch')
            self.assertEqual(set(result), expected)
            for field in ('dependency-tests', 'mapping-refreshed', 'mapping-refresh-plan', 'deferred-actions'):
                self.assertNotIn(field, self.adapt(basic)['git-control'])
        return result

    def source_branch(self, branch, filename=None, content='work\n'):
        self.g('checkout', 'master', repo=self.seed)
        self.g('checkout', '-b', branch, repo=self.seed)
        (self.seed / (filename or branch + '.txt')).write_text(content)
        commit = self.commit(self.seed, 'work on ' + branch)
        self.g('push', str(self.fork), branch, repo=self.seed)
        # Completed work is already present in this agent's local repository.
        self.g('fetch', '--no-tags', 'origin', '+refs/heads/' + branch + ':refs/remotes/origin/' + branch)
        self.g('branch', '--no-track', branch, 'refs/remotes/origin/' + branch)
        return commit

    def record(self, **mapping):
        step3.write_json(self.mapping, mapping)

    def test_new_batch_creates_one_branch_and_preserves_step21_and_wheel_identity(self):
        basic = self.task('Tensor.foo', 'Tensor.bar')
        before = basic.read_bytes()
        calls = []
        original = step3.git
        def observing(repo, *args, **kwargs):
            if args and args[0] == 'checkout':
                calls.append(args)
            return original(repo, *args, **kwargs)
        with patch.object(step3, 'git', side_effect=observing):
            result = self.run_step3(basic)
        branch = result['development-branch']
        self.assertRegex(branch, r'^adapt-set-[0-9a-f]{12}$')
        self.assertEqual(len(calls), 1)
        self.assertEqual(step3.read_object(self.mapping), {
            'adapt-Tensor.foo': branch, 'adapt-Tensor.bar': branch})
        self.assertEqual(self.adapt(basic)['task-branches'], ['adapt-Tensor.foo', 'adapt-Tensor.bar'])
        self.assertEqual(self.adapt(basic)['development-branch'], branch)
        self.assertEqual(self.adapt(basic)['unrelated'], 'preserve')
        self.assertEqual(before, basic.read_bytes())
        self.assertEqual(self.g('rev-parse', 'HEAD'), self.g('rev-parse', 'upstream/master'))

    def test_later_run_finds_member_and_extends_it_on_a_new_branch(self):
        first = self.run_step3(self.task('Tensor.foo', 'Tensor.bar'))
        branch = first['development-branch']
        (self.repo / 'adapted.txt').write_text('implemented together\n')
        previous = self.commit(self.repo, 'adapt both APIs')
        basic = self.task('Tensor.bar', 'Tensor.baz', run='two')
        result = self.run_step3(basic)
        self.assertNotEqual(result['development-branch'], branch)
        self.assertEqual(self.adapt(basic)['git-control']['action'], 'EXTEND')
        self.assertTrue(step3.ancestor(self.repo, previous))
        self.assertEqual(step3.read_object(self.mapping), {
            'adapt-Tensor.foo': branch, 'adapt-Tensor.bar': branch,
            'adapt-Tensor.baz': result['development-branch']})
        self.assertEqual(len(step3.read_object(self.mapping)), 3)
        self.assertEqual(self.g('rev-parse', branch), previous)

    def test_combines_all_sources_and_inherits_unrequested_members(self):
        left = self.source_branch('adapt-set-left')
        right = self.source_branch('adapt-set-right')
        unrelated = self.source_branch('adapt-set-unrelated')
        self.record(**{'adapt-Tensor.foo': 'adapt-set-left', 'adapt-Tensor.bar': 'adapt-set-left',
                       'adapt-Tensor.baz': 'adapt-set-right', 'adapt-Tensor.other': 'adapt-set-unrelated'})
        basic = self.task('Tensor.foo', 'Tensor.baz', 'Tensor.qux')
        result = self.run_step3(basic)
        branch = result['development-branch']
        self.assertEqual(self.adapt(basic)['git-control']['action'], 'EXTEND')
        self.assertNotIn(branch, ['adapt-set-left', 'adapt-set-right'])
        for commit in [left, right, self.g('rev-parse', 'upstream/master')]:
            self.assertTrue(step3.ancestor(self.repo, commit))
        self.assertFalse(step3.ancestor(self.repo, unrelated))
        mapping = step3.read_object(self.mapping)
        self.assertEqual(mapping['adapt-Tensor.foo'], 'adapt-set-left')
        self.assertEqual(mapping['adapt-Tensor.bar'], 'adapt-set-left')
        self.assertEqual(mapping['adapt-Tensor.baz'], 'adapt-set-right')
        self.assertEqual(mapping['adapt-Tensor.qux'], branch)
        self.assertEqual(mapping['adapt-Tensor.other'], 'adapt-set-unrelated')
        self.assertEqual(self.g('rev-parse', 'origin/adapt-set-left'), left)
        later_basic = self.task('Tensor.bar', run='later')
        later = self.run_step3(later_basic)
        self.assertEqual(self.adapt(later_basic)['git-control']['source-branches'], ['adapt-set-left'])
        self.assertNotIn('development-branch', later)

    def test_reuses_local_master_already_updated_by_step0(self):
        self.g('checkout', 'master', repo=self.seed)
        (self.seed / 'new-master.txt').write_text('latest\n')
        newest = self.commit(self.seed, 'new remote master')
        self.g('push', 'origin', 'master', repo=self.seed)
        # Simulate Step 0 updating master, then leaving HEAD on the wheel commit.
        self.g('fetch', 'upstream')
        self.g('branch', '-f', 'master', 'upstream/master')
        basic = self.task('Tensor.foo')
        self.run_step3(basic)
        self.assertEqual(self.adapt(basic)['git-control']['base-commit'], newest)
        self.assertEqual(self.g('rev-parse', 'HEAD'), newest)
        self.assertEqual(self.g('rev-parse', 'master'), newest)

    def test_does_not_refetch_upstream_when_remote_master_has_advanced(self):
        prepared = self.g('rev-parse', 'master')
        self.g('checkout', 'master', repo=self.seed)
        (self.seed / 'later-master.txt').write_text('arrived after Step 0\n')
        newer = self.commit(self.seed, 'later upstream commit')
        self.g('push', 'origin', 'master', repo=self.seed)
        basic = self.task('Tensor.foo')
        self.run_step3(basic)
        self.assertNotEqual(prepared, newer)
        self.assertEqual(self.adapt(basic)['git-control']['base-commit'], prepared)
        self.assertEqual(self.g('rev-parse', 'HEAD'), prepared)
        self.assertEqual(self.g('rev-parse', 'master'), prepared)
        self.assertEqual(self.g('rev-parse', 'upstream/master'), prepared)

    def test_both_remotes_can_be_offline_while_local_work_is_reused(self):
        previous = self.source_branch('adapt-set-existing')
        self.record(**{'adapt-Tensor.foo': 'adapt-set-existing'})
        prepared = self.g('rev-parse', 'master')
        self.origin.rename(self.root / 'offline-upstream')
        self.fork.rename(self.root / 'offline-fork')
        basic = self.task('Tensor.foo')
        result = self.run_step3(basic)
        self.assertEqual(self.adapt(basic)['git-control']['source-branches'], ['adapt-set-existing'])
        self.assertNotIn('development-branch', result)
        self.assertEqual(self.g('rev-parse', 'HEAD'), prepared)
        self.assertFalse(step3.ancestor(self.repo, previous))
        self.assertEqual(self.g('rev-parse', 'origin/adapt-set-existing'), previous)

    def test_remote_only_branch_is_not_reused_when_local_mapping_is_missing_its_branch(self):
        self.source_branch('adapt-set-remote')
        self.g('branch', '-D', 'adapt-set-remote')
        self.record(**{'adapt-Tensor.foo': 'adapt-set-remote'})
        self.assertNotIn('refs/heads/adapt-set-remote', step3.refs(self.repo))
        with self.assertRaisesRegex(ValueError, 'branch is missing'):
            self.run_step3(self.task('Tensor.foo'))
        self.assertNotIn('refs/heads/adapt-set-remote', step3.refs(self.repo))

    def test_local_development_is_authoritative_even_if_cached_fork_history_differs(self):
        self.source_branch('adapt-set-shared')
        self.record(**{'adapt-Tensor.foo': 'adapt-set-shared'})
        self.run_step3(self.task('Tensor.foo'))
        self.g('checkout', 'adapt-set-shared')
        (self.repo / 'local.txt').write_text('local work\n')
        local = self.commit(self.repo, 'local development')
        (self.seed / 'remote.txt').write_text('remote work\n')
        remote = self.commit(self.seed, 'remote development')
        self.g('push', str(self.fork), 'adapt-set-shared', repo=self.seed)
        self.g('fetch', '--no-tags', 'origin', '+refs/heads/adapt-set-shared:refs/remotes/origin/adapt-set-shared')
        basic = self.task('Tensor.foo', run='two')
        result = self.run_step3(basic)
        self.assertEqual(self.adapt(basic)['git-control']['source-branches'], ['adapt-set-shared'])
        self.assertNotIn('development-branch', result)
        self.assertTrue(step3.ancestor(self.repo, local))
        self.assertFalse(step3.ancestor(self.repo, remote))
        self.assertFalse((self.repo / 'remote.txt').exists())

    def test_conflict_leaves_mapping_unchanged_then_resumes_remaining_merges(self):
        self.source_branch('adapt-set-left', 'shared.txt', 'left\n')
        self.source_branch('adapt-set-right', 'shared.txt', 'right\n')
        third = self.source_branch('adapt-set-third')
        self.record(**{'adapt-Tensor.foo': 'adapt-set-left', 'adapt-Tensor.bar': 'adapt-set-left',
                       'adapt-Tensor.baz': 'adapt-set-right', 'adapt-Tensor.qux': 'adapt-set-third'})
        basic = self.task('Tensor.foo', 'Tensor.baz', 'Tensor.qux', 'Tensor.new')
        before = self.mapping.read_bytes()
        result = self.run_step3(basic)
        branch = result['development-branch']
        self.assertEqual(result['status'], 'MERGE_CONFLICT')
        self.assertEqual(result['conflict-files'], ['shared.txt'])
        self.assertEqual(self.mapping.read_bytes(), before)
        self.assertNotIn('development-branch', self.adapt(basic))
        again = self.run_step3(basic)
        self.assertEqual(again['development-branch'], branch)
        (self.repo / 'shared.txt').write_text('left and right preserved\n')
        self.g('add', 'shared.txt')
        self.g('-c', 'core.editor=true', 'merge', '--continue')
        result = self.run_step3(basic)
        self.assertEqual(result['status'], 'BRANCH_READY')
        self.assertEqual(result['development-branch'], branch)
        self.assertTrue(step3.ancestor(self.repo, third))
        self.assertEqual(step3.read_object(self.mapping), {
            'adapt-Tensor.foo':'adapt-set-left', 'adapt-Tensor.bar':'adapt-set-left',
            'adapt-Tensor.baz':'adapt-set-right', 'adapt-Tensor.qux':'adapt-set-third',
            'adapt-Tensor.new':branch})

    def test_missing_mapped_branch_stops_without_creating_replacement(self):
        self.record(**{'adapt-Tensor.foo': 'adapt-set-missing'})
        basic = self.task('Tensor.foo')
        before = step3.refs(self.repo)
        with self.assertRaisesRegex(ValueError, 'branch is missing'):
            self.run_step3(basic)
        self.assertEqual({k: v for k, v in step3.refs(self.repo).items() if k.startswith('refs/heads/')},
                         {k: v for k, v in before.items() if k.startswith('refs/heads/')})
        self.assertEqual(self.g('rev-parse', '--abbrev-ref', 'HEAD'), 'HEAD')

    def test_dirty_worktree_stops_without_checkout_or_map_changes(self):
        (self.repo / 'untracked.txt').write_text('preserve\n')
        basic = self.task('Tensor.foo')
        before = self.mapping.read_bytes()
        with self.assertRaisesRegex(ValueError, 'uncommitted/untracked'):
            self.run_step3(basic)
        self.assertEqual(self.mapping.read_bytes(), before)
        self.assertEqual((self.repo / 'untracked.txt').read_text(), 'preserve\n')

    def test_retry_does_not_create_another_big_branch(self):
        basic = self.task('Tensor.foo', 'Tensor.bar')
        first = self.run_step3(basic)
        adapt = self.adapt(basic)
        adapt['adapt-plan'] = {'step1': {'agentname': 'obsolete', 'task-branches': ['old-task']}}
        adapt['dispatcher'] = {'agents': ['old-agent']}
        step3.write_json(basic.parent / 'adapt-information.json', adapt)
        before = step3.refs(self.repo)
        second = self.run_step3(basic)
        self.assertNotIn('adapt-plan', self.adapt(basic))
        self.assertNotIn('dispatcher', self.adapt(basic))
        self.assertEqual(first['development-branch'], second['development-branch'])
        self.assertEqual(step3.refs(self.repo), before)

    def test_publication_failure_can_recover_after_mapping_was_written(self):
        basic = self.task('Tensor.foo')
        original = step3.write_json
        def fail_ready(path, value):
            if Path(path).name == 'adapt-information.json' and value.get('git-control', {}).get('status') == 'BRANCH_READY':
                raise OSError('fixture publication failure')
            return original(path, value)
        with patch.object(step3, 'write_json', side_effect=fail_ready):
            with self.assertRaisesRegex(OSError, 'publication failure'):
                self.run_step3(basic)
        branch = step3.read_object(self.mapping)['adapt-Tensor.foo']
        self.assertEqual(self.adapt(basic)['git-control']['status'], 'FAILED')
        recovered = self.run_step3(basic)
        self.assertEqual(recovered['development-branch'], branch)
        self.assertEqual(recovered['status'], 'BRANCH_READY')

    def test_unmapped_branch_is_not_automatically_adopted_for_a_task(self):
        commit = self.source_branch('adapt-Tensor.foo')
        result = self.run_step3(self.task('Tensor.foo', 'Tensor.bar'))
        self.assertNotEqual(result['development-branch'], 'adapt-Tensor.foo')
        self.assertFalse(step3.ancestor(self.repo, commit))
        self.assertEqual(self.g('rev-parse', 'adapt-Tensor.foo'), commit)
        self.assertEqual(step3.read_object(self.mapping)['adapt-Tensor.bar'], result['development-branch'])

    def test_duplicate_json_keys_and_empty_task_identifiers_are_rejected(self):
        basic = self.task('Tensor.foo')
        path = basic.parent / 'adapt-information.json'
        path.write_text('{"task-branches":{"Tensor.foo":"adapt-Tensor.foo","Tensor.foo":"adapt-Tensor.foo"}}')
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON key'):
            self.run_step3(basic)
        step3.write_json(path, {'task-branches': {'Tensor.foo': ''}})
        with self.assertRaisesRegex(ValueError, 'nonempty task'):
            self.run_step3(basic)

    def test_changed_selection_cannot_overwrite_pending_recovery(self):
        self.source_branch('adapt-set-left', 'shared.txt', 'left\n')
        self.source_branch('adapt-set-right', 'shared.txt', 'right\n')
        self.record(**{'adapt-Tensor.foo': 'adapt-set-left', 'adapt-Tensor.bar': 'adapt-set-right'})
        basic = self.task('Tensor.foo', 'Tensor.bar', 'Tensor.new')
        self.run_step3(basic)
        path = basic.parent / 'adapt-information.json'
        adapt = self.adapt(basic)
        adapt['task-branches'].append('adapt-Tensor.baz')
        step3.write_json(path, adapt)
        with self.assertRaisesRegex(ValueError, 'selection changed'):
            self.run_step3(basic)

    def test_cli_emits_machine_readable_ready_and_selected_branch(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for filename in ['prepare_development_branch.py', 'preparation_state.py']:
            (scripts / filename).write_bytes((Path(step3.__file__).parent / filename).read_bytes())
        basic = self.task('Tensor.foo')
        result = subprocess.run([sys.executable, '-B', str(scripts / 'prepare_development_branch.py'),
                                 '--basic-information', str(basic)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['status'], 'BRANCH_READY')
        self.assertEqual(output['development-branch'], self.adapt(basic)['development-branch'])

    def test_upstream_only_supports_creation_and_local_branch_reuse(self):
        self.g('remote', 'remove', 'origin')
        first = self.run_step3(self.task('Tensor.foo', 'Tensor.bar'))
        (self.repo / 'local-work.txt').write_text('no fork configured\n')
        commit = self.commit(self.repo, 'local adaptation')
        later_basic = self.task('Tensor.bar', run='later')
        later = self.run_step3(later_basic)
        self.assertEqual(self.adapt(later_basic)['git-control']['source-branches'], [first['development-branch']])
        self.assertNotIn('development-branch', later)
        self.assertTrue(step3.ancestor(self.repo, commit))
        self.assertEqual(self.g('remote'), 'upstream')

    def test_starting_on_master_still_checks_out_only_one_development_branch(self):
        self.g('checkout', 'master')
        original = step3.git
        checkouts = []
        def observing(repo, *args, **kwargs):
            if args and args[0] == 'checkout':
                checkouts.append(args)
            return original(repo, *args, **kwargs)
        basic = self.task('Tensor.foo')
        with patch.object(step3, 'git', side_effect=observing):
            self.run_step3(basic)
        self.assertEqual(len(checkouts), 1)
        self.assertNotEqual(self.g('branch', '--show-current'), 'master')

    def test_local_master_commits_are_never_overwritten(self):
        self.g('checkout', 'master')
        (self.repo / 'local-master.txt').write_text('preserve\n')
        local = self.commit(self.repo, 'unpublished master work')
        basic = self.task('Tensor.foo')
        with self.assertRaisesRegex(ValueError, 'preserving local commits'):
            self.run_step3(basic)
        self.assertEqual(self.g('rev-parse', 'master'), local)
        self.assertEqual(step3.read_object(self.mapping), {})

    def test_source_refresh_uses_upstream_and_preserves_fork(self):
        import fetch_latest_sources as sources
        result = sources.refresh_torchnpu(str(self.origin), self.root / 'source.log', data_root=self.root / 'data')
        self.assertEqual(result['main_branch'], 'master')
        self.assertEqual(self.g('remote', 'get-url', 'upstream'), str(self.origin))
        self.assertEqual(self.g('remote', 'get-url', 'origin'), str(self.fork))

    def test_source_refresh_migrates_legacy_primary_origin_to_upstream(self):
        import fetch_latest_sources as sources
        self.g('remote', 'remove', 'origin')
        self.g('remote', 'rename', 'upstream', 'origin')
        sources.refresh_torchnpu(str(self.origin), self.root / 'source.log', data_root=self.root / 'data')
        self.assertEqual(self.g('remote'), 'upstream')
        self.assertEqual(self.g('remote', 'get-url', 'upstream'), str(self.origin))

    def test_source_preparation_configures_the_requested_fork(self):
        import fetch_latest_sources as sources
        self.g('remote', 'remove', 'origin')
        sources.refresh_torchnpu(str(self.origin), self.root / 'source.log',
                                data_root=self.root / 'data', fork_remote=str(self.fork))
        self.assertEqual(self.g('remote', 'get-url', 'origin'), str(self.fork))
        self.assertEqual(self.g('remote', 'get-url', 'upstream'), str(self.origin))

    def test_source_preparation_preserves_a_different_existing_fork(self):
        import fetch_latest_sources as sources
        with self.assertRaisesRegex(ValueError, 'fork origin differs'):
            sources.refresh_torchnpu(str(self.origin), self.root / 'source.log',
                                    data_root=self.root / 'data', fork_remote=str(self.root / 'other-fork'))
        self.assertEqual(self.g('remote', 'get-url', 'origin'), str(self.fork))

    def test_all_existing_stops_without_checkout_merge_or_new_branch(self):
        left = self.source_branch('adapt-set-left')
        right = self.source_branch('adapt-set-right')
        self.record(**{'adapt-Tensor.foo': 'adapt-set-left', 'adapt-Tensor.bar': 'adapt-set-left',
                       'adapt-Tensor.baz': 'adapt-set-right'})
        basic = self.task('Tensor.foo', 'Tensor.baz')
        before = self.mapping.read_bytes()
        request = self.adapt(basic)['task-branches']
        refs_before = step3.refs(self.repo)
        head_before = self.g('rev-parse', 'HEAD')
        original = step3.git
        def lookup_only(repo, *args, **kwargs):
            self.assertNotIn(args[0], {'checkout', 'switch', 'merge', 'commit', 'push',
                                       'fetch', 'pull', 'reset', 'branch'})
            return original(repo, *args, **kwargs)
        with patch.object(step3, 'git', side_effect=lookup_only):
            result = self.run_step3(basic)
        self.assertEqual(result['status'], 'EXISTING_BRANCHES_READY')
        self.assertTrue(result['stop-task'])
        self.assertEqual(step3.refs(self.repo), refs_before)
        self.assertEqual(self.g('rev-parse', 'HEAD'), head_before)
        self.assertEqual(self.g('rev-parse', 'adapt-set-left'), left)
        self.assertEqual(self.g('rev-parse', 'adapt-set-right'), right)
        self.assertEqual(self.mapping.read_bytes(), before)
        adapt = self.adapt(basic)
        self.assertEqual(adapt['task-branches'], request)
        self.assertNotIn('development-branch', adapt)
        self.assertIn('adapt-Tensor.bar', adapt['git-control']['original-mappings'])
        for pending in ('合并', '测试检验', '必要开发', '映射刷新'):
            self.assertIn(pending, result['message'])
        refs_before = step3.refs(self.repo)
        retry = self.run_step3(basic)
        self.assertEqual(retry['status'], 'EXISTING_BRANCHES_READY')
        self.assertNotIn('development-branch', retry)
        self.assertEqual(step3.refs(self.repo), refs_before)
        self.assertEqual(self.mapping.read_bytes(), before)

    def test_all_existing_one_branch_stops_and_keeps_input_list(self):
        self.source_branch('adapt-set-existing')
        self.record(**{'adapt-Tensor.foo': 'adapt-set-existing', 'adapt-Tensor.bar': 'adapt-set-existing'})
        basic = self.task('Tensor.foo', 'Tensor.bar')
        before = self.mapping.read_bytes()
        result = self.run_step3(basic)
        self.assertNotIn('development-branch', result)
        self.assertNotIn('development-branch', self.adapt(basic))
        self.assertEqual(result['status'], 'EXISTING_BRANCHES_READY')
        self.assertTrue(result['stop-task'])
        self.assertEqual(len(self.adapt(basic)['task-branches']), 2)
        self.assertEqual(self.mapping.read_bytes(), before)

    def test_mixed_prunes_known_apis_and_preserves_their_global_mappings(self):
        dependency = self.source_branch('adapt-set-dependency')
        self.record(**{'adapt-Tensor.foo': 'adapt-set-dependency',
                       'adapt-Tensor.bar': 'adapt-set-dependency'})
        basic = self.task('Tensor.foo', 'Tensor.new')
        result = self.run_step3(basic)
        branch = result['development-branch']
        self.assertNotEqual(branch, 'adapt-set-dependency')
        self.assertFalse(result['stop-task'])
        self.assertEqual(result['task-branches'], ['adapt-Tensor.new'])
        adapt = self.adapt(basic)
        self.assertEqual(adapt['task-branches'], ['adapt-Tensor.new'])
        self.assertEqual(adapt['unrelated'], 'preserve')
        self.assertIn('测试按要求暂不执行', result['message'])
        self.assertEqual(adapt['git-control']['checkout-base'], 'refs/heads/adapt-set-dependency')
        self.assertEqual(self.g('rev-parse', 'origin/adapt-set-dependency'), dependency)
        self.assertEqual(step3.read_object(self.mapping), {
            'adapt-Tensor.foo':'adapt-set-dependency', 'adapt-Tensor.bar':'adapt-set-dependency',
            'adapt-Tensor.new':branch})
        retry = self.run_step3(basic)
        self.assertEqual(retry['status'], 'BRANCH_READY')
        self.assertEqual(retry['task-branches'], ['adapt-Tensor.new'])
        self.assertEqual(retry['development-branch'], branch)

    def test_mixed_publication_retry_survives_pruned_request(self):
        self.source_branch('adapt-set-dependency')
        self.record(**{'adapt-Tensor.foo': 'adapt-set-dependency'})
        basic = self.task('Tensor.foo', 'Tensor.new')
        original = step3.write_json
        def fail_ready(path, value):
            if Path(path).name == 'adapt-information.json' and value.get('git-control', {}).get('status') == 'BRANCH_READY':
                raise OSError('fixture final write failure')
            return original(path, value)
        with patch.object(step3, 'write_json', side_effect=fail_ready):
            with self.assertRaisesRegex(OSError, 'final write failure'):
                self.run_step3(basic)
        self.assertEqual(self.adapt(basic)['task-branches'], ['adapt-Tensor.new'])
        branch = self.adapt(basic)['git-control']['branch']
        result = self.run_step3(basic)
        self.assertEqual(result['status'], 'BRANCH_READY')
        self.assertEqual(result['development-branch'], branch)
        self.assertFalse(result['stop-task'])

    def test_all_existing_cli_exits_successfully_and_instructs_task_stop(self):
        self.source_branch('adapt-set-existing')
        self.record(**{'adapt-Tensor.foo': 'adapt-set-existing'})
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for filename in ['prepare_development_branch.py', 'preparation_state.py']:
            (scripts / filename).write_bytes((Path(step3.__file__).parent / filename).read_bytes())
        basic = self.task('Tensor.foo')
        result = subprocess.run([sys.executable, '-B', str(scripts / 'prepare_development_branch.py'),
                                 '--basic-information', str(basic)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['status'], 'EXISTING_BRANCHES_READY')
        self.assertTrue(output['stop-task'])


    def named_tasks(self, request, run='generic'):
        basic = self.task('placeholder', run=run)
        adapt = self.adapt(basic)
        adapt['task-branches'] = request
        step3.write_json(basic.parent / 'adapt-information.json', adapt)
        return basic

    def test_generic_new_tasks_need_no_api_name_or_adapt_prefix(self):
        long_identifier = '修复集合通信超时 / issue 123 ? [恢复]' * 20
        request = ['bugfix-12345', long_identifier]
        basic = self.named_tasks(request)
        result = self.run_step3(basic)
        branch = result['development-branch']
        self.assertEqual(result['status'], 'BRANCH_READY')
        self.assertEqual(result['task-branches'], request)
        self.assertNotIn('api-branches', result)
        self.assertEqual(self.adapt(basic)['task-branches'], request)
        self.assertEqual(step3.read_object(self.mapping), dict.fromkeys(request, branch))
        self.assertRegex(branch, r'^adapt-set-[0-9a-f]{12}$')

    def test_generic_existing_tasks_stop_without_merge_or_refresh(self):
        left = self.source_branch('adapt-set-left')
        right = self.source_branch('adapt-set-right')
        self.record(**{'issue:123': 'adapt-set-left', 'support-checkpoint': 'adapt-set-right',
                       'related bug': 'adapt-set-left'})
        request = ['issue:123', 'support-checkpoint']
        basic = self.named_tasks(request)
        before = self.mapping.read_bytes()
        refs_before = step3.refs(self.repo)
        head_before = self.g('rev-parse', 'HEAD')
        result = self.run_step3(basic)
        self.assertEqual(result['status'], 'EXISTING_BRANCHES_READY')
        self.assertTrue(result['stop-task'])
        self.assertEqual(self.mapping.read_bytes(), before)
        self.assertEqual(step3.refs(self.repo), refs_before)
        self.assertEqual(self.g('rev-parse', 'HEAD'), head_before)
        self.assertEqual(self.g('rev-parse', 'adapt-set-left'), left)
        self.assertEqual(self.g('rev-parse', 'adapt-set-right'), right)
        self.assertIn('related bug', self.adapt(basic)['git-control']['original-mappings'])

    def test_generic_mixed_tasks_prune_only_the_existing_task(self):
        dependency = self.source_branch('adapt-set-dependency')
        self.record(**{'fix-timeout': 'adapt-set-dependency', 'legacy-feature': 'adapt-set-dependency'})
        basic = self.named_tasks(['fix-timeout', 'feature-checkpoint'])
        result = self.run_step3(basic)
        branch = result['development-branch']
        self.assertEqual(result['status'], 'BRANCH_READY')
        self.assertEqual(self.adapt(basic)['task-branches'], ['feature-checkpoint'])
        self.assertEqual(result['task-branches'], ['feature-checkpoint'])
        self.assertIn('测试按要求暂不执行', result['message'])
        self.assertEqual(set(step3.read_object(self.mapping)), {'fix-timeout', 'legacy-feature', 'feature-checkpoint'})
        self.assertEqual(step3.read_object(self.mapping), {
            'fix-timeout':'adapt-set-dependency', 'legacy-feature':'adapt-set-dependency',
            'feature-checkpoint':branch})
        self.assertTrue(step3.ancestor(self.repo, dependency))
        self.assertEqual(self.run_step3(basic)['status'], 'BRANCH_READY')

    def test_duplicate_task_identifiers_cannot_create_multiple_work_items(self):
        basic = self.named_tasks(['same-task', 'same-task'])
        with self.assertRaisesRegex(ValueError, 'unique task identifiers'):
            self.run_step3(basic)
        self.assertEqual(step3.read_object(self.mapping), {})
        self.assertEqual(self.g('rev-parse', '--abbrev-ref', 'HEAD'), 'HEAD')

    def test_legacy_api_checkpoint_is_normalized_without_new_branch(self):
        basic = self.task('Tensor.foo')
        first = self.run_step3(basic)
        path = basic.parent / 'adapt-information.json'
        adapt = self.adapt(basic)
        checkpoint = adapt['git-control']
        checkpoint['api-branches'] = list(checkpoint['original-mappings'])
        checkpoint['mode'] = 'HAS_NEW_APIS'
        for field in ['requested-branches', 'existing-branches', 'remaining-branches']:
            checkpoint[field] = {str(i): identifier for i, identifier in enumerate(checkpoint[field])}
        adapt['task-branches'] = {'Tensor.foo': 'adapt-Tensor.foo'}
        step3.write_json(path, adapt)
        result = self.run_step3(basic)
        self.assertEqual(result['development-branch'], first['development-branch'])
        self.assertEqual(result['task-branches'], ['adapt-Tensor.foo'])
        self.assertNotIn('task-branches', self.adapt(basic)['git-control'])
        self.assertEqual(self.adapt(basic)['git-control']['mode'], 'HAS_NEW_TASKS')
        self.assertNotIn('api-branches', self.adapt(basic)['git-control'])
        self.assertEqual(self.adapt(basic)['task-branches'], ['adapt-Tensor.foo'])
        for field in ['requested-branches', 'existing-branches', 'remaining-branches']:
            self.assertIsInstance(self.adapt(basic)['git-control'][field], list)

    def test_legacy_task_object_is_read_but_only_array_is_written(self):
        basic = self.named_tasks({'旧的任务说明': 'fix-timeout', '旧的特性说明': 'feature-checkpoint'})
        result = self.run_step3(basic)
        self.assertEqual(self.adapt(basic)['task-branches'], ['fix-timeout', 'feature-checkpoint'])
        self.assertEqual(result['task-branches'], ['fix-timeout', 'feature-checkpoint'])
        self.assertEqual(set(step3.read_object(self.mapping)), {'fix-timeout', 'feature-checkpoint'})

    def test_array_rejects_empty_or_non_string_task_identifiers(self):
        for request in [[], [''], [None], [123], [['nested']], 'one-task']:
            with self.subTest(request=request):
                basic = self.named_tasks(request)
                with self.assertRaisesRegex(ValueError, 'nonempty task'):
                    self.run_step3(basic)
        self.assertEqual(step3.read_object(self.mapping), {})



    def test_retired_metadata_is_removed_without_losing_inherited_tasks(self):
        self.source_branch('adapt-set-dependency')
        self.record(**{'fix-timeout': 'adapt-set-dependency', 'related-task': 'adapt-set-dependency'})
        basic = self.named_tasks(['fix-timeout'])
        self.run_step3(basic)
        mapping_before = self.mapping.read_bytes()
        path = basic.parent / 'adapt-information.json'
        adapt = self.adapt(basic)
        checkpoint = adapt['git-control']
        checkpoint['task-branches'] = list(checkpoint['original-mappings'])
        checkpoint.update({'dependency-tests': {'executed': False}, 'mapping-refreshed': False,
                           'mapping-refresh-plan': {'fix-timeout': 'obsolete'},
                           'deferred-actions': [{'action': 'RUN_ALL_BRANCH_TESTS',
                                                'api-branches': list(checkpoint['original-mappings'])}]})
        step3.write_json(path, adapt)
        result = self.run_step3(basic)
        self.assertEqual(result['status'], 'EXISTING_BRANCHES_READY')
        self.assertEqual(result['task-branches'], ['fix-timeout'])
        self.assertNotIn('task-branches', self.adapt(basic)['git-control'])
        self.assertEqual(set(self.adapt(basic)['git-control']['original-mappings']), {'fix-timeout', 'related-task'})
        self.assertEqual(self.mapping.read_bytes(), mapping_before)

    def test_local_only_mapped_branch_is_reused_without_contacting_fork(self):
        original = self.source_branch('adapt-set-local-work')
        self.record(**{'fix-timeout': 'adapt-set-local-work'})
        self.g('checkout', 'adapt-set-local-work')
        (self.repo / 'local-latest.txt').write_text('newer local work\n')
        newest = self.commit(self.repo, 'local work not pushed yet')
        self.g('push', str(self.fork), '--delete', 'adapt-set-local-work', repo=self.seed)
        self.fork.rename(self.root / 'offline-fork')
        basic = self.named_tasks(['fix-timeout', 'feature-checkpoint'])
        result = self.run_step3(basic)
        self.assertEqual(result['status'], 'BRANCH_READY')
        self.assertTrue(step3.ancestor(self.repo, original))
        self.assertTrue(step3.ancestor(self.repo, newest))
        self.assertEqual(self.adapt(basic)['task-branches'], ['feature-checkpoint'])
        self.assertEqual(self.adapt(basic)['git-control']['source-branches'], ['adapt-set-local-work'])
        self.assertNotIn('source-commits', self.adapt(basic)['git-control'])

    def test_child_retries_after_merged_source_task_record_is_removed(self):
        self.source_branch('adapt-set-dependency')
        self.record(**{'fix-timeout':'adapt-set-dependency'})
        basic = self.named_tasks(['fix-timeout','feature-checkpoint'])
        first = self.run_step3(basic)
        self.g('checkout','master',repo=self.seed)
        self.g('merge','--ff-only','adapt-set-dependency',repo=self.seed)
        self.g('push','origin','master',repo=self.seed)
        self.g('fetch','upstream','master')
        self.g('branch','-f','master','upstream/master')
        self.record(**{'feature-checkpoint':first['development-branch']})
        result = self.run_step3(basic)
        self.assertEqual(result['development-branch'],first['development-branch'])
        self.assertEqual(result['task-branches'],['feature-checkpoint'])
        self.assertEqual(self.adapt(basic)['git-control']['source-branches'],['adapt-set-dependency'])
        self.assertTrue(self.g('branch','--list','adapt-set-dependency'))
        self.assertEqual(step3.read_object(self.mapping),{'feature-checkpoint':first['development-branch']})

    def test_retry_uses_updated_local_dependency_and_discards_legacy_snapshot(self):
        self.source_branch('adapt-set-dependency')
        self.record(**{'fix-timeout': 'adapt-set-dependency'})
        basic = self.named_tasks(['fix-timeout', 'feature-checkpoint'])
        first = self.run_step3(basic)
        (self.seed / 'new-dependency.txt').write_text('latest local dependency\n')
        newest = self.commit(self.seed, 'dependency corrected by this agent')
        self.g('fetch', '--no-tags', str(self.seed),
               'refs/heads/adapt-set-dependency:refs/heads/adapt-set-dependency')
        path = basic.parent / 'adapt-information.json'
        adapt = self.adapt(basic)
        adapt['git-control']['source-commits'] = ['f' * 40]
        adapt['git-control']['track-origin'] = True
        step3.write_json(path, adapt)
        result = self.run_step3(basic)
        self.assertEqual(result['development-branch'], first['development-branch'])
        self.assertTrue(step3.ancestor(self.repo, newest))
        self.assertTrue((self.repo / 'new-dependency.txt').is_file())
        self.assertNotIn('source-commits', self.adapt(basic)['git-control'])
        self.assertNotIn('track-origin', self.adapt(basic)['git-control'])



if __name__ == '__main__':
    unittest.main(verbosity=2)
