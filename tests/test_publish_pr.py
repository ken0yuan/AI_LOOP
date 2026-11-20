import json
from pathlib import Path
from subprocess import CompletedProcess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish_pr as pub


UPSTREAM = "org/project"
FORK = "dev/project"
BRANCH = "batch-b"
ISSUE = "https://gitcode.com/org/project/issues/7"
SHA = "f" * 40
TARGET = UPSTREAM, "master", FORK, BRANCH


def pr(number, *, fork=FORK, head=BRANCH, base="master", state="open", body=ISSUE):
    return dict(number=number, title="old", body=body, state=state, changed_files=1,
                head={"repo": {"full_name": fork}, "ref": head, "sha": SHA},
                base={"repo": {"full_name": UPSTREAM}, "ref": base},
                html_url=f"https://gitcode.com/{UPSTREAM}/merge_requests/{number}")


class SharedIssuePublication(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name) / "batch-b"
        self.run_dir.mkdir()
        self.key = {"repository": UPSTREAM, "base": "master", "fork": FORK,
                    "head": BRANCH, "issue-url": ISSUE}
        pub.save(self.run_dir / "issue.json", {"number": 7, "repository": UPSTREAM, "url": ISSUE})
        pub.save(self.run_dir / "adapt-information.json", {"development-branch": BRANCH})
        (self.run_dir / "pr-title.txt").write_text("reviewed title")
        (self.run_dir / "pr-body.md").write_text("reviewed body " + ISSUE)
        (self.run_dir / "result.json").write_text("{}")
        self.args = SimpleNamespace(run_dir=self.run_dir, source=self.run_dir, base="master",
                                    commit=SHA, verification="result.json")
        self.prs = {10: pr(10, head="batch-a"), 11: pr(11, head="old", state="merged"),
                    12: pr(12, fork="other/project"), 13: pr(13, head="older", state="closed")}
        self.linked = set(self.prs)
        self.writes = []
        self.pushed = False
        for name, impl in (("invoke", self.gitcode), ("git", self.git)):
            mock = patch.object(pub, name, side_effect=impl)
            mock.start()
            self.addCleanup(mock.stop)

    def git(self, repo, *args, **kwargs):
        if "push" in args:
            self.assertEqual(args[-1], f"{SHA}:refs/heads/{BRANCH}")
            self.writes.append("push")
            self.pushed = True
        data = ""
        if args[0] == "config":
            data = {"remote.upstream.url": UPSTREAM, "remote.origin.pushurl": FORK}[args[-1]]
            data = "https://gitcode.com/" + data
        elif args[0] == "branch":
            data = BRANCH
        elif args[0] == "rev-parse":
            data = SHA
        elif args[0] == "diff":
            data = "test.py\0"
        elif args[0] == "ls-remote" and self.pushed:
            data = SHA + "\trefs/heads/" + BRANCH
        return CompletedProcess(args, 0, data, "")

    def gitcode(self, tool, *args, **kwargs):
        self.assertEqual(tool, "gitcode")
        value = None
        if args[:2] == ("auth", "status"):
            pass
        elif args[0] == "api":
            path = args[1].split("/")
            if path[-1] == "pull_requests":
                value = [{"number": n} for n in self.linked]
            elif path[-1] == "issues":
                number = int(path[-2])
                if "--method" in args:
                    self.assertEqual(json.loads(kwargs["stdin"]), [7])
                    self.linked.add(number)
                    self.writes.append(("link", number))
                value = [{"number": 7}] if number in self.linked else []
            else:
                self.assertTrue(path[-1].startswith("files?"))
                value = [{"filename": "test.py"}]
        elif args[:2] == ("pr", "list"):
            value = [{"number": n} for n, p in self.prs.items() if p["head"]["ref"] == BRANCH
                     and ("--base" not in args or p["base"]["ref"] == args[args.index("--base")+1])]
        elif args[:2] == ("pr", "view"):
            value = self.prs[int(args[2])]
        elif args[:2] == ("pr", "create"):
            self.assertEqual(args[args.index("--fork")+1], FORK)
            self.assertEqual(args[args.index("--head")+1], BRANCH)
            self.assertEqual(args[args.index("--base")+1], "master")
            self.prs[20] = pr(20)
            self.writes.append(("create", 20))
            value = self.prs[20]
        elif args[:2] == ("pr", "edit"):
            number = int(args[2])
            self.writes.append(("edit", number))
            self.prs[number].update(title=args[args.index("--title")+1],
                                    body=Path(args[args.index("--body-file")+1]).read_text())
        else:
            self.fail(f"Unexpected GitCode call {args}")
        return CompletedProcess(args, 0, json.dumps(value), "")

    def sibling(self, **fields):
        folder = self.run_dir.parent / "batch-a"
        folder.mkdir()
        pub.save(folder / "pr.json", dict(self.key, **fields))

    def assert_refused(self, message):
        with self.assertRaisesRegex(pub.PublishError, message):
            pub.publish(self.args)
        self.assertEqual(self.writes, [])

    def test_create_then_resume_only_own_pr(self):
        self.sibling(head="batch-a", number=10)
        pub.publish(self.args)
        self.assertEqual(self.writes, ["push", ("create", 20), ("edit", 20), ("link", 20)])
        self.writes.clear()
        (self.run_dir / "pr-title.txt").write_text("revised title")
        pub.publish(self.args)
        self.assertEqual(self.writes, [("edit", 20)])

    def test_managed_run_registers_once_on_publish_and_update(self):
        data = self.run_dir.parent / "data"
        (data / "run_data").mkdir(parents=True)
        original = self.run_dir
        self.run_dir = data / "run_data" / original.name
        original.rename(self.run_dir)
        self.args.run_dir = self.args.source = self.run_dir
        self.sibling(head="batch-a", number=10)
        url = pub.publish(self.args)
        (self.run_dir / "pr-title.txt").write_text("revised title")
        self.assertEqual(pub.publish(self.args), url)
        self.assertEqual(json.loads((data / "pr-tracking.json").read_text()), [
            {"pr_url": pr(10)["html_url"], "run_data": "data/run_data/batch-a", "pr_status": "unknown"},
            {"pr_url": url, "run_data": "data/run_data/batch-b", "pr_status": "open"}])

    def test_discover_existing_pr_without_saved_number(self):
        self.prs[20] = pr(20)
        pub.publish(self.args)
        self.assertEqual(self.writes, ["push", ("edit", 20), ("link", 20)])

    def test_foreign_saved_number_stops_even_if_merged(self):
        self.prs[20] = pr(20)
        pub.save(self.run_dir / "pr.json", dict(self.key, number="11"))
        self.assert_refused("saved PR belongs")

    def test_shared_source_branch_other_open_base_stops_before_push(self):
        self.prs[14] = pr(14, base="release")
        self.assert_refused("same fork branch")

    def test_multiple_exact_matches_stop_before_push(self):
        self.prs.update({20: pr(20), 21: pr(21)})
        self.assert_refused("Multiple PRs")

    def test_retarget_after_push_stops_before_pr_edit(self):
        self.prs[20] = pr(20)
        original_git = self.git
        def retarget(repo, *args, **kwargs):
            reply = original_git(repo, *args, **kwargs)
            if "push" in args:
                self.prs[20]["base"]["ref"] = "release"
            return reply
        with patch.object(pub, "git", side_effect=retarget):
            with self.assertRaisesRegex(pub.PublishError, "saved PR belongs"):
                pub.publish(self.args)
        self.assertEqual(self.writes, ["push"])

    def test_foreign_issue_on_matching_pr_stops_before_push(self):
        self.prs[20] = pr(20, body=ISSUE + "1")
        self.assert_refused("different or missing issue")

    def test_closed_exact_match_stops_before_push(self):
        self.prs[20] = pr(20, state="closed")
        self.assert_refused("closed or merged")

    def test_sibling_with_same_fork_ref_stops_before_push(self):
        self.sibling(number=20)
        self.assert_refused("already managed")

    def test_incomplete_sibling_identity_stops_before_push(self):
        self.sibling(head=None)
        self.assert_refused("Cannot identify")

    def test_conflicting_local_pr_numbers_stop_before_push(self):
        pub.save(self.run_dir / "publish-state.json", dict(self.key, number=10))
        pub.save(self.run_dir / "pr.json", dict(self.key, number=20))
        self.assert_refused("numbers disagree")

    def test_uncertain_previous_create_does_not_create_again(self):
        pub.save(self.run_dir / "publish-state.json", dict(self.key, commit=SHA, create_pending=True))
        self.assert_refused("pending confirmation")
