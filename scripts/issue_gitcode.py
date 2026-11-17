"""Issue operations supported by GitCode CLI v0.16.0; publication decisions live in publish_issue."""

import json
import re
import subprocess

from publish_pr import PublishError, invoke, rejected_create, require


class IssueNotFound(PublishError):
    """The requested issue was not found in the repository."""


def _number(value):
    require(re.fullmatch(r"[1-9][0-9]*", str(value)), "Expected a positive GitCode issue number.")
    return int(value)


def _check_url(repo, number, url):
    expected = f"https://gitcode.com/{repo}/issues/{number}"
    require(isinstance(url, str) and url.rstrip("/").casefold() == expected.casefold(),
            f"GitCode returned an issue URL outside {repo}#{number}.")


def search(repo, term):
    """Read every result of a title/body search across open and closed issues."""
    require(isinstance(term, str) and term.strip(), "Issue search needs a nonempty term.")
    numbers = set()
    page = 1
    while True:
        # issue list has --page/--per-page, unlike pr list's --paginate.
        proc = invoke("gitcode", "issue", "list", "-R", repo, "--state", "all",
                      "--search", term, "--page", str(page), "--per-page", "100", "--json")
        batch = json.loads(proc.stdout)
        if batch is None or batch == []:  # Go may encode an empty slice as null.
            return numbers
        require(isinstance(batch, list), "Unexpected response to gitcode issue list.")
        for issue in batch:
            require(isinstance(issue, dict), "Malformed entry in gitcode issue list.")
            number = _number(issue.get("number"))
            if issue.get("html_url"):
                _check_url(repo, number, issue["html_url"])
            require(number not in numbers, "Issue search pages overlap; retry the search.")
            numbers.add(number)
        page += 1


def view(repo, number):
    """Return the remote issue, preserving its fields and normalizing its number."""
    number = _number(number)
    proc = invoke("gitcode", "issue", "view", str(number), "-R", repo, "--json", check=False)
    if proc.returncode:
        message = f"issue #{number} not found in {repo}"
        if proc.returncode == 3 and re.search(re.escape(message) + r"(?:\s|:|$)", proc.stderr, re.I):
            raise IssueNotFound(message)
        raise PublishError(f"Could not read {repo}#{number} (gitcode exit {proc.returncode}).")
    issue = json.loads(proc.stdout)
    require(isinstance(issue, dict) and _number(issue.get("number")) == number
            and isinstance(issue.get("title"), str) and isinstance(issue.get("body"), str),
            f"Incomplete remote issue response for {repo}#{number}.")
    _check_url(repo, number, issue.get("html_url"))
    return dict(issue, number=number)


def create(repo, title, body_path):
    """Return (number, HTTP rejection). A lost/ambiguous response must be reconciled."""
    try:
        proc = invoke("gitcode", "issue", "create", "-R", repo, "--title", title,
                      "--body-file", str(body_path), "--json", check=False)
    except subprocess.TimeoutExpired:
        return None, None
    try:
        response = json.loads(proc.stdout)
    except ValueError:
        response = None
    if isinstance(response, dict) and response.get("number") is not None:
        number = _number(response["number"])
        if response.get("html_url"):
            _check_url(repo, number, response["html_url"])
        return number, None
    return None, rejected_create(proc, response)


def edit(repo, number, body_path):
    """Replace only the body of an explicitly selected existing issue."""
    invoke("gitcode", "issue", "edit", str(_number(number)), "-R", repo,
           "--body-file", str(body_path), "--json")
