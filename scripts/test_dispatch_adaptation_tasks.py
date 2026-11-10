"""Plan generation checks with isolated Git repositories and no agent execution."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

import dispatch_adaptation_tasks as dispatcher
import prepare_development_branch as step3
import test_prepare_development_branch as branch_fixtures


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.fixture = branch_fixtures.DevelopmentBranchTests('runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.root = self.fixture.root

    def prepare(self, tasks, run='dispatch'):
        basic = self.fixture.named_tasks(tasks, run=run)
        result = step3.prepare(basic, root=self.root)
        return basic, result

    def dispatch(self, basic):
        return dispatcher.dispatch(basic, basic.parent / 'adapt-information.json', root=self.root)

    def test_all_api_tasks_go_to_exactly_one_agent_in_original_order(self):
        tasks = ['adapt-torch.nn.functional.relu', 'adapt-Tensor.add', 'adapt-view']
        basic, prepared = self.prepare(tasks)
        mapping_before = self.fixture.mapping.read_bytes()
        refs_before = step3.refs(self.fixture.repo)
        basic_before = basic.read_bytes()
        adapt_before = self.fixture.adapt(basic)
        result = self.dispatch(basic)
        self.assertEqual(result['status'], 'DISPATCH_READY')
        adapt = self.fixture.adapt(basic)
        self.assertEqual(adapt, {**adapt_before, 'adapt-plan': {
            'step1': {'agentname': 'api-adaptation', 'task-branches': tasks}}})
        self.assertEqual(adapt['development-branch'], prepared['development-branch'])
        self.assertEqual(basic.read_bytes(), basic_before)
        self.assertEqual(step3.refs(self.fixture.repo), refs_before)
        self.assertEqual(self.fixture.mapping.read_bytes(), mapping_before)

    def test_removed_dependency_tasks_are_not_dispatched_again(self):
        self.fixture.source_branch('adapt-set-existing')
        self.fixture.record(**{'adapt-Tensor.add': 'adapt-set-existing'})
        basic, _ = self.prepare(['adapt-Tensor.add', 'adapt-view'])
        self.dispatch(basic)
        self.assertEqual(self.fixture.adapt(basic)['adapt-plan'], {
            'step1': {'agentname': 'api-adaptation', 'task-branches': ['adapt-view']}})

    def test_step3_task_stop_creates_no_agent(self):
        self.fixture.source_branch('adapt-set-existing')
        self.fixture.record(**{'adapt-Tensor.add': 'adapt-set-existing'})
        basic, prepared = self.prepare(['adapt-Tensor.add'])
        self.assertTrue(prepared['stop-task'])
        result = self.dispatch(basic)
        self.assertEqual(result['status'], 'DISPATCH_STOPPED')
        self.assertNotIn('adapt-plan', self.fixture.adapt(basic))

    def test_mixed_task_types_do_not_partially_dispatch_api_subset(self):
        basic, _ = self.prepare(['adapt-Tensor.add', 'fix-collective-timeout'])
        result = self.dispatch(basic)
        self.assertEqual(result['status'], 'DISPATCH_RULE_REQUIRED')
        self.assertNotIn('adapt-plan', self.fixture.adapt(basic))
        self.assertEqual(result['unsupported-task-branches'], ['fix-collective-timeout'])

    def test_malformed_api_task_needs_another_rule(self):
        for task in ['adapt-', 'adapt-foo.bar()', 'adapt-fix-timeout']:
            with self.subTest(task=task):
                basic, _ = self.prepare([task], run='bad-' + str(len(task)))
                result = self.dispatch(basic)
                self.assertEqual(result['status'], 'DISPATCH_RULE_REQUIRED')
                self.assertNotIn('adapt-plan', self.fixture.adapt(basic))

    def test_nonready_step3_result_cannot_dispatch(self):
        basic, _ = self.prepare(['adapt-view'])
        self.dispatch(basic)
        path = basic.parent / 'adapt-information.json'
        adapt = self.fixture.adapt(basic)
        adapt['git-control']['status'] = 'MERGE_CONFLICT'
        step3.write_json(path, adapt)
        with self.assertRaisesRegex(ValueError, 'BRANCH_READY'):
            self.dispatch(basic)
        self.assertNotIn('adapt-plan', self.fixture.adapt(basic))

    def test_wrong_checkout_is_rejected_without_switching_back(self):
        basic, _ = self.prepare(['adapt-view'])
        self.fixture.g('checkout', 'master')
        with self.assertRaisesRegex(ValueError, 'Current checkout'):
            self.dispatch(basic)
        self.assertEqual(self.fixture.g('branch', '--show-current'), 'master')

    def test_stale_task_list_is_rejected(self):
        basic, _ = self.prepare(['adapt-view'])
        path = basic.parent / 'adapt-information.json'
        adapt = self.fixture.adapt(basic)
        adapt['task-branches'].append('adapt-Tensor.add')
        step3.write_json(path, adapt)
        with self.assertRaisesRegex(ValueError, 'remaining tasks'):
            self.dispatch(basic)

    def test_empty_remaining_list_starts_no_agent(self):
        basic, _ = self.prepare(['adapt-view'])
        path = basic.parent / 'adapt-information.json'
        adapt = self.fixture.adapt(basic)
        adapt['task-branches'] = []
        adapt['git-control']['remaining-branches'] = []
        step3.write_json(path, adapt)
        result = self.dispatch(basic)
        self.assertEqual(result['status'], 'NO_TASKS_TO_DISPATCH')
        self.assertNotIn('adapt-plan', self.fixture.adapt(basic))

    def test_worker_changes_since_step3_are_not_silently_redispatched(self):
        basic, _ = self.prepare(['adapt-view'])
        (self.fixture.repo / 'new-work.txt').write_text('worker result\n')
        self.fixture.commit(self.fixture.repo, 'subsequent development')
        with self.assertRaisesRegex(ValueError, 'changed since Step 3'):
            self.dispatch(basic)

    def test_cli_reads_both_inputs_and_writes_plan_into_adapt_information(self):
        basic, _ = self.prepare(['adapt-view', 'adapt-Tensor.add'])
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for name in ['dispatch_adaptation_tasks.py', 'prepare_development_branch.py', 'preparation_state.py']:
            (scripts / name).write_bytes((Path(dispatcher.__file__).parent / name).read_bytes())
        result = subprocess.run([sys.executable, '-B', str(scripts / 'dispatch_adaptation_tasks.py'),
                                 '--basic-information', str(basic),
                                 '--adapt-information', str(basic.parent / 'adapt-information.json')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['status'], 'DISPATCH_READY')
        self.assertEqual(self.fixture.adapt(basic)['adapt-plan'], {
            'step1': {'agentname': 'api-adaptation', 'task-branches': ['adapt-view', 'adapt-Tensor.add']}})

    def test_inputs_from_different_runs_are_rejected_without_changing_either(self):
        basic, _ = self.prepare(['adapt-view'])
        other = self.fixture.named_tasks(['adapt-Tensor.add'], run='other')
        path = basic.parent / 'adapt-information.json'
        other_path = other.parent / 'adapt-information.json'
        before, other_before = path.read_bytes(), other_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'same run-id'):
            dispatcher.dispatch(basic, other_path, root=self.root)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(other_path.read_bytes(), other_before)

    def test_unsupported_tasks_clear_old_plan_and_legacy_dispatcher(self):
        basic, _ = self.prepare(['adapt-view'])
        self.dispatch(basic)
        adapt = self.fixture.adapt(basic)
        adapt['dispatcher'] = {'agents': [{'name': 'obsolete-worker'}]}
        adapt['task-branches'].append('fix-timeout')
        adapt['git-control']['remaining-branches'].append('fix-timeout')
        step3.write_json(basic.parent / 'adapt-information.json', adapt)
        result = self.dispatch(basic)
        self.assertEqual(result['status'], 'DISPATCH_RULE_REQUIRED')
        actual = self.fixture.adapt(basic)
        self.assertNotIn('adapt-plan', actual)
        self.assertNotIn('dispatcher', actual)
        self.assertEqual(actual['task-branches'], ['adapt-view', 'fix-timeout'])

    def test_replanning_replaces_the_plan_without_changing_git_or_other_fields(self):
        basic, _ = self.prepare(['adapt-view', 'adapt-Tensor.add'])
        self.dispatch(basic)
        before = self.fixture.adapt(basic)
        refs_before = step3.refs(self.fixture.repo)
        self.dispatch(basic)
        self.assertEqual(self.fixture.adapt(basic), before)
        self.assertEqual(step3.refs(self.fixture.repo), refs_before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
