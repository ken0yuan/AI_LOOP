#!/usr/bin/env python3
"""Publish the reviewed AI_LOOP commit and PR text to GitCode. Python 3.9+."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


class PublishError(Exception):
    pass


def require(ok, message):
    if not ok:
        raise PublishError(message)


def read(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(value, dict), f"Expected an object in {path.name}")
    return value


def save(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def issue_lock(run, issue):
    # All runs under this run_data share this issue lock; the OS releases it on exit.
    path = run.parent / (".publish-" + hashlib.sha256(issue.encode()).hexdigest() + ".lock")
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


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
    numbers = set(linked)
    if known:
        numbers.add(int(known))
    if not known or not linked:
        listing = invoke("gitcode", "pr", "list", "-R", upstream, "--state", "all",
                         "--head", branch, "--base", base, "--paginate", "--json")
        # GitCode CLI v0.16.0 emits JSON null for an empty paginated slice.
        listed = json.loads(listing.stdout)
        if listed is not None:
            numbers.update(int(p["number"]) for p in rows(listed))
    matches = []
    for number in sorted(numbers):
        item = details(upstream, number)
        if identity(item) != expected:
            require(number not in linked and number != known,
                    "The issue or the saved record already points to a different PR branch or repository.")
            continue
        linked_in_text = issue_urls(item["body"])
        require(issue.lower() in linked_in_text or (number in linked and not linked_in_text),
                "Matching branch has a PR linked to a different or missing issue.")
        matches.append(item)
    require(len(matches) <= 1, "Multiple PRs matched this source and target. Resolve the ambiguity first.")
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


def owned_status(adapt):
    statuses = adapt.get("api-status", {})
    require(isinstance(statuses, dict), "Invalid api-status in adapt-information.json.")
    tasks = adapt.get("task-branches")
    if tasks is None:
        return statuses
    return {key: value for key, value in statuses.items()
            if key in tasks or "adapt-" + key in tasks}


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

    with issue_lock(run, issue_url.lower()):
        for filename in ("publish-state.json", "pr.json"):
            for other in run.parent.glob(f"*/{filename}"):
                if other.parent == run:
                    continue
                r = read(other)
                require(str(r.get("issue-url", "")).rstrip("/").lower() != issue_url.lower(),
                        f"This issue is already managed by run {other.parent.name}. Resume that run.")
        state = read(state_path) if state_path.exists() else {}
        record = read(record_path) if record_path.exists() else {}
        for prior in (state, record):
            if prior:
                require(all(prior.get(k) == v for k, v in key.items()), "Publication identity changed. Inspect the existing record.")
        known = state.get("number") or record.get("number")
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
        filenames = set(git(repo, "diff", "--name-only", "-z", "--find-renames",
                            f"{base_sha}...{code}").stdout.rstrip("\0").split("\0"))
        require(filenames != {""}, "Reviewed commit has no changes against the target.")
        state.update(key, commit=code)
        save(state_path, state)
        if ref_sha(repo, urls[0], branch) != code:
            git(repo, "-c", "push.followTags=false", "-c", "remote.origin.mirror=false", "push",
                "origin", f"{code}:refs/heads/{branch}")
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
        require(pr["head"]["sha"] == code or details(upstream, pr["number"])["head"]["sha"] == code,
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
                        "api-status": owned_status(adapt)})
        save(record_path, receipt)
        return url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--commit", required=True, help="Full SHA recorded when the development changes were reviewed.")
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
