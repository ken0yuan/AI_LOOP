"""Check the structured parts of an API PR against its current Markdown template."""
import re
import sqlite3
from pathlib import Path

HEADING = re.compile(r"^# 【([^】]+)】\s*$", re.M)
DEVICE = re.compile(r"^(GPU|NPU)\b\s*[/·]?\s*(.+)$", re.I)


def device_key(label):
    match = DEVICE.fullmatch(label.strip())
    if not match or not match[2].strip(" /·"):
        raise ValueError(f"设备行缺少执行端或型号：{label}")
    model = re.sub(r"\s*[（(](?:WSL|CUDA|CANN|驱动).*?[)）]$", "", match[2], flags=re.I)
    model = re.sub(r"^NVIDIA\s+(?:GeForce\s+)?", "", model, flags=re.I)
    return match[1].upper(), re.sub(r"[\s/·]+", "", model).casefold()


def cells(line):
    if not line.startswith("|") or not line.endswith("|"):
        raise ValueError("Markdown 表格行必须以 | 开始和结束")
    return [v.strip() for v in re.split(r"(?<!\\)\|", line[1:-1])]


def table_parts(section, header, optional_reason=False):
    lines = section.strip().splitlines()
    indexes = [i for i, s in enumerate(lines) if s.lstrip().startswith("|")]
    if not indexes or indexes != list(range(indexes[0], indexes[-1]+1)) or len(indexes) < 3:
        raise ValueError(f"必须有一张连续且已填写的表：{header}")
    rows = [cells(lines[i].strip()) for i in indexes]
    if rows[0] != header or len(rows[1]) != len(header) or not all(re.fullmatch(r":?-{3,}:?", c) for c in rows[1]):
        raise ValueError(f"表头或分隔行不符合模板：{header}")
    for row in rows[2:]:
        if len(row) != len(header) or not all(row[:-1] if optional_reason else row):
            raise ValueError(f"表格列数或必填项不正确：{row}")
    return lines[:indexes[0]], rows[2:], lines[indexes[-1]+1:]


def validate_api_body(body, template, changed_paths, upstream_template=None, classification_db=None):
    if re.search(r"<!--|\{\{[^{}\n]*\}\}", body):
        raise ValueError("正文仍含模板提示或占位符")
    parts = HEADING.split(body)
    layout = upstream_template or template
    if parts[0].strip() or parts[1::2] != HEADING.findall(layout) or re.search(r"^#{1,6}\s", ''.join(parts[2::2]), re.M):
        raise ValueError("正文章节标题或顺序不符合模板")
    sections = dict(zip(parts[1::2], parts[2::2]))
    if not {"合入来源", "CheckList", "修改方案", "功能验证"} <= sections.keys():
        raise ValueError("目标模板缺少 API 正文必需的栏目")
    if not all(v.strip() for v in sections.values()):
        raise ValueError("正文章节为空")
    for name in ("合入来源", "CheckList"):
        pattern = r"^- \[[ xX]\] (.+?)\s*$"
        source = dict(zip(HEADING.split(layout)[1::2], HEADING.split(layout)[2::2]))[name]
        if re.findall(pattern, sections[name], re.M) != re.findall(pattern, source, re.M):
            raise ValueError(f"【{name}】勾选项不符合模板")
    plan = sections["修改方案"].strip()
    headers = [cells(line) for line in template.splitlines() if line.startswith("| ") and "{{" not in line]
    if len(headers) == 2:
        before, rows, _ = table_parts(plan, headers[0], optional_reason=True)
        if len({r[0].strip('\x60 ') for r in rows}) != len(rows):
            raise ValueError("分类表中 API 正名重复")
        if classification_db is not None:
            with sqlite3.connect(classification_db.as_uri() + "?mode=ro", uri=True) as db:
                for row in rows:
                    api = row[0].strip("` ")
                    data = db.execute("SELECT 分类,细分模块,细分场景,API适配分类,分类待审核 FROM API支持数据 "
                                      "WHERE API名称=?", (api,)).fetchone()
                    if not data or list(data[:4]) != [v.strip("` ") for v in row[1:5]]:
                        raise ValueError(f"分类值与正名数据库记录不一致：{api}")
                    if (data[4] == "是" and not row[-1]) or (data[4] == "否" and row[-1]):
                        raise ValueError(f"分类原因应在待审核时填写，已审核时留空：{api}")
        plan = "\n".join(before)
    seen = set()
    groups = re.split(r"^\*\*\x60([^\x60\n]+)\x60\*\*[ \t]*$", plan, flags=re.M)
    if groups[0].strip() or len(groups) == 1:
        raise ValueError("【修改方案】按文件路径分段，各项使用 Markdown 列表")
    for index, (path, items) in enumerate(zip(groups[1::2], groups[2::2])):
        if index + 1 < len(groups[1::2]) and not items.endswith("\n\n"):
            raise ValueError("相邻文件段落之间需空一行")
        lines = [s for s in items.splitlines() if s.strip()]
        if path in seen or path not in changed_paths or not lines or any(not re.fullmatch(r"- \S.*", s) for s in lines):
            raise ValueError(f"修改方案含重复路径、非本 PR 文件或无效列表：{path}")
        seen.add(path)
    before, rows, after = table_parts(sections["功能验证"], headers[-1])
    devices = [device_key(row[0]) for row in rows]
    if any(s.strip() for s in before) or {k for k, _ in devices} != {"GPU", "NPU"} or len(set(devices)) != len(rows):
        raise ValueError("功能验证须先列表，包含 NPU 和 GPU，每种型号只写一行")
    for row, (kind, _) in zip(rows, devices):
        if any(key in row[-1] for key in ("待测", "待验证", "未执行", "受阻")) and row[1] in ("—", "未记录", "待测"):
            continue
        if "pytorch" not in row[1].lower() or (kind == "NPU" and "torch-npu" not in row[1].lower().replace("_", "-")):
            raise ValueError(f"{row[0]} 缺少实测包版本")
    notes = {}
    for line in after:
        if not line.strip():
            continue
        entry = re.fullmatch(r"(?:- )?(.+?)[：:]\s*(.+)", line.strip())
        if not entry or len(entry[2].strip()) > 100:
            raise ValueError("表下每个设备用一行说明，格式为「执行端及设备：说明」，说明最多 100 字")
        key = device_key(entry[1])
        if key in notes:
            raise ValueError("同一设备只能保留一条当前说明")
        notes[key] = entry[2]
    if set(notes) != set(devices):
        raise ValueError("功能验证的每行设备都须有一条对应的当前说明")


def api_template(run, adapt, basic, verification):
    agents = [item.get("agentname") for item in (adapt.get("adapt-plan") or {}).values() if isinstance(item, dict)]
    if "api-adaptation" not in agents and (agents or verification != (run / "api-loop/result.json").resolve()):
        return None
    venv = Path(basic.get("venv", ""))
    if venv.parent.name.startswith("torch-"):
        name = "api-adaptation-fixed-pr-template.md"
    elif venv.parent.name == "venv":
        name = "api-adaptation-pr-template.md"
    else:
        raise ValueError("无法根据批次 venv 选择 API PR 模板")
    return (Path(__file__).resolve().parents[1] / "templates" / name).read_text(encoding="utf-8")
