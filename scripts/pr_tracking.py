"""Top-level index of AI_LOOP PR receipts. Writers share the tracking lock."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile


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
def file_lock(path, *, wait=False):
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def receipt_url(record):
    expected = f"https://gitcode.com/{record['repository']}/merge_requests/{int(record['number'])}"
    url = record.get("url", expected)
    if url.lower() != expected.lower():
        raise ValueError(f"PR receipt URL differs from repository and number: {url}")
    return url


def load_index(data):
    path = data / "pr-tracking.json"
    if path.exists():
        entries = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError(f"Expected a PR list in {path}")
    else:
        entries = []
        for receipt in sorted((data / "run_data").glob("*/pr.json")):
            record = json.loads(receipt.read_text(encoding="utf-8-sig"))
            entries.append({"pr_url": receipt_url(record),
                            "run_data": receipt.parent.relative_to(data.parent).as_posix(),
                            "pr_status": "merged" if record.get("cleanup-completed") is True else "unknown"})
    index = {}
    for entry in entries:
        key = entry["pr_url"].lower()
        run = data.parent / entry["run_data"]
        if (entry["run_data"] != f"data/run_data/{run.name}" or run.name in ("", ".", "..") or key in index):
            raise ValueError(f"Invalid or duplicate PR tracking entry: {entry}")
        if entry["pr_status"] != "merged" and run.resolve().parent != (data / "run_data").resolve():
            raise ValueError(f"Invalid PR run directory: {run}")
        index[key] = entry
    if not path.exists():
        save(path, entries)
    return index


def register_receipt(run, receipt):
    # Standalone --source handoffs outside AI_LOOP keep their local receipt.
    if run.parent.name != "run_data" or run.parent.parent.name != "data":
        save(run / "pr.json", receipt)
        return
    data = run.parent.parent
    url = receipt_url(receipt)
    entry = {"pr_url": url, "run_data": run.relative_to(data.parent).as_posix(), "pr_status": "open"}
    with file_lock(data / ".pr-tracking.lock", wait=True):
        index = load_index(data)
        previous = index.get(url.lower())
        if previous and previous["run_data"] != entry["run_data"]:
            raise ValueError(f"PR already tracked in {previous['run_data']}")
        if previous and previous["pr_status"] in ("merged_pending", "merged"):
            raise ValueError(f"PR already merged: {url}")
        record_path = run / "pr.json"
        saved = json.loads(record_path.read_text(encoding="utf-8-sig")) if record_path.exists() else {}
        if saved.get("cleanup-completed") is True:
            raise ValueError(f"PR already merged: {url}")
        # Write the pointer first so an interrupted publication remains discoverable.
        if previous != entry:
            index[url.lower()] = entry
            save(data / "pr-tracking.json", list(index.values()))
        save(record_path, receipt)
