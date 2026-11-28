#!/usr/bin/env python3
"""Publish the reviewed AI_LOOP commit and PR text to GitCode. Python 3.9+."""
import argparse
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

from render_gpu_result import unittest_gpu_evidence
from pr_body_format import api_template, validate_api_body

from pr_tracking import file_lock, register_receipt, save


class PublishError(Exception):
    pass


def require(ok, message):
    if not ok:
        raise PublishError(message)


def read(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(value, dict), f"Expected an object in {path.name}")
    return value


def issue_lock(run, issue):
    # All runs under this run_data share this issue lock; the OS releases it on exit.
    path = run.parent / (".publish-" + hashlib.sha256(issue.encode()).hexdigest() + ".lock")
    return file_lock(path)


def invoke(*args, check=True, timeout=90, stdin=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    proc = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env, input=stdin)
    if check and proc.returncode:
        # Never echo credential-helper output, private payloads or full remote URLs.
        raise PublishError(f"{args[0]} failed (exit {proc.returncode}). Check authentication, access and connectivity.")
    return proc


def git(repo, *args, check=True):
    return invoke("git", "-C", str(repo), "-c", "credential.useHttpPath=true", *args, check=check)


def slug(url):
    m = re.fullmatch(r"(?:https://gitcode\.com/|ssh://git@gitcode\.com/|git@gitcode\.com:)"
                     r"([\w.-]+/[\w.-]+?)(?:\.git)?/?", url.strip(), re.I)
    require(m, "Expected a GitCode URL with a repository owner/name and no embedded credentials.")
    return m[1]


def rows(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("data", "items", "pull_requests", "files", "issues"):
            if isinstance(value.get(key), list):
                return value[key]
    raise PublishError("Unrecognized GitCode list response. Reconcile the remote result before retrying.")


def issue_urls(body):
    return {u.lower() for u in re.findall(
        r"https://gitcode\.com/[\w.-]+/[\w.-]+/issues/\d+(?![\w/])", body or "", re.I)}


def details(upstream, number):
    p = invoke("gitcode", "pr", "view", str(int(number)), "-R", upstream, "--json", check=False)
    obj = json.loads(p.stdout)
    require(isinstance(obj, dict) and str(obj.get("number")) == str(number)
            and obj.get("head") and obj.get("base") and obj.get("state")
            and isinstance(obj.get("body"), str), "Incomplete PR view response.")
    return obj


def identity(pr):
    return (pr["base"]["repo"]["full_name"].lower(), pr["base"]["ref"],
            pr["head"]["repo"]["full_name"].lower(), pr["head"]["ref"])


def find_pr(target, issue, known):
    upstream, base, fork, branch = target
    expected = (upstream.lower(), base, fork.lower(), branch)
    endpoint = f"repos/{upstream}/issues/{int(issue.rsplit('/', 1)[1])}/pull_requests"
    linked = {int(p["number"]) for p in rows(json.loads(invoke("gitcode", "api", endpoint).stdout))}
    known = int(known) if known else None
    # A push to the same fork/ref also updates open PRs targeting another base.
    listing = invoke("gitcode", "pr", "list", "-R", upstream, "--state", "all",
                     "--head", branch, "--paginate", "--json")
    listed = json.loads(listing.stdout)
    numbers = set(linked)
    if listed is not None:
        numbers.update(int(p["number"]) for p in rows(listed))
    if known is not None:
        numbers.add(known)
    matches = []
    for number in sorted(numbers):
        item = details(upstream, number)
        actual = identity(item)
        if actual != expected:
            require(number != known, "The saved PR belongs to a different branch or repository.")
            require(actual[2:] != expected[2:] or item["state"] not in ("open", "opened"),
                    "Another open PR uses the same fork branch. A push would also update it.")
            continue
        linked_in_text = issue_urls(item["body"])
        require(issue.lower() in linked_in_text or (number in linked and not linked_in_text),
                "Matching branch has a PR linked to a different or missing issue.")
        matches.append(item)
    if len(matches) > 1:
        # The platform can answer one create with two records for the same fork
        # branch. Keep the recorded or single open record; anything else still
        # needs manual reconciliation.
        preferred = [item for item in matches
                     if item["number"] == known or item["state"] in ("open", "opened")]
        require(len(preferred) == 1, "Multiple PRs matched this source and target. Resolve the ambiguity first.")
        matches = preferred
    if not matches:
        return None
    require(matches[0]["state"] in ("open", "opened") and not matches[0].get("merged"), "Existing PR is closed or merged.")
    return matches[0]


def remote_files(upstream, pr):
    expected_count = int(pr["changed_files"])
    require(expected_count > 0, "Expected a nonempty PR diff.")
    paths = set()
    page = 1
    while len(paths) < expected_count:
        endpoint = f"repos/{upstream}/pulls/{pr['number']}/files?page={page}&per_page=100"
        batch = rows(json.loads(invoke("gitcode", "api", endpoint).stdout))
        previous = len(paths)
        paths.update(item["filename"] for item in batch)
        require(previous < len(paths) <= expected_count, "Incomplete or inconsistent PR file listing.")
        page += 1
    return paths


def check_relation(upstream, pr_number, issue_number, *, establish=False):
    # POST takes a JSON array of issue numbers; GET is authoritative after a lost response.
    # https://docs.gitcode.com/docs/apis/post-api-v-5-repos-owner-repo-pulls-number-linked-issues/
    endpoint = f"repos/{upstream}/pulls/{pr_number}/issues"
    def read_numbers():
        data = rows(json.loads(invoke("gitcode", "api", endpoint).stdout))
        return {int(item["number"]) for item in data}
    links = read_numbers()
    require(not links or links == {issue_number}, "The PR already has a different linked issue.")
    if links or not establish:
        return
    try:
        invoke("gitcode", "api", endpoint, "--method", "POST", "-H", "Content-Type: application/json", "--input", "-",
               stdin=json.dumps([issue_number]), check=False)
    except subprocess.TimeoutExpired:
        pass
    require(read_numbers() == {issue_number},
            "Issue association is not confirmed. Reconcile the existing PR before completing publication.")


def ref_sha(repo, push_url, branch):
    lines = git(repo, "ls-remote", "--heads", push_url, f"refs/heads/{branch}").stdout.splitlines()
    if not lines:
        return None
    require(len(lines) == 1, "Ambiguous remote branch.")
    value, ref = lines[0].split()
    require(ref == f"refs/heads/{branch}", "Unexpected remote branch response.")
    return value


def check_single_commit(repo, base_sha, code):
    commits = git(repo, "rev-list", "--max-count=2", "--parents", f"{base_sha}..{code}").stdout.splitlines()
    require(len(commits) == 1 and len(commits[0].split()) == 2 and commits[0].split()[0] == code,
            "The PR must contain exactly one non-merge commit. Consolidate the task branch before review.")


def push_reviewed(repo, url, branch, code, pr, expected):
    ref = f"refs/heads/{branch}"
    remote = ref_sha(repo, url, branch)
    if expected is not None:
        require(pr and re.fullmatch("[0-9a-f]{40}", expected),
                "--expected-head must identify the original full SHA of an existing PR.")
    if remote == code:
        return
    options = []
    if pr:
        require(expected and remote == expected and pr["head"]["sha"] == expected,
                "PR head differs from the pre-edit SHA or --expected-head is missing. Reconcile before updating.")
        options = [f"--force-with-lease={ref}:{expected}"]
    elif remote is None:
        options = [f"--force-with-lease={ref}:"]
    git(repo, "-c", "push.followTags=false", "-c", "remote.origin.mirror=false", "push",
        *options, "origin", f"{code}:{ref}")


def delivery_status(run, adapt):
    statuses = adapt.get("api-status", {})
    require(isinstance(statuses, dict), "Invalid api-status in adapt-information.json.")
    tasks = adapt.get("task-branches")
    result_path = run / "api-loop/result.json"
    api_plan = any(item.get("agentname") == "api-adaptation"
                   for item in (adapt.get("adapt-plan") or {}).values() if isinstance(item, dict))
    if tasks is None and not api_plan and not result_path.is_file():
        return statuses
    require(isinstance(tasks, list), "发布清单缺少 task-branches。")
    apis = [task.removeprefix("adapt-") for task in tasks if task.startswith("adapt-")]
    require(apis or not api_plan, "发布清单没有保留的 API。")
    retained = {task: statuses[task] for task in tasks if task in statuses and not task.startswith("adapt-")}
    if not apis:
        return retained

    require(result_path.is_file(), "缺少本批 API 逐项结果 api-loop/result.json。")
    rows = read(result_path).get("results")
    require(isinstance(rows, list), "api-loop/result.json 缺少 results 数组。")
    by_api = {}
    for row in rows:
        require(isinstance(row, dict), "无效的 API 结果记录。")
        by_api.setdefault(row.get("api"), []).append(row)

    errors = []
    for api in apis:
        matches = by_api.get(api, [])
        if len(matches) != 1:
            errors.append(f"{api}: 对应 {len(matches)} 条结果，应有 1 条")
            continue
        row = matches[0]
        name = row.get("database_api") or api
        status = statuses.get(name)
        step, outcome, execution = row.get("next_step"), row.get("status"), row.get("execution")
        needs_test = row.get("strategy") in ("待适配", "补充testcase") or row.get("test_files") or row.get("adaptation")
        if (status != "支持" or outcome != "支持" or step != "step4"
                or (needs_test and execution != "pass")
                or execution in ("partial", "fail", "skip", "blocked")):
            errors.append(f"{api}: api-status={status}, status={outcome}, next_step={step}, execution={execution}")
        else:
            retained[name] = status
    require(not errors, "以下 API 未满足发布条件：\n" + "\n".join(errors))
    return retained


def check_gpu_evidence(run, repo, code, verification, changed_paths, body):
    """Compare API test evidence with Git blobs before publishing any remote change."""
    supplemental = {p for p in changed_paths if p.startswith("test/api_supplement/")
                    and Path(p).match("test_*.py")}
    if not supplemental and verification != (run / "api-loop/result.json").resolve():
        return
    handoff = read(verification) if verification.suffix.lower() == ".json" else {}
    entries = handoff.get("results", [])
    if not supplemental and not any("test_files" in e for e in entries):
        return

    @lru_cache(maxsize=None)
    def digest(path):
        proc = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", f"{code}:{path}"],
                              capture_output=True, timeout=90)
        require(proc.returncode == 0, f"Cannot read reviewed file {path} at {code}.")
        return hashlib.sha256(proc.stdout).hexdigest()

    @lru_cache(maxsize=None)
    def load_report(path):
        return read(path)

    @lru_cache(maxsize=None)
    def unittest_proof(path, helpers):
        return unittest_gpu_evidence(path, load_report(path), repo, helpers)[0]

    @lru_cache(maxsize=None)
    def manifest_files(path, expected_hash):
        if not path.is_file():
            return {}
        raw = path.read_bytes()
        return json.loads(raw).get("files", {}) if hashlib.sha256(raw).hexdigest() == expected_hash else {}

    paths = set(supplemental)
    for entry in entries:
        paths.update(entry.get("test_files", []))
        paths.update(((entry.get("device_validation") or {}).get("npu") or {}).get("handoff_files", {}))
    if not paths:
        return
    retained = set(git(repo, "--literal-pathspecs", "ls-tree", "-rz", "--name-only", code,
                       "--", *sorted(paths)).stdout.split("\0"))
    unlisted = supplemental & retained
    missing = set()
    for entry in entries:
        tests = set(entry.get("test_files", [])) & retained
        if not tests:
            continue
        unlisted.difference_update(tests)
        devices = entry.get("device_validation", {})
        cuda = devices.get("cuda") or {}
        report_path = run / (cuda.get("report") or "")
        report = load_report(report_path) if report_path.is_file() and report_path.suffix == ".json" else {}
        helpers = set((devices.get("npu") or {}).get("handoff_files", {})) - set(entry.get("test_files", []))
        if report.get("device_kind") == "GPU":
            proof = unittest_proof(report_path, frozenset(helpers))
            if proof["helper_files"] - retained:
                retained.update(git(repo, "--literal-pathspecs", "ls-tree", "-rz", "--name-only", code,
                                    "--", *sorted(proof["helper_files"] - retained)).stdout.split("\0"))
            helpers = helpers | proof["helper_files"]
            files = {f: proof["file_hashes"].get(f) for f in proof["verified_files"]}
            if (cuda.get("execution") in {"blocked", "not_run"} or
                    (cuda.get("head_sha") and proof["head_sha"] != cuda["head_sha"]) or
                    (cuda.get("run_id") and proof.get("run_id") and str(proof["run_id"]) != str(cuda["run_id"])) or
                    any(p not in retained or proof["file_hashes"].get(p) != digest(p) for p in helpers)):
                files = {}
        else:
            api = cuda.get("api") or entry.get("api")
            evidence = next((item for item in report.get("results", []) if item.get("api") == api), {})
            if (report.get("device") != "cuda" or cuda.get("execution") in {"blocked", "not_run"}
                    or (cuda.get("run_id") and str(cuda["run_id"]) != str(report.get("run_id")))):
                evidence = {}
            files = {f["file"]: f.get("sha256") for f in evidence.get("files", [])
                     if f.get("execution") in {"pass", "partial", "fail", "skip"}}
            if helpers:
                manifest = manifest_files(report_path.parent.parent / "manifest.json", report.get("manifest_sha256"))
                if any(p not in retained or manifest.get(p) != digest(p) for p in helpers):
                    files = {}
        missing.update(p for p in tests if files.get(p) != digest(p))
    missing.update(unlisted)
    if missing:
        # Only the final-result cell of a GPU device row can declare partial coverage.
        gpu_results = []
        for line in body.splitlines():
            if line.lstrip().startswith("|"):
                cells = line.strip().strip("|").split("|")
                if re.match(r"^(GPU\b|CUDA\b|NVIDIA\b)", cells[0].strip(), re.I):
                    gpu_results.append(cells[-1])
        require(any(re.search(r"待验证|待复测|pending|not verified", cell, re.I) for cell in gpu_results),
                "GPU evidence is missing or stale for: " + ", ".join(sorted(missing))
                + ". Run api-adaptation Step 1 for the affected files, or mark the GPU result 待验证 with its scope.")


def rejected_create(proc, response):
    """Recognize an explicit HTTP rejection, never a timeout, conflict or ambiguous error."""
    if not proc.returncode or (isinstance(response, dict) and response.get("number")):
        return None
    error_text = proc.stderr.strip()
    if isinstance(response, dict):
        error_text += "\n" + str(response.get("error") or response.get("message") or "")
    # A failed follow-up read cannot prove the earlier POST failed.
    if re.search(r"\b(?:GET|PATCH|HEAD|PUT|DELETE)\s+\S+", error_text):
        return None
    match = re.search(r"\bHTTP(?:/[\d.]+)?\s+(401|403|422)\b|"
                      r"(?:^|\n)\s*(?:Error:\s*)?(401 Unauthorized|403 Forbidden|422 Unprocessable (?:Entity|Content))\b",
                      error_text, re.I)
    return int((match[1] or match[2])[:3]) if match else None


def publish(args):
    run = args.run_dir.resolve()
    issue = read(run / "issue.json")
    adapt = read(run / "adapt-information.json")
    basic_file = run / "basic_information.json"
    basic = read(basic_file) if basic_file.exists() else {}
    if args.source:
        repo = args.source.resolve()
    else:
        source = basic["torch_npu_latest_source"]
        require(Path(source).is_absolute() or (run.parent.name == "run_data" and run.parent.parent.name == "data"),
                "Relative source requires the original data/run_data layout or --source.")
        repo = (run.parent.parent.parent / source).resolve()
    upstream = slug(git(repo, "config", "--get", "remote.upstream.url").stdout)
    # Multiple configured push destinations can have side effects outside the fork.
    push_urls = git(repo, "config", "--get-all", "remote.origin.pushurl", check=False)
    require(push_urls.returncode in (0, 1), "Cannot read origin push URL.")
    urls = push_urls.stdout.splitlines() or git(repo, "config", "--get-all", "remote.origin.url").stdout.splitlines()
    require(len(urls) == 1, "Expected a single fork for origin.")
    fork = slug(urls[0])
    configured_fork = basic.get("preparation_config", {}).get("torch_npu_fork_repo")
    if configured_fork:
        require(fork.lower() == slug(configured_fork).lower(), "Origin push target differs from the fork in the handoff.")
    require(fork.lower() != upstream.lower(), "origin must be the fork; upstream is the target.")
    number = int(issue["number"])
    issue_url = issue["url"].rstrip("/")
    require(issue_url.lower() == f"https://gitcode.com/{upstream}/issues/{number}".lower()
            and upstream.lower() == issue["repository"].lower(), "Issue and upstream repository differ.")
    branch = adapt["development-branch"]
    base = args.base
    for ref in (branch, base):
        git(repo, "check-ref-format", "--branch", ref)
    code = args.commit.lower()
    require(re.fullmatch("[a-f0-9]{40}", code), "Supply the full reviewed commit SHA.")
    require(git(repo, "branch", "--show-current").stdout.strip() == branch, "Checkout differs from the handoff branch.")
    require(git(repo, "rev-parse", "HEAD").stdout.strip() == code, "HEAD differs from the reviewed commit.")
    require(not git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout, "Commit reviewed task changes before publishing.")
    title = (run / "pr-title.txt").read_text(encoding="utf-8-sig").strip()
    body_path = run / "pr-body.md"
    body = body_path.read_text(encoding="utf-8-sig")
    require(title and "\n" not in title and body.strip(), "Supply a one-line title and a nonempty PR body.")
    require(issue_url.lower() in issue_urls(body), "PR body must link the handoff issue using its full URL.")
    verification = (run / args.verification).resolve()
    require(verification.is_file(), "Missing verification handoff; supply --verification PATH if needed.")
    target = upstream, base, fork, branch
    state_path, record_path = run / "publish-state.json", run / "pr.json"
    key = {"repository": upstream, "base": base, "fork": fork, "head": branch, "issue-url": issue_url}

    with issue_lock(run, issue_url.lower()), TemporaryDirectory() as staging:
        require(adapt == read(run / "adapt-information.json") and not adapt.get("withdrawing-api"),
                "Task scope changed; finish withdrawal and review the current handoff.")
        retained = delivery_status(run, adapt)
        if adapt.get("withdrawn-apis"):
            require(retained, "No retained API status to publish after withdrawal.")
        for filename in ("publish-state.json", "pr.json"):
            for other in run.parent.glob(f"*/{filename}"):
                if other.parent == run:
                    continue
                r = read(other)
                if str(r.get("issue-url", "")).rstrip("/").lower() == issue_url.lower():
                    require(r.get("fork") and r.get("head"),
                            f"Cannot identify the fork branch managed by run {other.parent.name}.")
                    require((r["fork"].lower(), r["head"]) != (fork.lower(), branch),
                            f"This fork branch is already managed by run {other.parent.name}. Resume that run.")
        state = read(state_path) if state_path.exists() else {}
        record = read(record_path) if record_path.exists() else {}
        for prior in (state, record):
            if prior:
                require(all(prior.get(k) == v for k, v in key.items()), "Publication identity changed. Inspect the existing record.")
        known = state.get("number") or record.get("number")
        if state.get("number") and record.get("number"):
            require(int(state["number"]) == int(record["number"]), "Saved PR numbers disagree.")
        if state.get("create_pending") and not state.get("create_rejection_http"):
            require(state.get("commit") == code, "Recover the previous create before publishing a different commit.")
        invoke("gitcode", "auth", "status")
        pr = find_pr(target, issue_url, known)
        require(pr or not state.get("create_pending") or state.get("create_rejection_http") in (401, 403, 422),
                "Previous create is pending confirmation. Same search returned no PR. No second create was sent.")
        if pr:
            check_relation(upstream, pr["number"], number)
        git(repo, "fetch", "--no-tags", "upstream", f"refs/heads/{base}")
        base_sha = git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()
        check_single_commit(repo, base_sha, code)
        filenames = set(git(repo, "diff", "--name-only", "-z", "--find-renames",
                            f"{base_sha}...{code}").stdout.rstrip("\0").split("\0"))
        require(filenames != {""}, "Reviewed commit has no changes against the target.")
        try:
            template = api_template(run, adapt, basic, verification)
            if template:
                upstream_template = git(repo, "show", f"{base_sha}:.gitcode/PULL_REQUEST_TEMPLATE.md", check=False)
                validate_api_body(body, template, filenames,
                                  upstream_template.stdout if upstream_template.returncode == 0 else None,
                                  Path(__file__).resolve().parents[1] / "data/api-support.sqlite3")
        except ValueError as error:
            raise PublishError(f"PR 草稿格式错误：{error}") from error
        # Always send the exact content checked here, even if another process edits the run draft.
        body_path = Path(staging) / "pr-body.md"
        body_path.write_bytes(body.encode("utf-8"))
        check_gpu_evidence(run, repo, code, verification, filenames, body)
        state.update(key, commit=code)
        save(state_path, state)
        push_reviewed(repo, urls[0], branch, code, pr, getattr(args, "expected_head", None))
        require(ref_sha(repo, urls[0], branch) == code, "Remote fork does not point to the reviewed commit.")
        if not pr:
            state["create_pending"] = True
            state.pop("create_rejection_http", None)
            save(state_path, state)
            rejection = None
            try:
                p = invoke("gitcode", "pr", "create", "-R", upstream, "--fork", fork, "--head", branch,
                           "--base", base, "--title", title, "--body-file", str(body_path), "--json", check=False)
                try:
                    response = json.loads(p.stdout)
                except ValueError:
                    response = {}
                rejection = rejected_create(p, response)
                if rejection:
                    state["create_rejection_http"] = rejection
                    save(state_path, state)
                if isinstance(response, dict) and str(response.get("number", "")).isdigit():
                    state["number"] = int(response["number"])
                    save(state_path, state)
            except subprocess.TimeoutExpired:
                pass  # The server may already have created it.
            pr = find_pr(target, issue_url, state.get("number"))
            if not pr and rejection:
                state.update(create_pending=False, create_rejection_http=rejection)
                save(state_path, state)
                raise PublishError(f"Create rejected (HTTP {rejection}); no matching PR found. Correct the rejection and retry.")
            require(pr, "Create result is uncertain. Run again to reconcile; a retry will not create a duplicate.")
        state.update(number=int(pr["number"]), create_pending=False)
        save(state_path, state)
        pr = find_pr(target, issue_url, state["number"])
        require(pr and pr["head"]["sha"] == code,
                "PR head has not reached the reviewed commit; retry after the platform updates.")
        if pr.get("title") != title or pr["body"].strip() != body.strip():
            invoke("gitcode", "pr", "edit", str(pr["number"]), "-R", upstream, "--title", title,
                   "--body-file", str(body_path), "--json")
        pr = find_pr(target, issue_url, state["number"])
        require(pr["head"]["sha"] == code and pr["title"] == title and pr["body"].strip() == body.strip(),
                "PR readback differs from reviewed commit, title or body.")
        check_relation(upstream, pr["number"], number, establish=True)
        require(remote_files(upstream, pr) == filenames, "PR file list differs from reviewed diff.")
        url = pr["html_url"]
        require(isinstance(url, str) and url.lower() == f"https://gitcode.com/{upstream}/merge_requests/{pr['number']}".lower(),
                "PR URL does not match the target repository and number.")
        receipt = dict(record, **key)
        receipt.update({"status": "created", "number": int(pr["number"]), "url": url, "commit": code,
                        "remote-sha": code, "run-id": run.name, "verification": str(args.verification),
                        "api-status": retained})
        register_receipt(run, receipt)
        return url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--commit", required=True, help="Full SHA recorded when the development changes were reviewed.")
    parser.add_argument("--expected-head", help="Full remote PR head SHA verified before starting this update.")
    parser.add_argument("--base", default="master", help="AI_LOOP defaults to master; override for another target.")
    parser.add_argument("--source", type=Path, help="Source checkout if handoff paths moved.")
    parser.add_argument("--verification", type=Path, default=Path("api-loop/result.json"),
                        help="Existing verification record, relative to --run-dir or absolute.")
    args = parser.parse_args()
    try:
        print(publish(args))
    except (PublishError, OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as error:
        message = "Command timed out; retry to check remote state." if isinstance(error, subprocess.TimeoutExpired) else str(error)
        parser.exit(1, message + "\n")


if __name__ == "__main__":
    main()
