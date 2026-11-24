"""Render an API PR verification row from current and reusable GPU or NPU evidence."""
import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tarfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile

from npu_unittest_evidence import load_npu
from pr_body_format import device_key


def cell(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a nonempty text field in the GPU receipt")
    return " ".join(value.splitlines()).strip().replace("\\", "\\\\").replace("|", "\\|")


def unittest_gpu_evidence(path, data, source=None, extra_files=()):
    """Bind the original receipt and its JUnit cases to the files that actually ran."""
    xml = path.with_name("junit.xml")
    selected = set(data["selected_files"])
    outcomes = case_results(xml, data["junit"]) if data.get("junit_generated") else {}
    verified = {f for f in selected if data.get("test_started") and
                any(k.startswith(f.removesuffix(".py").replace("/", ".") + ".") for k in outcomes)}
    hashes = dict(data.get("file_hashes", {}))
    helpers = set()
    proof = path.with_name("source-files.json")
    commit_file = path.with_name("commit.txt")
    head = commit_file.read_text().strip() if commit_file.is_file() else ""
    if proof.exists():
        binding = json.loads(proof.read_bytes())
        if (not head or binding["head_sha"] != head or
                binding["receipt_sha256"] != hashlib.sha256(path.read_bytes()).hexdigest() or
                binding["junit_sha256"] != (hashlib.sha256(xml.read_bytes()).hexdigest() if xml.exists() else None)):
            raise ValueError("Unittest receipt, JUnit and source proof do not match")
        hashes = binding["file_hashes"]
        helpers = set(hashes) - selected
        verified.intersection_update(binding["verified_files"])
        data = dict(data, run_id=binding["run_id"], independent_files=binding.get("independent_files", []))
    elif source and re.fullmatch(r"[0-9a-fA-F]{40}", head):
        for file in (selected | set(extra_files)) - hashes.keys():
            blob = subprocess.run(["git", "-C", str(source), "cat-file", "blob", f"{head}:{file}"], capture_output=True)
            if blob.returncode == 0:
                hashes[file] = hashlib.sha256(blob.stdout).hexdigest()
    return dict(data, file_hashes=hashes, verified_files=verified, helper_files=helpers, head_sha=head), xml


def load_evidence(path, source=None):
    """Normalize the unittest receipt or the archived CUDA report from the pytest action."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("device_kind") == "NPU":
        return load_npu(data, path.parent), path.with_name("junit.xml")
    if "device_kind" in data:
        if data["device_kind"] != "GPU":
            raise ValueError("Expected a GPU receipt")
        return unittest_gpu_evidence(path, data, source)
    if data.get("device") != "cuda":
        raise ValueError("Expected execution-result.json or a legacy CUDA evidence/report.json")
    folder = path.parent
    if not (folder / "junit.xml").exists():
        folder /= f"artifacts-{data['run_attempt']}"
    xml = folder / "junit.xml"
    counts = None
    if xml.exists():
        root = ET.parse(xml).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        if not suites:
            raise ValueError(f"No JUnit suites in {xml}")
        counts = {k: sum(int(s.attrib[k]) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    audit = {}
    if (folder / "step1-audit.json").is_file():
        audit = json.loads((folder / "step1-audit.json").read_bytes())
    elif (folder / "test-output.tar.gz").is_file():
        with tarfile.open(folder / "test-output.tar.gz") as archive:
            member = next((m for m in archive if m.isfile() and m.name.removeprefix("./") == "step1-audit.json"), None)
            if member:
                audit = json.load(archive.extractfile(member))
    if audit and audit.get("manifest_sha256") != data.get("manifest_sha256"):
        raise ValueError("Legacy audit and report have different manifests")
    env = data["environment"]
    hashes = {f["file"]: f["sha256"] for item in data["results"] for f in item["files"]}
    verified = {f["file"] for item in data["results"] for f in item["files"]
                if f["execution"] in {"pass", "fail", "skip", "partial"}}
    independent = {f["file"] for item in data["results"] if item.get("device_independent") for f in item["files"]}
    return dict(device_kind="GPU", device_model=env["gpu_name"], torch_version=env["torch"],
                cuda_version=env.get("cuda_build"), torch_git_version=env.get("torch_git_version"),
                selected_files=sorted(hashes), file_hashes=hashes, verified_files=verified, independent_files=independent,
                junit=counts, junit_generated=xml.exists(), scope="本次运行",
                test_exit_code=audit.get("exitstatus"), test_exit_code_display=str(audit.get("exitstatus", "未记录")),
                execution_status="仅本次运行，PR 全量范围待验证"), xml


def case_results(xml, counts):
    if counts is None:
        return {}
    cases = {}
    observed = dict.fromkeys(("tests", "failures", "errors", "skipped"), 0)
    for case in ET.parse(xml).iter("testcase"):
        name = case.attrib["classname"] + "." + case.attrib["name"]
        if name in cases:
            raise ValueError(f"Ambiguous duplicate JUnit ID: {name}")
        outcome = next((k for tag, k in (("error", "errors"), ("failure", "failures"), ("skipped", "skipped"))
                        if case.find(tag) is not None), "passed")
        cases[name] = outcome
        observed["tests"] += 1
        if outcome != "passed":
            observed[outcome] += 1
    if observed != counts:
        raise ValueError(f"JUnit cases and reported counts disagree: {xml}")
    return cases


def aggregate(plan, current, reuse, source, commit):
    spec = json.loads(plan.read_bytes())
    ids = spec["required_test_ids"]
    receipt, _ = current
    if (spec.get("device") != receipt["device_kind"] or not isinstance(ids, list) or not ids
            or not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids)):
        raise ValueError("Expected a matching device plan with unique required_test_ids for the current PR")
    current_files = set(receipt["selected_files"])
    outcomes = {}
    used_files = set()
    notes = []
    sources = [(current, current_files)]
    for path, *files in reuse:
        if not files:
            raise ValueError("--reuse requires a receipt and the verified unchanged file paths")
        previous = load_evidence(Path(path), source)
        sources.append((previous, set(files)))
    for (report, xml), files in sources:
        if report["device_kind"] != receipt["device_kind"]:
            raise ValueError("Cannot reuse evidence from a different device kind")
        if receipt["device_kind"] == "NPU" and any(
            report.get(k) != receipt.get(k) for k in
            ("device_model", "torch_version", "torch_npu_version", "torch_git_version", "torch_npu_git_version", "cann_version")
        ):
            raise ValueError("Incompatible NPU model or runtime build")
        if any(report.get(k) and receipt.get(k) and report[k] != receipt[k]
               for k in ("device_model", "torch_version", "cuda_version", "torch_git_version")):
            raise ValueError("Incompatible GPU model or PyTorch version in reused evidence")
        if used_files & files or not files <= set(report["selected_files"]):
            raise ValueError("Overlapping or unreported files in reused evidence")
        used_files.update(files)
        eligible = files.intersection(report.get("verified_files", files))
        if report.get("file_hashes") or (report["device_kind"] == "GPU" and "test_started" in report):
            if not source or not commit or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
                raise ValueError("Matching archived files requires --source and the full --commit SHA")
            checked = set()
            helpers = report.get("helper_files", set())
            for file in eligible | helpers:
                blob = subprocess.run(["git", "-C", str(source), "cat-file", "blob", f"{commit}:{file}"],
                                      capture_output=True, check=False)
                if blob.returncode == 0 and hashlib.sha256(blob.stdout).hexdigest() == report["file_hashes"].get(file):
                    checked.add(file)
            eligible = eligible & checked if helpers <= checked else set()
        prefixes = tuple(f.removesuffix(".py").replace("/", ".") + "." for f in eligible)
        cases = report["log_cases"] if "log_cases" in report else case_results(xml, report["junit"])
        subset = {i: cases[i] for i in ids if i in cases and i.startswith(prefixes)}
        outcomes.update(subset)
        for file, problem in report.get("file_problems", {}).items():
            if file in eligible:
                notes.append(f"{Path(file).name}：{problem}")
        if not subset and report is not receipt:
            continue
        if report is receipt and any(v in {"errors", "failures"} for k, v in cases.items() if k not in ids):
            notes.append("本轮另有计划外失败或错误")
        unexpected_npu_exit = (report is receipt and "file_problems" not in report and report.get("test_exit_code") not in (0, None)
                               and not any(v in {"errors", "failures"} for v in cases.values()))
        if (receipt["device_kind"] == "GPU" and report.get("test_exit_code") != 0) or unexpected_npu_exit:
            notes.append("测试退出码 " + report["test_exit_code_display"])
        if any(k in report["execution_status"] for k in ("回退", "契约", "平台限制", "门控", "fallback")):
            notes.append(("本轮" if report is receipt else "沿用报告") + "：" + report["execution_status"])
        if files.intersection(report.get("independent_files", set())):
            notes.append("含设备无关用例")
    total = len(ids)
    counts = Counter(outcomes.values())
    passed = counts["passed"]
    status = f"{passed}/{total} 通过"
    if passed == total and not notes:
        return "完整，" + status
    details = []
    for key, label in (("failures", "失败"), ("errors", "错误"), ("skipped", "跳过")):
        n = counts[key]
        if n:
            details.append(f"{n} {label}")
    if total > len(outcomes):
        details.append(f"{total - len(outcomes)} 待验证")
    return "，".join([status, *details, *dict.fromkeys(notes)])


def device_row(report):
    kind = report["device_kind"]
    if kind not in ("GPU", "NPU"):
        raise ValueError("Expected GPU or NPU evidence")
    counts = report["junit"]
    if counts is None:
        junit = "报告无效" if report["junit_generated"] else "未生成"
    else:
        keys = ("tests", "failures", "errors", "skipped")
        if not report["junit_generated"] or any(type(counts[k]) is not int or counts[k] < 0 for k in keys):
            raise ValueError("Invalid JUnit statistics in GPU receipt")
        junit = ", ".join(f"{key}={counts[key]}" for key in keys)
    model = cell(report["device_model"])
    version = "PyTorch " + cell(report["torch_version"])
    if kind == "NPU":
        version += "，torch-npu " + cell(report["torch_npu_version"])
    fields = [f"{kind} / {model}", version,
              f"{junit}（{cell(report['scope'])}）", cell(report["test_exit_code_display"]),
              cell(report["execution_status"])]
    return "| " + " | ".join(fields) + " |"


gpu_row = device_row  # Compatibility with existing GPU callers.


def fill_draft(body, row, *, scope_checked=True):
    """Replace the matching GPU row or insert a newly tested model into the five-column table."""
    lines = body.splitlines(keepends=True)
    headings = [i for i, line in enumerate(lines) if re.fullmatch(r"#{1,6}\s+【功能验证】", line.strip())]
    if len(headings) != 1:
        raise ValueError("Expected one 功能验证 section in the draft")
    start = headings[0] + 1
    end = next((i for i in range(start, len(lines)) if re.match(r"^#{1,6}\s", lines[i])), len(lines))
    header = ["执行端及设备", "实测包版本", "JUnit 统计", "测试退出码", "执行状态"]
    tables = [i for i in range(start, end) if [c.strip() for c in lines[i].strip().strip("|").split("|")] == header]
    if len(tables) != 1:
        raise ValueError("Expected one fixed-version five-column verification table")
    first = tables[0] + 2
    if first > end or not re.fullmatch(r"\|(?:\s*:?-{3,}:?\s*\|){5}", lines[first - 1].strip()):
        raise ValueError("Invalid verification table separator")
    stop = next((i for i in range(first, end) if not lines[i].lstrip().startswith("|")), end)

    def device(line):
        return re.split(r"(?<!\\)\|", line.strip(), maxsplit=2)[1].strip()

    kind, model = device_key(device(row))
    devices = [i for i in range(first, stop) if device_key(device(lines[i]))[0] == kind]
    matches = [i for i in devices if device_key(device(lines[i]))[1] == model]
    slots = matches or [i for i in devices if "{{实际设备型号}}" in device(lines[i])]
    if len(slots) > 1 or (not slots and model == "未记录" and devices):
        raise ValueError("Cannot uniquely identify the device model row")
    newline = "\r\n" if lines[tables[0]].endswith("\r\n") else "\n"
    if slots:
        i = slots[0]
        if matches and lines[i].strip() != row and not scope_checked:
            raise ValueError("Updating an existing device row with a partial run requires --plan; use --reuse for verified unchanged files")
        lines[i] = row + (newline if lines[i].endswith("\n") else "")
    else:
        if not lines[stop - 1].endswith("\n"):
            lines[stop - 1] += newline
        lines.insert(stop, row + newline)
    return "".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path, help="unittest execution-result.json or legacy CUDA report.json")
    parser.add_argument("--plan", type=Path, help="current PR device test-plan.json")
    parser.add_argument("--source", type=Path, help="reviewed PR source repository for test file hashes")
    parser.add_argument("--commit", help="full reviewed PR SHA for test file hashes")
    parser.add_argument("--reuse", nargs="+", action="append", default=[], metavar="ARG",
                        help="previous receipt followed by verified unchanged file paths (repeatable)")
    parser.add_argument("--draft", type=Path, help="fill the matching device row in an existing pr-body.md")
    args = parser.parse_args()
    try:
        report, xml = load_evidence(args.receipt, args.source)
        if args.reuse and not args.plan:
            raise ValueError("--reuse requires --plan")
        if args.plan:
            report["execution_status"] = aggregate(args.plan, (report, xml), args.reuse, args.source, args.commit)
        if report["device_kind"] == "NPU" and "log_cases" in report and not args.plan:
            raise ValueError("Archived NPU logs require --plan, --source and --commit")
        row = device_row(report)
        if args.draft:
            if args.draft.resolve() == args.receipt.resolve():
                raise ValueError("The draft must be distinct from the receipt")
            original = args.draft.read_bytes()
            scope_checked = bool(args.plan or (report.get("pr_required_total") and
                                              report.get("pr_missing_test_ids") == []))
            updated = fill_draft(original.decode("utf-8"), row, scope_checked=scope_checked).encode("utf-8")
            if updated != original:
                tmp = NamedTemporaryFile(dir=args.draft.parent, delete=False)
                path = Path(tmp.name)
                try:
                    with tmp:
                        tmp.write(updated)
                    path.chmod(stat.S_IMODE(args.draft.stat().st_mode))
                    if args.draft.read_bytes() != original:
                        raise ValueError("Draft changed during rendering; retry with the current draft")
                    os.replace(path, args.draft)
                finally:
                    path.unlink(missing_ok=True)
        print(row)
    except (OSError, ValueError, TypeError, KeyError, IndexError, ET.ParseError, tarfile.TarError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    main()
