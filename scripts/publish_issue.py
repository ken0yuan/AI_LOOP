#!/usr/bin/env python3
"""Publish or reuse the reviewed Step 4.1 issue draft on GitCode. Python 3.9+."""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

import issue_gitcode as remote
from publish_pr import PublishError, issue_lock, read, require, save, slug


STATE = "issue-publish-state.json"
SEARCH = "issue-search.json"


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def content_key(title, body):
    return digest([title, body.replace("\r\n", "\n").rstrip("\n")])


def issue_number(value):
    require(re.fullmatch(r"[1-9][0-9]*", str(value)), "Expected a positive issue number.")
    return int(value)


def linked_number(url, repo):
    link = urlsplit(url)
    require(link.scheme in ("https", "http") and link.hostname, "Invalid issue URL.")
    if link.hostname.lower() != "gitcode.com":
        return None
    match = re.fullmatch(r"/([\w.-]+/[\w.-]+)/issues/([1-9][0-9]*)/?", link.path)
    require(match, "Expected a GitCode issue URL ending in /issues/NUMBER.")
    return int(match[2]) if match[1].casefold() == repo.casefold() else None


@contextmanager
def body_snapshot(run, body):
    # Send the text that was checked, even if another process changes the draft.
    with tempfile.TemporaryDirectory(dir=run, prefix=".issue-body-") as directory:
        name = Path(directory) / "issue-body.md"
        with name.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
        yield name


def load_views(repo, numbers, *, allow_missing=False):
    issues = {}
    for number in sorted(numbers):
        try:
            issues[number] = remote.view(repo, number)
        except remote.IssueNotFound:
            if not allow_missing:
                raise PublishError(f"Issue #{number} disappeared during search. Retry the lookup.")
    return issues


def search_repo(repo, queries):
    numbers = set()
    for query in dict.fromkeys(queries):
        numbers.update(remote.search(repo, query))
    return numbers


def decision_view(item):
    return {key: item[key] for key in ("number", "title", "body", "html_url", "state")}


def finish(run, repo, current, metadata, status, receipt):
    """Persist the verified remote view obtained by the operation."""
    number = issue_number(current["number"])
    same_record = (str(receipt.get("repository", "")).casefold() == repo.casefold()
                   and str(receipt.get("number")) == str(number))
    if (same_record and receipt.get("status") == "created"
            and receipt.get("run-id", receipt.get("run_id", run.name)) == run.name):
        status = "created"
    result = dict(receipt) if same_record else {}
    result.update({"status": status, "repository": repo, "number": number,
                   "url": current["html_url"], "run-id": run.name,
                   "title": current["title"], "body": current["body"]})
    if "run_id" in result:
        result["run_id"] = run.name
    save(run / STATE, dict(metadata, phase="confirmed", number=number, status=status,
                           remote_digest=digest(decision_view(current))))
    save(run / "issue.json", result)
    return result


def apply_edit(run, repo, item, body):
    number = item["number"]
    require(decision_view(remote.view(repo, number)) == decision_view(item),
            "The selected issue changed after review. Re-run before editing.")
    with body_snapshot(run, body) as snapshot:
        try:
            remote.edit(repo, number, snapshot)
        except (PublishError, subprocess.TimeoutExpired):
            pass  # A lost PATCH response may still have changed the body.
    updated = remote.view(repo, number)
    require(updated["title"] == item["title"]
            and content_key("", updated["body"]) == content_key("", body),
            "The requested body update was not confirmed. Review the remote issue and retry.")
    return updated


def stored(path):
    return read(path) if path.is_file() else {}


def same_repo(record, repo):
    return str(record.get("repository", "")).casefold() == repo.casefold()


def recent_numbers(run, repo, terms):
    """Find matching local readbacks while GitCode's search index catches up."""
    if not terms:
        return set()
    numbers = set()
    for record in run.parent.glob("*/issue.json"):
        if record.parent == run:
            continue
        receipt = read(record)
        if not same_repo(receipt, repo):
            continue
        # Older receipts may only carry the repository and number.
        text = "\n".join(value for value in (receipt.get("title"), receipt.get("body"))
                         if isinstance(value, str)).casefold()
        if any(term.casefold() in text for term in terms):
            numbers.add(issue_number(receipt["number"]))
    return numbers


def lookup(run, repo, draft, previous, keywords, link, receipt, intent, *, refresh):
    """Execute only caller-supplied searches; keep the results together for this draft."""
    terms = list(dict.fromkeys(previous.get("terms", []) + keywords))
    require(all(isinstance(q, str) and q.strip() for q in terms), "Empty --keyword.")
    hints = set(previous.get("hints", []))
    for record in (receipt, intent):
        if same_repo(record, repo) and record.get("number"):
            hints.add(issue_number(record["number"]))
    if link:
        number = linked_number(link, repo)
        if number:
            hints.add(number)
    snapshot = {"repository": repo, "draft": draft, "terms": terms, "hints": sorted(hints),
                "versions": previous.get("versions", {})}
    # An interrupted or failed search must still be completed and shown to the caller.
    save(run / SEARCH, dict(snapshot, incomplete=True))
    numbers = hints.union(int(n) for n in snapshot["versions"])
    numbers.update(recent_numbers(run, repo, terms))
    queries = terms if refresh or previous.get("incomplete") or not keywords else keywords
    found = search_repo(repo, queries)
    issues = load_views(repo, found)
    issues.update(load_views(repo, numbers - found, allow_missing=True))
    entries = [decision_view(issues[n]) for n in sorted(issues)]
    snapshot["versions"] = {str(item["number"]): digest(item) for item in entries}
    save(run / SEARCH, snapshot)
    return issues, snapshot, {"status": "searched", "repository": repo, "keywords": terms, "issues": entries}


def pending(repo, message, number=None):
    return {"status": "pending", "repository": repo, "number": number, "message": message}


def confirm_post(run, repo, intent, receipt):
    """Verify the response to the agent's create operation by its returned number."""
    number = intent.get("number")
    if not number:
        return pending(repo, "No issue number was returned. Search, compare the remote issues and reuse the confirmed number.")
    try:
        item = remote.view(repo, number)
    except remote.IssueNotFound:
        return pending(repo, "The returned issue number is not yet visible. Retry this run.", number)
    if intent.get("phase") == "confirmed":
        if digest(decision_view(item)) != intent.get("remote_digest"):
            return pending(repo, "The previously confirmed issue changed. Search and review it before continuing.", number)
        return finish(run, repo, item, intent, intent["status"], receipt)
    if content_key(item["title"], item["body"]) != intent["draft"]:
        return pending(repo, "Remote content differs from the submitted draft. Search and compare this number.", number)
    return finish(run, repo, item, intent, "created", receipt)


def submit(run, repo, title, body, metadata, receipt):
    intent = dict(metadata, phase="creating")
    with body_snapshot(run, body) as snapshot:
        save(run / STATE, intent)
        try:
            number, rejection = remote.create(repo, title, snapshot)
        except FileNotFoundError:
            save(run / STATE, dict(metadata, phase="rejected"))
            raise
        except subprocess.TimeoutExpired:
            number, rejection = None, None
    if rejection and not number:
        save(run / STATE, dict(metadata, phase="rejected"))
        raise PublishError(f"Create rejected (HTTP {rejection}). Correct the error and retry.")
    if number:
        intent["number"] = issue_number(number)
        save(run / STATE, intent)
    return confirm_post(run, repo, intent, receipt)


def publish(args):
    run = args.run_dir.resolve()
    repo = slug(read(run / "basic_information.json")["preparation_config"]["torch_npu_repo"])
    with issue_lock(run, "issue-publish:" + repo.casefold()):
        lines = (run / "issue-title.txt").read_text(encoding="utf-8-sig").splitlines()
        require(len(lines) == 1 and lines[0].strip(), "Expected one nonempty title line.")
        title = lines[0].strip()
        body = (run / "issue-body.md").read_text(encoding="utf-8-sig")
        require(body.strip(), "Empty issue-body.md.")
        draft = content_key(title, body)
        receipt = stored(run / "issue.json")
        state = stored(run / STATE)
        previous = stored(run / SEARCH)
        if not same_repo(previous, repo):
            previous = {}
        elif previous.get("draft") != draft:
            # Refresh the search the agent acted on and present it again before
            # publishing the edited draft. A new search starts with its own terms.
            previous = {} if args.command == "search" else dict(previous, incomplete=True)
        if args.command == "search":
            return lookup(run, repo, draft, previous, args.keyword, args.issue_url,
                          receipt, state, refresh=False)[2]

        if state.get("phase") == "creating":
            require(same_repo(state, repo), "The previous create is unresolved in a different repository.")
            # Never send a second POST while the first one is unconfirmed.
            if args.command == "create" or state["draft"] != draft:
                outcome = confirm_post(run, repo, state, receipt)
                if outcome["status"] == "pending" or state["draft"] == draft:
                    return outcome
                receipt, state = outcome, read(run / STATE)
            elif state.get("number") and args.number != issue_number(state["number"]):
                return pending(repo, "Reconcile the issue number returned by the previous POST first.", state["number"])
        elif (args.command == "create" and same_repo(state, repo) and state.get("phase") == "confirmed"
              and state.get("operation") == "create" and draft == state.get("draft")):
            # Replay a previously completed operation, never a new deduplication decision.
            return confirm_post(run, repo, state, receipt)

        issues, snapshot, results = lookup(run, repo, draft, previous, [], None, receipt, state, refresh=True)
        if not previous or previous.get("incomplete") or previous["versions"] != snapshot["versions"]:
            return dict(results, message="Compare these current results with the draft before choosing an operation.")
        metadata = {"repository": repo, "draft": draft, "operation": args.command}
        if args.command == "reuse":
            require(args.number in issues, "First look up the selected issue using search --issue-url or --keyword.")
            chosen = issues[args.number]
            if args.update_body and content_key("", chosen["body"]) != content_key("", body):
                chosen = apply_edit(run, repo, chosen, body)
            # If a create returned a number, ownership of this POST is known.
            status = ("created" if state.get("phase") == "creating" and state.get("number") == args.number
                      else "reused")
            if (state.get("draft") == draft and same_repo(state, repo)
                    and (state.get("phase") == "creating"
                         or (state.get("operation") == "create" and state.get("number") == args.number))):
                metadata["operation"] = "create"  # A replay of this POST must remain idempotent.
            return finish(run, repo, chosen, metadata, status, receipt)

        require(snapshot["terms"], "Search the main repository with at least one agent-chosen --keyword before creating.")
        for path in run.parent.glob("*/" + STATE):
            if path.parent != run:
                other = read(path)
                if (same_repo(other, repo) and other.get("phase") == "creating" and other.get("draft") == draft):
                    return pending(repo, f"Run '{path.parent.name}' already sent this exact payload. Reconcile its POST first.")
        return submit(run, repo, title, body, metadata, receipt)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--run-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", parents=[common], help="Look up issues without publishing.")
    search.add_argument("--keyword", action="append", default=[], help="Search term chosen by the caller; repeat as needed.")
    search.add_argument("--issue-url", help="Issue URL supplied in the handoff.")
    reuse = commands.add_parser("reuse", parents=[common], help="Reuse the specific issue selected by the caller.")
    reuse.add_argument("--number", type=int, required=True)
    reuse.add_argument("--update-body", action="store_true", help="Explicitly authorized edit of the chosen issue.")
    commands.add_parser("create", parents=[common], help="Publish the draft after the caller decides to create.")
    args = parser.parse_args(argv)
    try:
        if args.command == "reuse":
            issue_number(args.number)
        result = publish(args)
    except (OSError, ValueError, KeyError, TypeError, PublishError, subprocess.TimeoutExpired) as error:
        message = "GitCode timed out. Retry the same run." if isinstance(error, subprocess.TimeoutExpired) else str(error)
        print(json.dumps({"status": "error", "message": message}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
