import hashlib
import json
from pathlib import Path
import subprocess
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
        self.remote = None
        self.parents = SHA + " " + "a" * 40
        for name, impl in (("invoke", self.gitcode), ("git", self.git)):
            mock = patch.object(pub, name, side_effect=impl)
            mock.start()
            self.addCleanup(mock.stop)

    def git(self, repo, *args, **kwargs):
        if "push" in args:
            self.assertEqual(args[-1], f"{SHA}:refs/heads/{BRANCH}")
            self.writes.append("push")
            self.remote = SHA
            if 20 in self.prs:
                self.prs[20]["head"]["sha"] = SHA
        data = ""
        if args[0] == "config":
            data = {"remote.upstream.url": UPSTREAM, "remote.origin.pushurl": FORK}[args[-1]]
            data = "https://gitcode.com/" + data
        elif args[0] == "branch":
            data = BRANCH
        elif args[0] == "rev-parse":
            data = SHA
        elif args[0] == "rev-list":
            data = self.parents
        elif args[0] == "diff":
            data = "test.py\0"
        elif args[0] == "ls-remote" and self.remote:
            data = self.remote + "\trefs/heads/" + BRANCH
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

    def test_api_template_blocks_old_body_before_create_or_edit(self):
        adapt = pub.read(self.run_dir / "adapt-information.json")
        adapt["adapt-plan"] = {"step1": {"agentname": "api-adaptation"}}
        pub.save(self.run_dir / "adapt-information.json", adapt)
        pub.save(self.run_dir / "basic_information.json", {"venv": "data/venv/torch-2.15/python-3.11"})
        self.assert_refused("PR 草稿格式错误")
        self.prs[20] = pr(20)
        self.remote = SHA
        self.assert_refused("PR 草稿格式错误")

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
        self.remote = SHA
        pub.publish(self.args)
        self.assertEqual(self.writes, [("edit", 20), ("link", 20)])

    def test_reject_multiple_or_merge_commits_before_publication(self):
        for parents in (self.parents + "\n" + "a" * 40 + " " + "b" * 40,
                        self.parents + " " + "b" * 40, ""):
            self.parents = parents
            self.assert_refused("exactly one non-merge commit")

    def test_update_requires_original_head(self):
        self.prs[20] = pr(20)
        self.remote = self.prs[20]["head"]["sha"] = "b" * 40
        self.assert_refused("pre-edit SHA")
        self.args.expected_head = "c" * 40
        self.assert_refused("pre-edit SHA")
        self.args.expected_head = self.remote
        pub.publish(self.args)
        self.assertEqual(self.remote, SHA)
        self.assertEqual(pub.read(self.run_dir / "pr.json")["remote-sha"], SHA)

    def test_foreign_saved_number_stops_even_if_merged(self):
        self.prs[20] = pr(20)
        pub.save(self.run_dir / "pr.json", dict(self.key, number="11"))
        self.assert_refused("saved PR belongs")

    def test_shared_source_branch_other_open_base_stops_before_push(self):
        self.prs[14] = pr(14, base="release")
        self.assert_refused("same fork branch")

    def test_evidence_rejection_precedes_push_and_receipt(self):
        with patch.object(pub, "check_gpu_evidence", side_effect=pub.PublishError("GPU evidence is stale")):
            self.assert_refused("GPU evidence is stale")
        self.assertFalse((self.run_dir / "publish-state.json").exists())

    def test_multiple_exact_matches_stop_before_push(self):
        self.prs.update({20: pr(20), 21: pr(21)})
        self.assert_refused("Multiple PRs")

    def test_retarget_after_push_stops_before_pr_edit(self):
        self.prs[20] = pr(20)
        self.args.expected_head = self.remote = self.prs[20]["head"]["sha"] = "b" * 40
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


class SingleCommitGit(unittest.TestCase):
    def test_amend_and_concurrent_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "source"
            fork = Path(tmp) / "fork.git"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "init", "-q", "--bare", str(fork)], check=True)
            def git(*args):
                return pub.git(repo, *args).stdout.strip()
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            git("config", "commit.gpgsign", "false")
            git("remote", "add", "origin", str(fork))
            git("commit", "--allow-empty", "-qm", "base")
            base = git("rev-parse", "HEAD")
            git("checkout", "-qb", BRANCH)
            path = repo / "test.py"
            path.write_text("v1")
            git("add", path.name)
            git("commit", "-qm", "test coverage")
            first = git("rev-parse", "HEAD")
            pub.push_reviewed(repo, str(fork), BRANCH, first, None, None)
            path.write_text("v2")
            git("commit", "-qam", "review fix")
            with self.assertRaisesRegex(pub.PublishError, "exactly one"):
                pub.check_single_commit(repo, base, git("rev-parse", "HEAD"))
            git("reset", "--soft", base)
            git("commit", "-qm", "revised test coverage")
            revised = git("rev-parse", "HEAD")
            pub.check_single_commit(repo, base, revised)
            current_pr = {"head": {"sha": first}}
            pub.push_reviewed(repo, str(fork), BRANCH, revised, current_pr, first)
            self.assertEqual(pub.ref_sha(repo, str(fork), BRANCH), revised)
            self.assertEqual(git("rev-list", "--count", f"{base}..{revised}"), "1")
            path.write_text("v3")
            git("commit", "-qa", "--amend", "--no-edit")
            candidate = git("rev-parse", "HEAD")
            # Another push won after the initial read. The Git server must reject the stale lease.
            git("push", f"--force-with-lease=refs/heads/{BRANCH}:{revised}",
                "origin", f"{first}:refs/heads/{BRANCH}")
            with patch.object(pub, "ref_sha", return_value=revised), self.assertRaises(pub.PublishError):
                pub.push_reviewed(repo, str(fork), BRANCH, candidate, {"head": {"sha": revised}}, revised)
            self.assertEqual(pub.ref_sha(repo, str(fork), BRANCH), first)


class GPUHandoff(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run = Path(temp.name)
        self.repo = self.run / "source"
        self.repo.mkdir()
        self.file = "test/api_supplement/test_example.py"
        self.helper = "test/api_supplement/data.bin"
        (self.repo / self.file).parent.mkdir(parents=True)
        (self.repo / self.file).write_bytes(b"def test_example(): pass\n")
        (self.repo / self.helper).write_bytes(b"\xff\x00\r\n")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                        "commit", "--no-gpg-sign", "-qm", "reviewed files"], cwd=self.repo, check=True)
        self.sha = pub.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.hashes = {p: hashlib.sha256(subprocess.check_output(
            ["git", "show", f"{self.sha}:{p}"], cwd=self.repo)).hexdigest() for p in [self.file, self.helper]}
        self.verification = self.run / "api-loop/result.json"
        self.verification.parent.mkdir()
        self.report = self.run / "batch/evidence/report.json"
        self.report.parent.mkdir(parents=True)
        self.manifest = self.run / "batch/manifest.json"
        self.manifest.write_text(json.dumps({"files": self.hashes}))
        self.gpu = {"device": "cuda", "run_id": 123, "manifest_sha256": hashlib.sha256(
            self.manifest.read_bytes()).hexdigest(), "results": [{"api": "api", "files": [
                {"file": self.file, "sha256": self.hashes[self.file], "execution": "pass"}]}]}
        self.entry = {"api": "api", "test_files": [self.file], "device_validation": {
            "npu": {"handoff_files": self.hashes}, "cuda": {
                "api": "api", "run_id": 123, "execution": "pass", "report": "batch/evidence/report.json"}}}

    def check(self, status="全部通过", extra=()):
        pub.save(self.report, self.gpu)
        pub.save(self.verification, {"results": [self.entry]})
        pub.check_gpu_evidence(self.run, self.repo, self.sha, self.verification,
                               {self.file, *extra}, f"| GPU RTX | {status} |")

    def test_current_report_and_removed_test(self):
        removed = "test/api_supplement/test_removed.py"
        self.entry["test_files"].append(removed)
        self.check(extra=[removed])

    def test_stale_test_rejects_pass_but_allows_explicit_pending(self):
        self.gpu["results"][0]["files"][0]["sha256"] = "a" * 64
        with self.assertRaisesRegex(pub.PublishError, self.file):
            self.check()
        self.check("1 个文件待验证")

    def test_changed_helper_invalidates_dependent_test(self):
        self.manifest.write_text(json.dumps({"files": {self.helper: "b" * 64}}))
        self.gpu["manifest_sha256"] = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        with self.assertRaisesRegex(pub.PublishError, self.file):
            self.check()

    def test_unlisted_new_file_and_missing_report_are_not_verified(self):
        self.entry["test_files"] = []
        with self.assertRaisesRegex(pub.PublishError, self.file):
            self.check()
        self.entry["test_files"] = [self.file]
        self.entry["device_validation"]["cuda"]["report"] = "absent.json"
        with self.assertRaisesRegex(pub.PublishError, self.file):
            self.check()
