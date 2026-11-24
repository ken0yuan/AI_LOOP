"""Read verbose per-file unittest logs without manufacturing JUnit results."""
import re
from collections import Counter

CASE = re.compile(r"^(test\w*) \(([\w.]+)\)(?:\s|$)")
OUTCOME = re.compile(r"(?:^|\.\.\. )(ok|FAIL|ERROR|skipped(?: .*)?|expected failure|unexpected success)$")


def log_cases(text, filename):
    prefix = filename.removesuffix(".py").replace("/", ".")
    cases = {}
    pending = None
    for line in text.splitlines():
        match = CASE.match(line)
        if match:
            method, identity = match.groups()
            parts = identity.split(".")
            if pending or len(parts) < 2 or method != parts[-1]:
                return {}
            pending = ".".join([prefix, *parts[-2:]])
        status = OUTCOME.search(line.strip())
        if pending and status:
            label = status[1]
            if pending in cases:
                raise ValueError(f"Duplicate unittest case in {filename}: {pending}")
            cases[pending] = ("passed" if label == "ok" else "errors" if label == "ERROR"
                              else "skipped" if label.startswith("skipped") or label == "expected failure"
                              else "failures")
            pending = None
    totals = re.findall(r"^Ran (\d+) tests? in ", text, re.M)
    if pending or totals != [str(len(cases))] or not re.search(r"^(OK|FAILED)(?: \([^\n]*\))?\s*$", text, re.M):
        return {}
    return cases


def run_problem(text, cases, exit_code):
    """Surface errors outside individual test methods, including class/module fixtures."""
    summary = re.findall(r"^FAILED(?: \(([^\n]*)\))?\s*$", text, re.M)
    if summary:
        counters = dict(re.findall(r"(failures|errors|unexpected successes)=(\d+)", summary[-1]))
        extra_errors = int(counters.get("errors", 0)) - sum(v == "errors" for v in cases.values())
        if extra_errors > 0:
            fixtures = re.findall(r"^ERROR: (setUpClass|tearDownClass|setUpModule|tearDownModule) \(", text, re.M)
            detail = "（" + "、".join(dict.fromkeys(fixtures)) + "）" if fixtures else ""
            return f"{extra_errors} 个用例外错误{detail}"
    if (summary or exit_code not in (None, 0)) and not any(v in ("errors", "failures") for v in cases.values()):
        return f"测试进程异常退出（{exit_code}）" if exit_code not in (None, 0) else "unittest 最终汇总失败"
    return None


def load_npu(data, folder):
    for key in ("device_model", "torch_version", "torch_npu_version"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Missing NPU receipt field {key}")
    if data.get("junit_generated") or "files" not in data:
        return data  # Unified unittest receipt with JUnit.
    files = data["files"]
    if not isinstance(files, list) or not files:
        raise ValueError("Expected per-file NPU unittest evidence")
    cases, hashes, exits, problems = {}, {}, [], {}
    for item in files:
        filename = item["file"]
        if filename in hashes or not re.fullmatch(r"[a-fA-F0-9]{64}", item["sha256"]):
            raise ValueError(f"Duplicate file or invalid SHA256: {filename}")
        hashes[filename] = item["sha256"].lower()
        text = (folder / item["log"]).read_text(encoding="utf-8", errors="replace")
        outcomes = log_cases(text, filename)
        cases.update(outcomes)
        problem = run_problem(text, outcomes, item.get("exit"))
        if problem:
            problems[filename] = problem
        exits.append(item.get("exit"))
    codes = Counter(exits)
    display = "、".join(("未记录" if code is None else str(code)) +
                       (f"（{count} 个文件）" if len(codes) > 1 else "") for code, count in codes.items())
    return dict(data, selected_files=list(hashes), file_hashes=hashes, log_cases=cases, file_problems=problems,
                junit=None, junit_generated=False, test_exit_code=next(iter(codes)) if len(codes) == 1 else None,
                test_exit_code_display=display, scope=data.get("scope", "本次运行"),
                execution_status=data.get("execution_status", "待验证"))
