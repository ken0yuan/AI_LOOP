import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from npu_unittest_evidence import log_cases
from pr_body_format import validate_api_body
from render_gpu_result import aggregate, load_evidence, fill_draft

FIXED = (ROOT / "templates/api-adaptation-fixed-pr-template.md").read_text()
NIGHTLY = (ROOT / "templates/api-adaptation-pr-template.md").read_text()
PATH = "test/a.py"
NPU = "| NPU / 910B4 | PyTorch 2.15，torch-npu 2.15 | 未生成 | 0 | 完整，1/1 通过 |"
GPU = "| GPU / RTX 5070 Ti | PyTorch 2.15 | 未生成 | 0 | 完整，1/1 通过 |"


def example(template):
    import re
    body = re.sub(r"<!--.*?-->", "", template, flags=re.S)
    body = re.sub(r"^\{\{.*(?:差异|调整).*?\}\}$", "**\x60test/a.py\x60**\n\n- 传入空序列时返回空结果。", body, flags=re.M)
    body = re.sub(r"^\| NPU .*$", NPU, body, flags=re.M)
    body = re.sub(r"^\| GPU .*$", GPU + "\n\nNPU / 910B4：验证空输入。\nGPU / RTX 5070 Ti：验证空输入。", body, flags=re.M)
    body = re.sub(r"^\| \{\{API 正名.*$", "| torch.a | Core | 模块 | 场景 | 框架类API |  |", body, flags=re.M)
    return re.sub(r"\{\{.*?\}\}", "已填写", body)


class FormatTest(unittest.TestCase):
    def test_both_templates_and_changed_layout(self):
        for template in (FIXED, NIGHTLY):
            body = example(template)
            validate_api_body(body, template, {PATH})
            for bad in (body.replace(" | 测试退出码", ""), body.replace(GPU, GPU + "\n" + GPU),
                        body.replace("GPU / RTX 5070 Ti：验证空输入。", ""),
                        body.replace("验证空输入。", "字"*101), body.replace("test/a.py", "test/old.py"),
                        body.replace("# 【接口变更】", "# 【资料变更】"), body.replace("- [ ] 需求", "- [ ] 旧要求")):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    validate_api_body(bad, template, {PATH})
        with self.assertRaises(ValueError):
            validate_api_body(example(NIGHTLY), FIXED, {PATH})

    def test_render_updates_only_selected_model(self):
        body = example(FIXED).replace(NPU, NPU + "\n" + NPU.replace("910B4", "950"))
        row = NPU.replace("910B4", "950").replace("完整，1/1 通过", "0/1 通过，1 失败")
        updated = fill_draft(body, row)
        self.assertIn(NPU, updated)
        self.assertIn(GPU, updated)
        self.assertIn(row, updated)


class ArchivedNpuTest(unittest.TestCase):
    def test_unittest_log_qualifies_main_and_handles_warnings(self):
        log = "test_a (__main__.Suite.test_a)\ndoc ... warning from driver\nok\nRan 1 test in 0.1s\nOK\n"
        self.assertEqual(log_cases(log, PATH), {"test.a.Suite.test_a": "passed"})
        self.assertEqual(log_cases("Ran 1 test in 0.1s\nOK\n", PATH), {})

    def test_class_cleanup_error_survives_mixed_file_exits(self):
        import hashlib
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            files, ids = [], []
            for name in ("a", "b"):
                path = folder / (name + ".py")
                code = ("import unittest\nclass Suite(unittest.TestCase):\n"
                        "    def test_ok(self): pass\n")
                if name == "a":
                    code += "    @classmethod\n    def tearDownClass(cls): raise RuntimeError('cleanup')\n"
                path.write_text(code + "unittest.main(verbosity=2)\n")
                proc = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
                self.assertEqual(proc.returncode, int(name == "a"))
                log = folder / (name + ".log")
                log.write_text(proc.stderr)
                files.append(dict(file=path.name, log=log.name, exit=proc.returncode,
                                  sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
                ids.append(name + ".Suite.test_ok")
            receipt = folder / "receipt.json"
            receipt.write_text(json.dumps(dict(device_kind="NPU", device_model="950", torch_version="2.15",
                                               torch_npu_version="2.15", files=files)))
            plan = folder / "plan.json"
            plan.write_text(json.dumps(dict(device="NPU", required_test_ids=ids)))
            def blob(args, **kwargs):
                return mock.Mock(stdout=(folder / args[-1].split(":")[-1]).read_bytes(), returncode=0)
            with mock.patch("render_gpu_result.subprocess.run", side_effect=blob):
                report = load_evidence(receipt)
                status = aggregate(plan, report, [], folder, "a"*40)
            self.assertEqual(status, "2/2 通过，a.py：1 个用例外错误（tearDownClass）")
            self.assertEqual(report[0]["test_exit_code_display"], "1（1 个文件）、0（1 个文件）")

    def test_scope_and_incompatible_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            plan = folder / "plan.json"
            plan.write_text(json.dumps(dict(device="NPU", required_test_ids=["test.a.Suite.test_a","test.b.Suite.test_b"])))
            def receipt(name, model, status, digest):
                file = f"test/{name}.py"
                log = folder / (name + ".log")
                log.write_text(f"test_{name} (__main__.Suite.test_{name}) ... {status}\nRan 1 test in 0.1s\n" +
                               ("OK\n" if status == "ok" else "FAILED (failures=1)\n"))
                path = folder / (name + ".json")
                path.write_text(json.dumps(dict(device_kind="NPU", device_model=model, torch_version="2.15",
                                                torch_npu_version="2.15", files=[dict(file=file, log=log.name,
                                                exit=int(status != "ok"), sha256=digest)])))
                return path
            import hashlib
            digest = hashlib.sha256(b"source").hexdigest()
            cur = load_evidence(receipt("a", "950", "FAIL", digest))
            def result(old):
                return aggregate(plan, cur, [[str(old),"test/b.py"]], folder, "a"*40)
            with mock.patch("render_gpu_result.subprocess.run", return_value=mock.Mock(stdout=b"source", returncode=0)):
                self.assertEqual(result(receipt("b", "950", "ok", digest)), "1/2 通过，1 失败")
                self.assertIn("1 待验证", result(receipt("b", "950", "ok", "0"*64)))
                with self.assertRaises(ValueError):
                    result(receipt("b", "910B4", "ok", digest))
