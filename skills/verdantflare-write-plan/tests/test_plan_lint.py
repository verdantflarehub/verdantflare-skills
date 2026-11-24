"""Single-variable mutation unit test suite (>= 18 cases) for verdantflare-write-plan/scripts/plan_lint.py."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint  # noqa: E402

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def run_cli(*args: str) -> tuple[int, str, str]:
    out_buf, err_buf = io.StringIO(), io.StringIO()
    with mock.patch("sys.stdout", out_buf), mock.patch("sys.stderr", err_buf):
        try:
            rc = plan_lint.main(list(args))
        except SystemExit as exc:
            rc = int(exc.code) if isinstance(exc.code, int) else 2
    return rc, out_buf.getvalue(), err_buf.getvalue()


def build_valid_complex_workspace(root: Path, *, t3_author: str = "Gemini") -> dict[str, Path]:
    """Create a 100% compliant baseline complex R&D workspace with real PNG bytes, receipts, changes, reviews, and acceptance."""
    for rel in (
        "docs/plan",
        "docs/design",
        "docs/changes",
        "docs/reviews",
        "docs/acceptance",
        "docs/archive/plan",
        "docs/archive/changes",
        "docs/archive/reviews",
        "docs/archive/acceptance",
        ".gdrive/managed/pending/design",
        ".gdrive/managed/receipts/pending/design",
    ):
        (root / rel).mkdir(parents=True, exist_ok=True)

    img_sha = hashlib.sha256(TINY_PNG).hexdigest()
    img_name = f"arch-diagram--{img_sha}.png"
    img_path = root / ".gdrive/managed/pending/design" / img_name
    img_path.write_bytes(TINY_PNG)
    rec_path = root / ".gdrive/managed/receipts/pending/design" / f"{img_name}.json"
    rec_path.write_text(json.dumps({"sha256": img_sha, "bucket": "pending"}, indent=2), encoding="utf-8")

    plan_path = root / "docs/plan/2026.11.alpha-gov.plan.md"
    design_path = root / "docs/design/alpha-gov.md"
    accept_path = root / "docs/acceptance/accept_20261115_alpha-gov.md"
    t1_rev = root / "docs/reviews/review_20261115_alpha-gov_t1.md"
    t2_rev = root / "docs/reviews/review_20261115_alpha-gov_t2.md"
    t4_rev = root / "docs/reviews/review_20261115_alpha-gov_t4.md"

    chg_t1 = root / "docs/archive/changes/change_20261115_t1-spec.md"
    chg_t2 = root / "docs/archive/changes/change_20261115_t2-scan.md"
    chg_t3 = root / "docs/archive/changes/change_20261115_t3-impl.md"
    chg_t4 = root / "docs/archive/changes/change_20261115_t4-run.md"

    for p, agent_str in (
        (chg_t1, "Gemini（T1 方案编制）"),
        (chg_t2, "Gemini（T2 影响面扫描）"),
        (chg_t3, f"{t3_author}（T3 核心模块实现）"),
        (chg_t4, f"{t3_author}（T4 实跑取证）"),
    ):
        p.write_text(
            f"# 子任务变更\n\n负责人：Mengsk\n助手：{agent_str}\n变更状态：已完成\n更新时间：2026-11-15\n\n## 1. 当前依据\n\n- `session_id = sess-author-01`\n",
            encoding="utf-8",
            newline="\n",
        )

    design_path.write_text(
        "# Alpha 专题设计\n\n"
        "设计状态：草案\n更新时间：2026-11-15\n\n"
        "## 1. 当前依据\n\n"
        "- [所属计划](../plan/2026.11.alpha-gov.plan.md)：关联专项计划。\n\n"
        "## 2. 架构原理\n\n"
        "```text\n"
        "+-------------------+      +-------------------+\n"
        "|   Client Input    | ---> |   Gate Checker    |\n"
        "+-------------------+      +-------------------+\n"
        "```\n\n"
        f"![架构图](../../.gdrive/managed/pending/design/{img_name})\n",
        encoding="utf-8",
        newline="\n",
    )

    detail_reviewer = "Gemini" if t3_author == "Codex" else "Codex"
    code_sha = "a" * 64
    inst_sha = "b" * 64
    accept_path.write_text(
        "# Alpha 专项验收文档\n\n"
        "负责人：Mengsk\n"
        f"执行助手：{t3_author}（T4 实跑取证）\n"
        f"验真助手：{detail_reviewer}（脚本反作弊验真）、Claude（轻量架构符合性审计）\n"
        "关联计划：[所属计划](../plan/2026.11.alpha-gov.plan.md)\n"
        "验收状态：智能体已验真\n"
        "更新时间：2026-11-15\n\n"
        "## 1. 当前依据\n\n"
        "- [所属计划](../plan/2026.11.alpha-gov.plan.md)\n\n"
        "## 2. 锁定验收场景与判据\n\n"
        "验收流程审定记录：\n\n"
        f"- **执行者起草**：{t3_author} 起草。\n"
        f"- **研发核验修订**：{detail_reviewer} 直接修订。\n"
        "- **Claude 修订**：Claude 修订定稿。\n"
        "- **分歧上报**：无。\n\n"
        "### 2.1 S1：核心门禁验证\n\n"
        "- **验收对象**：核心门禁模块。\n"
        "- **取证流程**：执行者运行标准测试套件。\n"
        "- **证据内容**：退出码与断言日志。\n"
        "- **充分性判据**：退出码 `0` 且断言全部通过。\n"
        "- **判定**：Codex 全面核验，Claude 抽查。\n\n"
        "## 3. 智能体实跑与演示证据\n\n"
        "- `S1` 实跑通过：退出码 `0`，18 项断言全部通过。\n\n"
        "## 4. 防伪锚点与双轨验真\n\n"
        "### 4.1 工作区基准与提交标识\n\n"
        "- 基准提交：`deadbeef12345678`\n\n"
        "### 4.2 运行宿主与环境标识\n\n"
        "- Python 3.12 UTF-8\n\n"
        "### 4.3 关键源码与测试脚本 SHA-256\n\n"
        f"- `plan_lint.py`：`{code_sha}`\n\n"
        "### 4.4 安装态制品 SHA-256 与前期评审收据\n\n"
        f"- 安装态 `plan_lint.py`：`{inst_sha}`\n\n"
        "### 4.5 T4 双轨验真原生会话收据\n\n"
        f"- {detail_reviewer}：`thread_id = th-rd-t4`, `task_id = tk-rd-t4`, `turn_id = tr-rd-t4`\n"
        "- Claude：`session_id = ss-c-t4`, `task_id = tk-c-t4`, `turn_id = tr-c-t4`\n\n"
        "## 5. CP1 双抽检核验单\n\n"
        "### 5.1 线上与宿主系统抽检速查入口\n\n"
        "- 抽检通过。\n\n"
        "### 5.2 验收文档抽检核签记录\n\n"
        "- 待核签。\n",
        encoding="utf-8",
        newline="\n",
    )

    lock_hash, _, _ = plan_lint.compute_lock_hash(accept_path)
    rev_hash_t2 = plan_lint.compute_review_hash("t2", accept_path, design_path)
    rev_hash_t4 = plan_lint.compute_review_hash("t4", accept_path)

    t1_rev.write_text(
        "# T1 评审报告\n\n"
        "负责人：Mengsk\n"
        "评审助手：Claude（总体架构核验）、Codex（工程细节评审）\n"
        "关联计划：[所属计划](../plan/2026.11.alpha-gov.plan.md)\n"
        "关联阶段：T1\n"
        f"评审对象哈希：{rev_hash_t2}\n"
        "评审结论：已通过\n"
        "更新时间：2026-11-15\n\n"
        "## 1. 当前依据\n\n- [所属计划](../plan/2026.11.alpha-gov.plan.md)\n\n"
        "## 2. 总体架构核验与符合性审计\n\n"
        "Claude 核验通过：`session_id = ss-c-t1`, `task_id = tk-c-t1`, `turn_id = tr-c-t1`。\n\n"
        "## 3. 工程细节与防漏评审\n\n"
        "Codex 细节评审通过：`thread_id = th-x-t1`, `task_id = tk-x-t1`, `turn_id = tr-x-t1`。\n\n"
        "## 4. 架构疑难升级裁定\n\n无架构级疑难升级。\n\n"
        "## 5. 问题闭环与放行结论\n\n未闭环 HIGH/MEDIUM：0\n\n- `[CLOSED]` H1：已闭环。\n",
        encoding="utf-8",
        newline="\n",
    )

    t2_rev.write_text(
        "# T2 评审报告\n\n"
        "负责人：Mengsk\n"
        "评审助手：Codex（全仓影响面防漏评审）\n"
        "关联计划：[所属计划](../plan/2026.11.alpha-gov.plan.md)\n"
        "关联阶段：T2\n"
        f"评审对象哈希：{rev_hash_t2}\n"
        "评审结论：已通过\n"
        "更新时间：2026-11-15\n\n"
        "## 1. 当前依据\n\n- [所属计划](../plan/2026.11.alpha-gov.plan.md)\n\n"
        "## 2. 总体架构核验与符合性审计\n\n"
        "本阶段未变更 T1 已核验总体架构，按低 Token 规范免调 Claude。\n\n"
        "## 3. 工程细节与防漏评审\n\n"
        "Codex 全仓防漏审通过：`thread_id = th-x-t2`, `task_id = tk-x-t2`, `turn_id = tr-x-t2`。\n\n"
        "## 4. 架构疑难升级裁定\n\n无架构级疑难升级。\n\n"
        "## 5. 问题闭环与放行结论\n\n未闭环 HIGH/MEDIUM：0\n\n- `[CLOSED]` M1：已闭环。\n",
        encoding="utf-8",
        newline="\n",
    )

    t4_rev.write_text(
        "# T4 验真与架构审计报告\n\n"
        "负责人：Mengsk\n"
        f"评审助手：Claude（架构符合性轻量审计）、{detail_reviewer}（脚本反作弊与哈希证据链验真）\n"
        "关联计划：[所属计划](../plan/2026.11.alpha-gov.plan.md)\n"
        "关联阶段：T4\n"
        f"评审对象哈希：{rev_hash_t4}\n"
        "评审结论：已通过\n"
        "更新时间：2026-11-15\n\n"
        "## 1. 当前依据\n\n- [所属计划](../plan/2026.11.alpha-gov.plan.md)\n\n"
        "## 2. 总体架构核验与符合性审计\n\n"
        "Claude 基于 <= 1,000 字摘要单完成架构审计：`session_id = ss-c-t4`, `task_id = tk-c-t4`, `turn_id = tr-c-t4`。\n\n"
        "## 3. 工程细节与防漏评审\n\n"
        f"{detail_reviewer} 完成脚本反作弊与哈希验真：`thread_id = th-rd-t4`, `task_id = tk-rd-t4`, `turn_id = tr-rd-t4`。\n\n"
        "## 4. 架构疑难升级裁定\n\n无架构级疑难升级。\n\n"
        "## 5. 问题闭环与放行结论\n\n未闭环 HIGH/MEDIUM：0\n\n- `[CLOSED]` V1：已核验。\n",
        encoding="utf-8",
        newline="\n",
    )

    plan_path.write_text(
        "# Alpha 分级治理专项计划\n\n"
        "负责人：Mengsk\n"
        f"助手：Gemini（方案编制）、{t3_author}（编码与实跑）、Claude（架构核验与审计）、Codex（细节评审）\n"
        "计划等级：复杂研发计划\n"
        "计划状态：进行中\n"
        "更新时间：2026-11-15\n"
        "状态依据：已获 CP0 批准。\n\n"
        "## 1. 背景说明\n\n跨仓核心治理改造。\n\n"
        "## 2. 目标职责\n\n交付完整分级管控能力。\n\n"
        "## 3. 完成条件\n\n"
        "### 3.1 整体完成标准\n\n全部门禁通过。\n\n"
        "### 3.2 人类检查点（2 个）\n\n"
        "#### 3.2.1 CP0：人类审核技术方案\n\n"
        "- 关联设计：[专题设计](../design/alpha-gov.md)\n"
        "- 当前结论：已通过\n\n"
        "#### 3.2.2 CP1：验收审批\n\n"
        "- 抽检线上与验收文档。\n"
        "- 当前结论：待检查\n\n"
        "## 4. 计划说明\n\n"
        "### 4.1 阶段安排\n\n"
        "#### 4.1.1 T1：完善计划与方案\n\n完成设计与考卷。\n\n"
        "#### 4.1.2 T2：分析影响范围\n\n完成全仓扫描。\n\n"
        "#### 4.1.3 T3：实施子计划\n\n完成编码。\n\n"
        "#### 4.1.4 T4：验收子计划\n\n完成实跑与双轨验真。\n\n"
        "#### 4.1.5 T5：人类验收子计划\n\n呈报 CP1，批准后收尾归档。\n\n"
        "### 4.2 设计规划\n\n- [专题设计](../design/alpha-gov.md)\n\n"
        "## 5. 当前进展\n\n"
        "完成 **4/5 项（80%）**，按末级子任务计数。\n\n"
        "### 5.1 T1 完善计划与方案（1/1）\n\n"
        "- [x] T1.1：编制专题设计与预设考卷。\n"
        "  - 关联文档：[专题设计](../design/alpha-gov.md)\n"
        "  - 关联进展：[T1 变更](../archive/changes/change_20261115_t1-spec.md)\n\n"
        "### 5.2 T2 分析影响范围（1/1）\n\n"
        "- [x] T2.1：完成全仓扫描与评审。\n"
        "  - 关联文档：[专题设计](../design/alpha-gov.md)\n"
        "  - 关联进展：[T2 变更](../archive/changes/change_20261115_t2-scan.md)\n\n"
        "### 5.3 T3 实施子计划（1/1）\n\n"
        "- [x] T3.1：完成核心模块实现。\n"
        "  - 关联文档：[专题设计](../design/alpha-gov.md)\n"
        "  - 关联进展：[T3 变更](../archive/changes/change_20261115_t3-impl.md)\n\n"
        "### 5.4 T4 验收子计划（1/1）\n\n"
        "- [x] T4.1：完成全场景实跑与双轨验真。\n"
        "  - 关联文档：[专项验收](../acceptance/accept_20261115_alpha-gov.md)\n"
        "  - 关联进展：[T4 变更](../archive/changes/change_20261115_t4-run.md)\n\n"
        "### 5.5 T5 人类验收子计划（0/1）\n\n"
        "- [ ] T5.1：呈报 CP1，批准后同构归档。\n"
        "  - 关联文档：[专项验收](../acceptance/accept_20261115_alpha-gov.md)\n"
        "  - 关联进展：无\n\n"
        "## 6. 中断恢复\n\n暂无\n\n"
        "## 7. 审批记录\n\n"
        f"- **A01（2026-11-15，Mengsk 批准）**：批准 CP0，采用 D4-A，锁卷哈希 `{lock_hash}`。\n\n"
        "## 8. 当前依据\n\n- [专题设计](../design/alpha-gov.md)\n",
        encoding="utf-8",
        newline="\n",
    )

    return {
        "root": root,
        "plan": plan_path,
        "design": design_path,
        "accept": accept_path,
        "t1": t1_rev,
        "t2": t2_rev,
        "t4": t4_rev,
        "img": img_path,
        "receipt": rec_path,
        "img_name": Path(img_name),
    }


class PlanLintMutationTests(unittest.TestCase):
    def assert_gate(self, plan_path: Path, gate: str, expected_rc: int, expected_codes: list[str], **kwargs):
        issues, rc = plan_lint.check_plan(plan_path, gate, **kwargs)
        actual_codes = sorted({i.code for i in issues})
        self.assertEqual(rc, expected_rc, f"gate={gate} issues={issues}")
        self.assertEqual(actual_codes, sorted(expected_codes), f"gate={gate} issues={issues}")

    # 1. P1: Full lifecycle + T1 superseded by T2 + T2 zero-Claude + CP0 during T4 + D4-A substitute
    def test_p1_full_complex_lifecycle_and_d4a_substitute(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            # Make T1 hash older (superseded by T2 design evolution) -> CP0 must still pass!
            ws["t1"].write_text(
                ws["t1"].read_text("utf-8").replace(
                    ws["t2"].read_text("utf-8").split("评审对象哈希：")[1].splitlines()[0],
                    "sha256:" + "1" * 64,
                ),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "draft", 0, [])
            self.assert_gate(ws["plan"], "cp0", 0, [])
            self.assert_gate(ws["plan"], "cp1", 0, [])

            # Re-running cp0 while acceptance is in '实跑取证中' must also pass without state conflict
            acc_running = ws["accept"].read_text("utf-8").replace("验收状态：智能体已验真", "验收状态：实跑取证中")
            ws["accept"].write_text(acc_running, encoding="utf-8")
            self.assert_gate(ws["plan"], "cp0", 0, [])

            # Advance to CP1已通过 with affirmative Ch.7 CP1 human approval and run archive-preflight (T5.1 still [ ])
            ws["accept"].write_text(
                acc_running.replace("验收状态：实跑取证中", "验收状态：CP1已通过"), encoding="utf-8"
            )
            ws["plan"].write_text(
                ws["plan"]
                .read_text("utf-8")
                .replace("当前结论：待检查", "当前结论：已通过")
                .replace(
                    "\n## 8. 当前依据",
                    "- **A02（2026-11-15，Mengsk 批准）**：完成宿主与验收文档双抽检，正式批准 CP1 并授权归档。\n\n## 8. 当前依据",
                ),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "archive-preflight", 0, [])

            # Symmetrically archive plan, reviews, acceptance and update inbound links
            arc_plan = ws["root"] / "docs/archive/plan" / ws["plan"].name
            ws["plan"].write_text(
                ws["plan"]
                .read_text("utf-8")
                .replace("计划状态：进行中", "计划状态：已完成")
                .replace("完成 **4/5 项（80%）**", "完成 **5/5 项（100%）**")
                .replace("### 5.5 T5 人类验收子计划（0/1）", "### 5.5 T5 人类验收子计划（1/1）")
                .replace("- [ ] T5.1：", "- [x] T5.1：")
                .replace("../design/alpha-gov.md", "../../design/alpha-gov.md")
                .replace("../archive/changes/", "../changes/")
                .replace("../acceptance/", "../acceptance/"),
                encoding="utf-8",
            )
            shutil.move(str(ws["plan"]), str(arc_plan))
            for r_key in ("t1", "t2", "t4"):
                shutil.move(str(ws[r_key]), str(ws["root"] / "docs/archive/reviews" / ws[r_key].name))
            shutil.move(str(ws["accept"]), str(ws["root"] / "docs/archive/acceptance" / ws["accept"].name))
            ws["design"].write_text(
                ws["design"].read_text("utf-8").replace("../plan/2026.11.alpha-gov.plan.md", "../archive/plan/2026.11.alpha-gov.plan.md"),
                encoding="utf-8",
            )
            self.assert_gate(arc_plan, "archive", 0, [])

        # D4-A legitimate substitute: Codex is T3/T4 author -> Gemini substitutes detail review in t4
        with tempfile.TemporaryDirectory() as tmp2:
            ws_sub = build_valid_complex_workspace(Path(tmp2), t3_author="Codex")
            self.assert_gate(ws_sub["plan"], "cp1", 0, [])

    # 2. F1: Missing or fake TXT diagram blocks CP0 with PL-CP0-TXT
    def test_f1_missing_or_fake_txt_diagram_blocks_cp0(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            orig = ws["design"].read_text("utf-8")
            # Subcase 1: remove text fence completely
            no_txt = orig.replace(
                "```text\n+-------------------+      +-------------------+\n|   Client Input    | ---> |   Gate Checker    |\n+-------------------+      +-------------------+\n```\n\n",
                "",
            )
            ws["design"].write_text(no_txt, encoding="utf-8")
            new_h = plan_lint.compute_review_hash("t2", ws["accept"], ws["design"])
            ws["t2"].write_text(
                re.sub(r"评审对象哈希：sha256:[0-9a-f]{64}", f"评审对象哈希：{new_h}", ws["t2"].read_text("utf-8")),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-CP0-TXT"])

            # Subcase 2: 1-line fake marker inside text fence
            fake_txt = orig.replace(
                "+-------------------+      +-------------------+\n|   Client Input    | ---> |   Gate Checker    |\n+-------------------+      +-------------------+",
                "fake placeholder only",
            )
            ws["design"].write_text(fake_txt, encoding="utf-8")
            new_h2 = plan_lint.compute_review_hash("t2", ws["accept"], ws["design"])
            ws["t2"].write_text(
                re.sub(r"评审对象哈希：sha256:[0-9a-f]{64}", f"评审对象哈希：{new_h2}", ws["t2"].read_text("utf-8")),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-CP0-TXT"])

    # 3. F2a: Missing receipt or tampered image bytes blocks CP0 with PL-CP0-IMG
    def test_f2a_missing_receipt_or_tampered_image_hash_blocks_cp0(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            saved_rec = ws["receipt"].read_bytes()
            ws["receipt"].unlink()
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-CP0-IMG"])

            ws["receipt"].write_bytes(saved_rec)
            ws["img"].write_bytes(TINY_PNG + b"\x00")
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-CP0-IMG"])

    # 4. F2b: Unmounted .gdrive returns exit code 2 and PL-GDRIVE-UNMOUNTED
    def test_f2b_unmounted_gdrive_returns_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            shutil.rmtree(ws["root"] / ".gdrive")
            self.assert_gate(ws["plan"], "cp0", 2, ["PL-GDRIVE-UNMOUNTED"])

    # PL-CP0-PROC: acceptance procedure review record and per-scenario four elements are required at CP0
    def test_cp0_requires_acceptance_procedure_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            acc_orig = ws["accept"].read_text("utf-8")
            ws["accept"].write_text(acc_orig.replace("- **Claude 修订**：Claude 修订定稿。\n", ""), encoding="utf-8")
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-CP0-PROC", "PL-REV-STALE"])

            ws["accept"].write_text(acc_orig.replace("- **充分性判据**：", "- **量化通过判据**："), encoding="utf-8")
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-CP0-PROC", "PL-REV-STALE"])

    # 5. F3a: Missing t2 review or [OPEN] mismatch blocks CP0 with PL-CP0-REV
    def test_f3a_missing_t2_or_open_count_mismatch_blocks_cp0(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            t2_orig = ws["t2"].read_text("utf-8")
            ws["t2"].write_text(t2_orig + "- `[OPEN]` M2：未修复中危问题。\n", encoding="utf-8")
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-CP0-REV"])

            ws["t2"].unlink()
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-CP0-REV"])

    # 6. F3b: Stale t2 review-hash blocks CP0, whereas header edit & .gdrive bucket promotion stay green
    def test_f3b_stale_t2_review_hash_vs_exempt_metadata_and_bucket_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            # Exempt change: update design header status + promote pending -> approved bucket
            (ws["root"] / ".gdrive/managed/approved/design").mkdir(parents=True, exist_ok=True)
            (ws["root"] / ".gdrive/managed/receipts/approved/design").mkdir(parents=True, exist_ok=True)
            shutil.move(str(ws["img"]), str(ws["root"] / ".gdrive/managed/approved/design" / ws["img_name"]))
            shutil.move(
                str(ws["receipt"]),
                str(ws["root"] / ".gdrive/managed/receipts/approved/design" / f"{ws['img_name']}.json"),
            )
            ws["design"].write_text(
                ws["design"]
                .read_text("utf-8")
                .replace("设计状态：草案", "设计状态：已批准")
                .replace(".gdrive/managed/pending/", ".gdrive/managed/approved/"),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp0", 0, [])

            # Single mutation in frozen body section without updating t2 review-hash
            ws["design"].write_text(ws["design"].read_text("utf-8") + "\n未经评审悄悄追加架构段落。\n", encoding="utf-8")
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-REV-STALE"])

    # 7. F3c (part 1): Forced d4b without approval, author overlap, single-agent dual role, and Claude detail ban
    def test_f3c_indep_forced_d4b_author_overlap_dual_role_and_claude_detail_ban(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            # (a) Force --indep-mode d4b when Ch.7 approved D4-A
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-REV-INDEP"], indep_mode="d4b")

            # (b) Author Gemini overlaps with t2 reviewer
            t2_orig = ws["t2"].read_text("utf-8")
            ws["t2"].write_text(
                t2_orig.replace("评审助手：Codex（全仓影响面防漏评审）", "评审助手：Gemini（全仓影响面防漏评审）"),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-REV-INDEP"])

            # (c) Claude prohibited from substituting engineering detail review
            ws["t2"].write_text(
                t2_orig.replace("评审助手：Codex（全仓影响面防漏评审）", "评审助手：Claude（工程细节防漏评审）"),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-REV-INDEP"])
            ws["t2"].write_text(t2_orig, encoding="utf-8")

            # (d) Single agent serving dual roles in t1
            t1_orig = ws["t1"].read_text("utf-8")
            ws["t1"].write_text(
                t1_orig.replace("评审助手：Claude（总体架构核验）、Codex（工程细节评审）", "评审助手：Claude（总体架构核验）"),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-REV-INDEP"])

    # 8. F3c (part 2): D4-B approved in Ch.7 requires isolated session_id and explicit header flag
    def test_f3c_d4b_approved_session_overlap_or_missing_flag_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            ws["plan"].write_text(
                ws["plan"].read_text("utf-8").replace("采用 D4-A", "特批采用 D4-B"), encoding="utf-8"
            )
            t2_base = ws["t2"].read_text("utf-8").replace(
                "评审助手：Codex（全仓影响面防漏评审）", "评审助手：Gemini（全仓影响面防漏评审）"
            ).replace("Codex 全仓防漏审通过：`thread_id = th-x-t2`", "Gemini 隔离防漏审通过：`session_id = sess-iso-99`")

            # Missing 同源隔离会话评审：是 -> PL-REV-INDEP
            ws["t2"].write_text(t2_base, encoding="utf-8")
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-REV-INDEP"])

            # Add flag -> passes!
            with_flag = t2_base.replace("评审结论：已通过", "同源隔离会话评审：是\n评审结论：已通过")
            ws["t2"].write_text(with_flag, encoding="utf-8")
            self.assert_gate(ws["plan"], "cp0", 0, [])

            # Overlapping session_id with author's sess-author-01 -> PL-REV-INDEP
            ws["t2"].write_text(with_flag.replace("sess-iso-99", "sess-author-01"), encoding="utf-8")
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-REV-INDEP"])

    # 9. F4: Missing acceptance doc or zero scenarios in Chapter 2 blocks CP0 with PL-CP0-ACC
    def test_f4_missing_acceptance_or_zero_scenarios_blocks_cp0(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            acc_orig = ws["accept"].read_text("utf-8")
            no_scen = acc_orig.replace("### 2.1 S1：核心门禁验证", "### 2.1 概述说明")
            ws["accept"].write_text(no_scen, encoding="utf-8")
            new_h = plan_lint.compute_review_hash("t2", ws["accept"], ws["design"])
            ws["t2"].write_text(
                re.sub(r"评审对象哈希：sha256:[0-9a-f]{64}", f"评审对象哈希：{new_h}", ws["t2"].read_text("utf-8")),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-CP0-ACC"])

            ws["accept"].unlink()
            self.assert_gate(ws["plan"], "cp0", 1, ["PL-CP0-ACC"])

    # 10. F5a: Residual placeholder or missing SHA-256 in acceptance blocks CP1 with PL-CP1-ACC
    def test_f5a_unfilled_acceptance_or_missing_sha256_blocks_cp1(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            acc_unfilled = ws["accept"].read_text("utf-8").replace(
                "- `S1` 实跑通过：退出码 `0`，18 项断言全部通过。", "待 T4 实跑回填。"
            )
            ws["accept"].write_text(acc_unfilled, encoding="utf-8")
            new_t4_h = plan_lint.compute_review_hash("t4", ws["accept"])
            ws["t4"].write_text(
                re.sub(r"评审对象哈希：sha256:[0-9a-f]{64}", f"评审对象哈希：{new_t4_h}", ws["t4"].read_text("utf-8")),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-CP1-ACC"])

    # 11. F5b: Missing t4 review hits PL-CP1-REV; tampering frozen 2..4.4 hits PL-REV-STALE; appending 4.5/5 passes
    def test_f5b_missing_t4_review_or_tampered_frozen_evidence_vs_exempt_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            # Exempt edits in 4.5, Ch.5, and header status -> still 0!
            acc_tail = (
                ws["accept"]
                .read_text("utf-8")
                .replace("验收状态：智能体已验真", "验收状态：CP1已通过")
                .replace("- 待核签。", "- 已核签 A02。")
            )
            ws["accept"].write_text(acc_tail, encoding="utf-8")
            self.assert_gate(ws["plan"], "cp1", 0, [])

            # Tamper frozen 4.3 SHA-256 anchor without re-signing t4 -> PL-REV-STALE
            ws["accept"].write_text(acc_tail.replace("a" * 64, "c" * 64), encoding="utf-8")
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-REV-STALE"])

            # Restore acceptance and delete t4 review -> PL-CP1-REV
            ws["accept"].write_text(acc_tail, encoding="utf-8")
            ws["t4"].unlink()
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-CP1-REV"])

    # 12. F5c: T4 reviewer overlap with T3 author or Claude substituting script audit hits PL-REV-INDEP
    def test_f5c_cp1_author_overlap_or_claude_substituting_script_audit_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp), t3_author="Gemini")
            t4_orig = ws["t4"].read_text("utf-8")
            # Author Gemini listed as T4 detail reviewer
            ws["t4"].write_text(
                t4_orig.replace("Codex（脚本反作弊与哈希证据链验真）", "Gemini（脚本反作弊与哈希证据链验真）"),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-REV-INDEP"])

            # Claude pulled in to do script anti-cheat detail review
            ws["t4"].write_text(
                t4_orig.replace("Codex（脚本反作弊与哈希证据链验真）", "Claude（脚本反作弊细节评审）"),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-REV-INDEP"])

    # 13. F6a: Tampering Chapter 2 locked exam text hits PL-CP1-LOCK
    def test_f6a_tampered_chapter2_lock_hash_blocks_cp1(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            ws["accept"].write_text(
                ws["accept"].read_text("utf-8").replace("退出码 `0` 且断言全部通过。", "退出码 `0` 即可。"),
                encoding="utf-8",
            )
            new_t4_h = plan_lint.compute_review_hash("t4", ws["accept"])
            ws["t4"].write_text(
                re.sub(r"评审对象哈希：sha256:[0-9a-f]{64}", f"评审对象哈希：{new_t4_h}", ws["t4"].read_text("utf-8")),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-CP1-LOCK"])

    # 14. F6b: Leaving a T1..T4 task unchecked hits PL-CP1-TASKS
    def test_f6b_unchecked_t1_to_t4_task_blocks_cp1(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            ws["plan"].write_text(
                ws["plan"]
                .read_text("utf-8")
                .replace("完成 **4/5 项（80%）**", "完成 **3/5 项（60%）**")
                .replace("### 5.4 T4 验收子计划（1/1）", "### 5.4 T4 验收子计划（0/1）")
                .replace("- [x] T4.1：", "- [ ] T4.1："),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-CP1-TASKS"])

    # 15. F7 (& M3): Independent single-variable archive anomalies (active remnant, missing core doc, docs/ link, root/agents link)
    def test_f7_asymmetric_archive_remnant_or_broken_inbound_link_blocks_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            ws["accept"].write_text(
                ws["accept"].read_text("utf-8").replace("验收状态：智能体已验真", "验收状态：CP1已通过"),
                encoding="utf-8",
            )
            arc_plan = ws["root"] / "docs/archive/plan" / ws["plan"].name
            ws["plan"].write_text(
                ws["plan"]
                .read_text("utf-8")
                .replace("计划状态：进行中", "计划状态：已完成")
                .replace("当前结论：待检查", "当前结论：已通过")
                .replace(
                    "\n## 8. 当前依据",
                    "- **A02（2026-11-15，Mengsk 批准）**：双抽检通过，正式批准 CP1 并授权归档。\n\n## 8. 当前依据",
                )
                .replace("完成 **4/5 项（80%）**", "完成 **5/5 项（100%）**")
                .replace("### 5.5 T5 人类验收子计划（0/1）", "### 5.5 T5 人类验收子计划（1/1）")
                .replace("- [ ] T5.1：", "- [x] T5.1：")
                .replace("../design/alpha-gov.md", "../../design/alpha-gov.md")
                .replace("../archive/changes/", "../changes/"),
                encoding="utf-8",
            )
            shutil.move(str(ws["plan"]), str(arc_plan))
            for r_key in ("t1", "t2", "t4"):
                shutil.move(str(ws[r_key]), str(ws["root"] / "docs/archive/reviews" / ws[r_key].name))
            arc_accept = ws["root"] / "docs/archive/acceptance" / ws["accept"].name
            shutil.move(str(ws["accept"]), str(arc_accept))
            design_clean = ws["design"].read_text("utf-8").replace(
                "../plan/2026.11.alpha-gov.plan.md", "../archive/plan/2026.11.alpha-gov.plan.md"
            )
            ws["design"].write_text(design_clean, encoding="utf-8")
            # Baseline archived state passes cleanly
            self.assert_gate(arc_plan, "archive", 0, [])

            # Single mutation (a): active remnant left in docs/reviews/ ONLY -> PL-ARC-SYM
            remnant_rev = ws["root"] / "docs/reviews" / ws["t4"].name
            shutil.copy2(ws["root"] / "docs/archive/reviews" / ws["t4"].name, remnant_rev)
            self.assert_gate(arc_plan, "archive", 1, ["PL-ARC-SYM"])
            remnant_rev.unlink()
            self.assert_gate(arc_plan, "archive", 0, [])

            # Single mutation (b): missing archived acceptance doc ONLY -> PL-ARC-SYM
            saved_acc = arc_accept.read_bytes()
            arc_accept.unlink()
            self.assert_gate(arc_plan, "archive", 1, ["PL-ARC-SYM"])
            arc_accept.write_bytes(saved_acc)
            self.assert_gate(arc_plan, "archive", 0, [])

            # Single mutation (c): stale inbound link inside docs/design/ ONLY -> PL-ARC-SYM
            ws["design"].write_text(
                design_clean.replace("../archive/plan/2026.11.alpha-gov.plan.md", "../plan/2026.11.alpha-gov.plan.md"),
                encoding="utf-8",
            )
            self.assert_gate(arc_plan, "archive", 1, ["PL-ARC-SYM"])
            ws["design"].write_text(design_clean, encoding="utf-8")
            self.assert_gate(arc_plan, "archive", 0, [])

            # Single mutation (d / M3): stale inbound link in repo root README.md and agents/README.md (outside docs/) -> PL-ARC-SYM
            root_readme = ws["root"] / "README.md"
            root_readme.write_text("# 仓库索引\n\n- [旧计划入口](docs/plan/2026.11.alpha-gov.plan.md)\n", encoding="utf-8")
            self.assert_gate(arc_plan, "archive", 1, ["PL-ARC-SYM"])
            root_readme.unlink()
            self.assert_gate(arc_plan, "archive", 0, [])

            agents_readme = ws["root"] / "agents/README.md"
            agents_readme.parent.mkdir(parents=True, exist_ok=True)
            agents_readme.write_text("# 全局提示词\n\n- [旧评审入口](../docs/reviews/review_20261115_alpha-gov_t4.md)\n", encoding="utf-8")
            self.assert_gate(arc_plan, "archive", 1, ["PL-ARC-SYM"])
            agents_readme.unlink()
            self.assert_gate(arc_plan, "archive", 0, [])

    # 16. F8: Missing or renamed complex stage blocks draft with PL-DRAFT-STRUCT
    def test_f8_missing_or_renamed_complex_stage_blocks_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            ws["plan"].write_text(
                ws["plan"].read_text("utf-8").replace("T2 分析影响范围", "T2 直接跳步编码"),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "draft", 1, ["PL-DRAFT-STRUCT"])

    # 17. F9: Simple plan with >= 2 CPs or unapproved complex scope blocks draft with PL-LVL-DOWNGRADE
    def test_f9_simple_plan_two_checkpoints_or_unapproved_complex_scope_blocks_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, _, _ = run_cli(
                "init", "--level", "simple", "--slug", "demo", "--title", "示例简单计划",
                "--owner", "Mengsk", "--date", "2026-11-15", "--root", str(root),
            )
            self.assertEqual(rc, 0)
            p = root / "docs/plan/2026.11.demo.plan.md"
            base = p.read_text("utf-8")
            self.assert_gate(p, "draft", 0, [])

            # Mutation 1: 2 checkpoints on simple plan
            two_cp = base.replace("### 3.2 人类检查点（0 个）", "### 3.2 人类检查点（2 个）\n\n#### 3.2.1 CP0\n\n#### 3.2.2 CP1")
            p.write_text(two_cp, encoding="utf-8")
            self.assert_gate(p, "draft", 1, ["PL-LVL-DOWNGRADE"])

            # Mutation 2: unapproved complex scope (cross-repo + DB Schema migration)
            p.write_text(
                base.replace("确认不涉及跨仓改造、公共契约变更或数据库迁移。", "本次需跨 3 个代码仓修改公共契约并执行数据库 Schema 迁移。"),
                encoding="utf-8",
            )
            self.assert_gate(p, "draft", 1, ["PL-LVL-DOWNGRADE"])

    # 18. F10a: Unlisted ungraded plan or mismatched repo_id blocks draft with PL-LVL-REQ
    def test_f10a_unlisted_or_wrong_repo_id_legacy_plan_blocks_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs/plan").mkdir(parents=True)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
            p = root / "docs/plan/2026.10.old.plan.md"
            p.write_text("# 旧计划\n\n负责人：Mengsk\n计划状态：进行中\n\n## 1. 背景说明\n\n2026-10-15 立项。\n", encoding="utf-8")
            env = os.environ.copy()
            env["GIT_AUTHOR_DATE"] = "2026-10-15T10:00:00+08:00"
            env["GIT_COMMITTER_DATE"] = "2026-10-15T10:00:00+08:00"
            subprocess.run(["git", "add", "."], cwd=root, env=env, check=True)
            subprocess.run(["git", "commit", "-m", "init old legacy"], cwd=root, env=env, check=True, capture_output=True)

            # Without .legacy-plans.json -> PL-LVL-REQ
            self.assert_gate(p, "draft", 1, ["PL-LVL-REQ"], repo_id="org/repo-a")

            rc, _, _ = run_cli("freeze", "--root", str(root), "--repo-id", "org/repo-a")
            self.assertEqual(rc, 0)
            self.assert_gate(p, "draft", 0, [], repo_id="org/repo-a")

            # Unlisted plan added after freeze -> PL-LVL-REQ
            p_unlisted = root / "docs/plan/2026.11.unlisted.plan.md"
            p_unlisted.write_text("# 清单外计划\n\n负责人：Mengsk\n计划状态：草案\n\n## 1. 背景说明\n\n新立项。\n", encoding="utf-8")
            self.assert_gate(p_unlisted, "draft", 1, ["PL-LVL-REQ"], repo_id="org/repo-a")

            # Cross-repo wrong repo_id -> PL-LVL-REQ
            self.assert_gate(p, "draft", 1, ["PL-LVL-REQ"], repo_id="org/repo-b")

    # 19. F10b: Independent verification of BOTH ratchets (migrated: true AND git log -S revert detection) + freeze one-time guard
    def test_f10b_migrated_flag_or_git_history_ratchet_prevents_revert_escape(self):
        # Subcase A: Pure migrated: true ratchet (never committed 计划等级： to git history)
        with tempfile.TemporaryDirectory() as tmp_a:
            root_a = Path(tmp_a)
            (root_a / "docs/plan").mkdir(parents=True)
            subprocess.run(["git", "init"], cwd=root_a, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root_a, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=root_a, check=True)
            p_a = root_a / "docs/plan/2026.10.mig-flag.plan.md"
            initial_txt = "# 存量计划\n\n负责人：Mengsk\n计划状态：进行中\n\n## 1. 背景说明\n\n存量任务。\n"
            p_a.write_text(initial_txt, encoding="utf-8")
            env = os.environ.copy()
            env["GIT_AUTHOR_DATE"] = "2026-10-20T10:00:00+08:00"
            env["GIT_COMMITTER_DATE"] = "2026-10-20T10:00:00+08:00"
            subprocess.run(["git", "add", "."], cwd=root_a, env=env, check=True)
            subprocess.run(["git", "commit", "-m", "init legacy"], cwd=root_a, env=env, check=True, capture_output=True)

            rc, _, _ = run_cli("freeze", "--root", str(root_a), "--repo-id", "org/ratchet")
            self.assertEqual(rc, 0)
            rc2, _, _ = run_cli("freeze", "--root", str(root_a), "--repo-id", "org/ratchet")
            self.assertEqual(rc2, 2)
            self.assert_gate(p_a, "draft", 0, [], repo_id="org/ratchet")

            # Add 计划等级 in working tree (not committed), run migrate-legacy, then revert working tree text
            p_a.write_text(initial_txt.replace("计划状态：进行中", "计划等级：简单计划\n计划状态：进行中"), encoding="utf-8")
            rc_mig, _, _ = run_cli("migrate-legacy", str(p_a), "--root", str(root_a))
            self.assertEqual(rc_mig, 0)
            manifest_data = json.loads((root_a / "docs/plan/.legacy-plans.json").read_text("utf-8"))
            self.assertIs(manifest_data["plans"]["docs/plan/2026.10.mig-flag.plan.md"]["migrated"], True)
            p_a.write_text(initial_txt, encoding="utf-8")
            self.assert_gate(p_a, "draft", 1, ["PL-LVL-REQ"], repo_id="org/ratchet")

        # Subcase B: Pure git log -S ratchet (migrated remains False in .legacy-plans.json)
        with tempfile.TemporaryDirectory() as tmp_b:
            root_b = Path(tmp_b)
            (root_b / "docs/plan").mkdir(parents=True)
            subprocess.run(["git", "init"], cwd=root_b, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root_b, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=root_b, check=True)
            p_b = root_b / "docs/plan/2026.10.ratchet.plan.md"
            p_b.write_text(initial_txt, encoding="utf-8")
            env = os.environ.copy()
            env["GIT_AUTHOR_DATE"] = "2026-10-20T10:00:00+08:00"
            env["GIT_COMMITTER_DATE"] = "2026-10-20T10:00:00+08:00"
            subprocess.run(["git", "add", "."], cwd=root_b, env=env, check=True)
            subprocess.run(["git", "commit", "-m", "init legacy"], cwd=root_b, env=env, check=True, capture_output=True)
            run_cli("freeze", "--root", str(root_b), "--repo-id", "org/ratchet")

            p_b.write_text(initial_txt.replace("计划状态：进行中", "计划等级：简单计划\n计划状态：进行中"), encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root_b, check=True)
            subprocess.run(["git", "commit", "-m", "grade plan"], cwd=root_b, check=True, capture_output=True)
            p_b.write_text(initial_txt, encoding="utf-8")
            manifest_b = json.loads((root_b / "docs/plan/.legacy-plans.json").read_text("utf-8"))
            self.assertIs(manifest_b["plans"]["docs/plan/2026.10.ratchet.plan.md"]["migrated"], False)
            self.assert_gate(p_b, "draft", 1, ["PL-LVL-REQ"], repo_id="org/ratchet")

    # 20. P2: Simple S0 and S1 plans + unmigrated legacy checkbox updates pass cleanly
    def test_p2_simple_s0_and_s1_plans_and_legacy_checkbox_update_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs/archive/plan").mkdir(parents=True)
            # (a) S0 0-CP zero-human-touch simple plan full lifecycle
            rc_s0, _, _ = run_cli(
                "init", "--level", "simple", "--slug", "s0-auto", "--title", "零人工自闭环修复",
                "--owner", "Mengsk", "--cp-mode", "s0", "--date", "2026-11-15", "--root", str(root),
            )
            self.assertEqual(rc_s0, 0)
            p_s0 = root / "docs/plan/2026.11.s0-auto.plan.md"
            self.assert_gate(p_s0, "draft", 0, [])
            txt_s0 = (
                p_s0.read_text("utf-8")
                .replace("完成 **0/2 项（0%）**", "完成 **1/2 项（50%）**")
                .replace("### 5.1 T1 实施与验证（0/1）", "### 5.1 T1 实施与验证（1/1）")
                .replace("- [ ] T1.1：", "- [x] T1.1：")
            )
            p_s0.write_text(txt_s0, encoding="utf-8")
            self.assert_gate(p_s0, "archive-preflight", 0, [])
            arc_s0 = root / "docs/archive/plan" / p_s0.name
            p_s0.write_text(
                txt_s0.replace("计划状态：草案", "计划状态：已完成")
                .replace("完成 **1/2 项（50%）**", "完成 **2/2 项（100%）**")
                .replace("### 5.2 T2 自验收与归档（0/1）", "### 5.2 T2 自验收与归档（1/1）")
                .replace("- [ ] T2.1：", "- [x] T2.1："),
                encoding="utf-8",
            )
            shutil.move(str(p_s0), str(arc_s0))
            self.assert_gate(arc_s0, "archive", 0, [])

            # (b) S1 1-CP simple plan with affirmative Ch.7 human approval
            rc, _, _ = run_cli(
                "init", "--level", "simple", "--slug", "s1-fix", "--title", "单点确认修复",
                "--owner", "Mengsk", "--cp-mode", "s1-cp1", "--date", "2026-11-15", "--root", str(root),
            )
            self.assertEqual(rc, 0)
            p = root / "docs/plan/2026.11.s1-fix.plan.md"
            self.assert_gate(p, "draft", 0, [])

            txt = (
                p.read_text("utf-8")
                .replace("完成 **0/2 项（0%）**", "完成 **1/2 项（50%）**")
                .replace("### 5.1 T1 实施与验证（0/1）", "### 5.1 T1 实施与验证（1/1）")
                .replace("- [ ] T1.1：", "- [x] T1.1：")
                .replace("当前结论**：待提交。", "当前结论**：已通过。")
                .replace(
                    "## 7. 审批记录\n\n暂无",
                    "## 7. 审批记录\n\n- **A01（2026-11-15，Mengsk 批准）**：单点体验确认通过，授权归档。",
                )
            )
            p.write_text(txt, encoding="utf-8")
            self.assert_gate(p, "archive-preflight", 0, [])

            arc_p = root / "docs/archive/plan" / p.name
            p.write_text(
                txt.replace("计划状态：草案", "计划状态：已完成")
                .replace("完成 **1/2 项（50%）**", "完成 **2/2 项（100%）**")
                .replace("### 5.2 T2 自验收与归档（0/1）", "### 5.2 T2 自验收与归档（1/1）")
                .replace("- [ ] T2.1：", "- [x] T2.1："),
                encoding="utf-8",
            )
            shutil.move(str(p), str(arc_p))
            self.assert_gate(arc_p, "archive", 0, [])

        # (c) Unmigrated frozen legacy plan normal checkbox progress update continues to pass draft
        with tempfile.TemporaryDirectory() as tmp_leg:
            leg_root = Path(tmp_leg)
            (leg_root / "docs/plan").mkdir(parents=True)
            subprocess.run(["git", "init"], cwd=leg_root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=leg_root, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=leg_root, check=True)
            leg_plan = leg_root / "docs/plan/2026.10.legacy-prog.plan.md"
            leg_txt = (
                "# 历史在途计划\n\n负责人：Mengsk\n计划状态：进行中\n\n"
                "## 1. 背景说明\n\n2026-10-18 立项。\n\n## 5. 当前进展\n\n- [ ] T1.1：存量子任务推进。\n"
            )
            leg_plan.write_text(leg_txt, encoding="utf-8")
            env = os.environ.copy()
            env["GIT_AUTHOR_DATE"] = "2026-10-18T10:00:00+08:00"
            env["GIT_COMMITTER_DATE"] = "2026-10-18T10:00:00+08:00"
            subprocess.run(["git", "add", "."], cwd=leg_root, env=env, check=True)
            subprocess.run(["git", "commit", "-m", "legacy init"], cwd=leg_root, env=env, check=True, capture_output=True)
            rc_frz, _, _ = run_cli("freeze", "--root", str(leg_root), "--repo-id", "org/legacy")
            self.assertEqual(rc_frz, 0)
            leg_plan.write_text(leg_txt.replace("- [ ] T1.1：", "- [x] T1.1："), encoding="utf-8")
            self.assert_gate(leg_plan, "draft", 0, [], repo_id="org/legacy")

    # 21. P3: Deprecated complex plan symmetric archive passes with unchecked tasks
    def test_p3_deprecated_complex_plan_symmetric_archive_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            ws["plan"].write_text(
                ws["plan"]
                .read_text("utf-8")
                .replace("计划状态：进行中", "计划状态：已废弃")
                .replace("../design/alpha-gov.md", "../../design/alpha-gov.md")
                .replace("../archive/changes/", "../changes/"),
                encoding="utf-8",
            )
            ws["accept"].write_text(
                ws["accept"].read_text("utf-8").replace("验收状态：智能体已验真", "验收状态：已废弃"),
                encoding="utf-8",
            )
            for rk in ("t1", "t2", "t4"):
                ws[rk].write_text(
                    ws[rk].read_text("utf-8").replace("评审结论：已通过", "评审结论：已废弃"),
                    encoding="utf-8",
                )
            self.assert_gate(ws["plan"], "archive-preflight", 0, [])

            arc_plan = ws["root"] / "docs/archive/plan" / ws["plan"].name
            shutil.move(str(ws["plan"]), str(arc_plan))
            for rk in ("t1", "t2", "t4"):
                shutil.move(str(ws[rk]), str(ws["root"] / "docs/archive/reviews" / ws[rk].name))
            shutil.move(str(ws["accept"]), str(ws["root"] / "docs/archive/acceptance" / ws["accept"].name))
            ws["design"].write_text(
                ws["design"].read_text("utf-8").replace("../plan/2026.11.alpha-gov.plan.md", "../archive/plan/2026.11.alpha-gov.plan.md"),
                encoding="utf-8",
            )
            self.assert_gate(arc_plan, "archive", 0, [])

    # 22. P4a: classify mutual exclusion & negation context immunity
    def test_p4_classify_mutual_exclusion_and_negation_immunity(self):
        rc_both, _, _ = run_cli("classify", "--text", "abc", "--plan", "x.md")
        self.assertEqual(rc_both, 2)

        rc_neg, out_neg, _ = run_cli(
            "classify",
            "--text",
            "修复单文件文案排版，不涉及跨仓改动，无需数据库 Schema 迁移，免除公共契约变更",
            "--json",
        )
        self.assertEqual(rc_neg, 0)
        res_neg = json.loads(out_neg)
        self.assertEqual(res_neg["level"], "简单计划")
        self.assertEqual(res_neg["recommended_mode"], "S0")
        self.assertEqual(res_neg["score"], 0)

        rc_pos, out_pos, _ = run_cli(
            "classify",
            "--text",
            "跨 3 个代码仓改造并发锁状态机并执行数据库 Schema 迁移",
            "--json",
        )
        self.assertEqual(rc_pos, 0)
        res_pos = json.loads(out_pos)
        self.assertEqual(res_pos["level"], "复杂研发计划")
        self.assertEqual(res_pos["recommended_mode"], "C2.complex")

    # 23. P4b: init non-overwrite, slug traversal rejection, symlink rejection, and duplicate header key rejection
    def test_p4_init_guards_overwrite_symlink_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc1, _, _ = run_cli(
                "init", "--level", "complex", "--slug", "core-x", "--title", "核心计划",
                "--owner", "Mengsk", "--with-core-docs", "--date", "2026-11-15", "--root", str(root),
            )
            self.assertEqual(rc1, 0)
            # Second run without --force must exit 2
            rc2, _, _ = run_cli(
                "init", "--level", "complex", "--slug", "core-x", "--title", "被覆盖标题",
                "--owner", "Mengsk", "--with-core-docs", "--date", "2026-11-15", "--root", str(root),
            )
            self.assertEqual(rc2, 2)
            self.assertIn("# 核心计划", (root / "docs/plan/2026.11.core-x.plan.md").read_text("utf-8"))

            # Path traversal in slug -> exit 2
            rc_bad_slug, _, _ = run_cli(
                "init", "--level", "simple", "--slug", "../escape", "--title", "越界", "--owner", "Mengsk", "--root", str(root)
            )
            self.assertEqual(rc_bad_slug, 2)

            # Symlink component inside root -> exit 2 (zero skip across all OS privilege modes)
            sym_root = root / "sym-ws"
            (sym_root / "docs").mkdir(parents=True)
            with mock.patch.object(Path, "is_symlink", autospec=True, side_effect=lambda p: p.name == "plan"):
                rc_sym, _, _ = run_cli(
                    "init", "--level", "simple", "--slug", "sym", "--title", "软链拦截", "--owner", "Mengsk", "--root", str(sym_root)
                )
                self.assertEqual(rc_sym, 2)

            # Duplicate metadata key in a plan header -> PL-DRAFT-STRUCT
            plan_file = root / "docs/plan/2026.11.core-x.plan.md"
            plan_file.write_text(
                plan_file.read_text("utf-8").replace("计划等级：复杂研发计划", "计划等级：复杂研发计划\n计划等级：复杂研发计划"),
                encoding="utf-8",
            )
            self.assert_gate(plan_file, "draft", 1, ["PL-DRAFT-STRUCT"])

    # 24. P4c: Byte-exact lock-hash regression against the bundled CP0 A03 locked slice + CRLF/BOM invariance
    def test_p4_deterministic_lock_hash_matches_fixed_cp0_a03_baseline(self):
        live_accept = SKILL_ROOT / "tests/fixtures/locked-exam.md"
        self.assertTrue(live_accept.is_file(), f"Missing fixed acceptance fixture: {live_accept}")
        expected_a03_hash = "sha256:150798fa72094168eaa553253f47ff257ddc8f073f4d23eb9c51a8eef56a43ca"

        rc, out, _ = run_cli("lock-hash", str(live_accept), "--json")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["lock_hash"], expected_a03_hash)
        self.assertEqual(payload["scenario_count"], 6)

        with tempfile.TemporaryDirectory() as tmp:
            crlf_copy = Path(tmp) / "accept_crlf_bom.md"
            raw_lf = live_accept.read_text("utf-8")
            crlf_copy.write_bytes(("\ufeff" + raw_lf.replace("\n", "   \r\n")).encode("utf-8"))
            digest2, count2, _ = plan_lint.compute_lock_hash(crlf_copy)
            self.assertEqual(digest2, expected_a03_hash)
            self.assertEqual(count2, 6)

    # 25. H1: Fenced fake headings cannot truncate review-hash/lock-hash; duplicate or misordered headings rejected
    def test_h1_fenced_fake_heading_and_duplicate_or_misordered_chapters_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            acc_base = ws["accept"].read_text("utf-8")

            # Insert fenced fake '### 4.5 fake-heading' in Chapter 3 and re-sign t4
            acc_fenced = acc_base.replace(
                "- `S1` 实跑通过：退出码 `0`，18 项断言全部通过。",
                "```markdown\n### 4.5 fake-heading\n## 3. fake-ch3\n```\n\n- `S1` 实跑通过：退出码 `0`，18 项断言全部通过。",
            )
            ws["accept"].write_text(acc_fenced, encoding="utf-8")
            h_fenced = plan_lint.compute_review_hash("t4", ws["accept"])
            ws["t4"].write_text(
                re.sub(r"评审对象哈希：sha256:[0-9a-f]{64}", f"评审对象哈希：{h_fenced}", ws["t4"].read_text("utf-8")),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 0, [])

            # Mutate evidence AFTER the fenced fake heading -> MUST alter t4 hash and hit PL-REV-STALE!
            ws["accept"].write_text(acc_fenced.replace("a" * 64, "f" * 64), encoding="utf-8")
            self.assertNotEqual(plan_lint.compute_review_hash("t4", ws["accept"]), h_fenced)
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-REV-STALE"])

            # Duplicate ### 4.5 or rogue 4.x outside Ch.4 -> rejected by AST structure validator
            ws["accept"].write_text(
                acc_base.replace("## 3. 智能体实跑与演示证据\n\n", "## 3. 智能体实跑与演示证据\n\n### 4.5 伪造提前截断\n\n"),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                plan_lint.compute_review_hash("t4", ws["accept"])
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-CP1-ACC", "PL-CP1-LOCK", "PL-CP1-REV", "PL-REV-INDEP"])

            # Misordered H2 chapters (swap ## 2. and ## 3.) -> rejected
            ws["accept"].write_text(
                acc_base.replace("## 2. 锁定验收场景与判据", "## 9. 非法章节顺序"),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                plan_lint.compute_lock_hash(ws["accept"])

    # 26. H2: Unapproved/negated CP0 candidate hash blocks CP1; status-only CP1 without Ch.7 approval blocks archive
    def test_h2_unapproved_or_negated_cp0_and_missing_cp1_approval_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            plan_orig = ws["plan"].read_text("utf-8")
            lock_hash, _, _ = plan_lint.compute_lock_hash(ws["accept"])

            # Candidate/negated text with exact lock_hash in Ch.7 MUST block CP1 with PL-CP1-LOCK
            ws["plan"].write_text(
                re.sub(
                    r"- \*\*A01[^\n]+",
                    f"- CP0 尚未批准；仅记录候选摘要 `{lock_hash}`。",
                    plan_orig,
                ),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-CP1-LOCK"])

            # Revision-instruction entry ("指示 / 重新提交 CP0") with exact lock_hash MUST also block CP1
            ws["plan"].write_text(
                re.sub(
                    r"- \*\*A01[^\n]+",
                    f"- **A01（2026-11-15，Mengsk 指示）**：要求修订后重新提交 CP0 审批，候选 `{lock_hash}`。",
                    plan_orig,
                ),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-CP1-LOCK"])

            # Restore valid CP0 approval, set CP1 status text to 已通过 WITHOUT Ch.7 CP1 approval -> blocks archive-preflight!
            ws["accept"].write_text(
                ws["accept"].read_text("utf-8").replace("验收状态：智能体已验真", "验收状态：CP1已通过"),
                encoding="utf-8",
            )
            ws["plan"].write_text(plan_orig.replace("当前结论：待检查", "当前结论：已通过"), encoding="utf-8")
            self.assert_gate(ws["plan"], "archive-preflight", 1, ["PL-ARC-SYM"])

    # 27. H3: Reviewer header vs Ch.3 receipt swap, author session reuse, accept 4.5 mismatch, & missing author change doc
    def test_h3_reviewer_body_receipt_swap_session_reuse_and_missing_author_change_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp), t3_author="Gemini")
            t4_orig = ws["t4"].read_text("utf-8")
            acc_orig = ws["accept"].read_text("utf-8")

            # (a) Keep Codex in t4 header, swap Ch.3 receipt to author Gemini -> PL-REV-INDEP
            ws["t4"].write_text(
                t4_orig.replace(
                    "Codex 完成脚本反作弊与哈希验真：`thread_id = th-rd-t4`",
                    "Gemini 完成脚本反作弊与哈希验真：`session_id = ss-gem-t4`",
                ),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-REV-INDEP"])

            # (b) Keep Codex name in Ch.3, but reuse author's session_id sess-author-01 -> PL-REV-INDEP
            ws["t4"].write_text(t4_orig.replace("th-rd-t4", "sess-author-01"), encoding="utf-8")
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-REV-INDEP"])
            ws["t4"].write_text(t4_orig, encoding="utf-8")

            # (c) Mismatch accept_*.md 4.5 receipt owner vs t4_rev -> PL-REV-INDEP
            ws["accept"].write_text(
                acc_orig.replace("- Codex：`thread_id = th-rd-t4`", "- Gemini：`session_id = ss-gem-99`"),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-REV-INDEP"])
            ws["accept"].write_text(acc_orig, encoding="utf-8")

            # (d) Delete deliverable change_*.md on disk -> fail-closed PL-REV-INDEP
            chg_t3 = ws["root"] / "docs/archive/changes/change_20261115_t3-impl.md"
            chg_t3.unlink()
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-REV-INDEP"])

    # 28. M2: Missing .git, non-zero git exit code, or git timeout fails closed with exit 2 (PL-IO-ERR)
    def test_m2_git_missing_or_query_failure_blocks_legacy_exemption_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs/plan").mkdir(parents=True)
            p = root / "docs/plan/2026.10.legacy-err.plan.md"
            p.write_text("# 旧计划\n\n负责人：Mengsk\n计划状态：进行中\n\n## 1. 背景说明\n\n2026-10-12 立项。\n", encoding="utf-8")
            run_cli("freeze", "--root", str(root), "--repo-id", "org/err-repo")

            # (a) Missing .git directory -> exit 2 PL-IO-ERR (never silently exempted!)
            self.assert_gate(p, "draft", 2, ["PL-IO-ERR"], repo_id="org/err-repo")

            # Initialize .git without commits (git log exits 128) -> exit 2 PL-IO-ERR
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            self.assert_gate(p, "draft", 2, ["PL-IO-ERR"], repo_id="org/err-repo")

            # Commit properly -> now passes cleanly (0)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
            self.assert_gate(p, "draft", 0, [], repo_id="org/err-repo")

            # (b) Simulate git subprocess timeout -> exit 2 PL-IO-ERR
            with mock.patch("plan_lint.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=8)):
                self.assert_gate(p, "draft", 2, ["PL-IO-ERR"], repo_id="org/err-repo")

    # 29. M4: init rejects newline header injection and preserves pre-existing files with zero partial writes on failure
    def test_m4_init_newline_injection_and_force_preserves_existing_files_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_target = root / "docs/plan/2026.11.safe-init.plan.md"
            rev_target = root / "docs/reviews/review_20261115_safe-init_t1.md"
            acc_target = root / "docs/acceptance/accept_20261115_safe-init.md"
            plan_target.parent.mkdir(parents=True, exist_ok=True)
            sentinel = "# 原始受保护计划内容\n\n负责人：Mengsk\n"
            plan_target.write_text(sentinel, encoding="utf-8")

            # (a) Newline injection in --title under --force --with-core-docs -> exits 2, sentinel intact, zero core docs written
            rc_nl, _, _ = run_cli(
                "init", "--level", "complex", "--slug", "safe-init",
                "--title", "标题注入\n计划等级：简单计划", "--owner", "Mengsk",
                "--with-core-docs", "--force", "--date", "2026-11-15", "--root", str(root),
            )
            self.assertEqual(rc_nl, 2)
            self.assertEqual(plan_target.read_text("utf-8"), sentinel)
            self.assertFalse(rev_target.exists())
            self.assertFalse(acc_target.exists())

            # (b) Duplicate metadata key in rendered content under --force --with-core-docs -> caught in memory BEFORE touching disk!
            orig_parse = plan_lint.parse_doc_text

            def fake_dup_on_acc(raw: str, path: Path):
                parsed = orig_parse(raw, path)
                if path.name.startswith("accept_"):
                    parsed.duplicate_keys.append("负责人")
                return parsed

            with mock.patch("plan_lint.parse_doc_text", side_effect=fake_dup_on_acc):
                rc_dup, _, _ = run_cli(
                    "init", "--level", "complex", "--slug", "safe-init",
                    "--title", "合法标题", "--owner", "Mengsk",
                    "--with-core-docs", "--force", "--date", "2026-11-15", "--root", str(root),
                )
            self.assertEqual(rc_dup, 2)
            self.assertEqual(plan_target.read_text("utf-8"), sentinel)
            self.assertFalse(rev_target.exists())
            self.assertFalse(acc_target.exists())

    # 30. H2-R2: Negated ("不同意 CP0"), deferred ("暂缓批准 CP0"), later-revoked CP0, & S1 unrelated ("批准修正文档标题") all block
    def test_h2_r2_negated_deferred_revoked_and_s1_unrelated_approvals_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp))
            plan_orig = ws["plan"].read_text("utf-8")
            lock_hash, _, _ = plan_lint.compute_lock_hash(ws["accept"])

            # (a) "不同意 CP0" with "核签" in meta and exact lock_hash -> MUST block CP1 with PL-CP1-LOCK
            ws["plan"].write_text(
                re.sub(
                    r"- \*\*A01[^\n]+",
                    f"- **A01（2026-11-15，Mengsk 核签）**：不同意 CP0，锁卷哈希 `{lock_hash}`。",
                    plan_orig,
                ),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-CP1-LOCK"])

            # (b) "暂缓批准 CP0" with "核签" in meta and exact lock_hash -> MUST block CP1 with PL-CP1-LOCK
            ws["plan"].write_text(
                re.sub(
                    r"- \*\*A01[^\n]+",
                    f"- **A01（2026-11-15，Mengsk 核签）**：暂缓批准 CP0，锁卷哈希 `{lock_hash}`。",
                    plan_orig,
                ),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-CP1-LOCK"])

            # (c) A01 approved CP0, followed by later A02 suspending/revoking CP0 -> MUST block CP1 with PL-CP1-LOCK
            ws["plan"].write_text(
                plan_orig.replace(
                    f"锁卷哈希 `{lock_hash}`。",
                    f"锁卷哈希 `{lock_hash}`。\n- **A02（2026-11-16，Mengsk 核签）**：暂停 CP0 实施授权，待重新评审。",
                ),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-CP1-LOCK"])

            # (d) S1 simple plan with unrelated approval ("批准修正文档标题") or negated CP1 -> MUST block archive-preflight
            rc, _, _ = run_cli(
                "init", "--level", "simple", "--slug", "s1-strict", "--title", "单点确认校验",
                "--owner", "Mengsk", "--cp-mode", "s1-cp1", "--date", "2026-11-15", "--root", str(ws["root"]),
            )
            self.assertEqual(rc, 0)
            p_s1 = ws["root"] / "docs/plan/2026.11.s1-strict.plan.md"
            base_s1 = (
                p_s1.read_text("utf-8")
                .replace("完成 **0/2 项（0%）**", "完成 **1/2 项（50%）**")
                .replace("### 5.1 T1 实施与验证（0/1）", "### 5.1 T1 实施与验证（1/1）")
                .replace("- [ ] T1.1：", "- [x] T1.1：")
                .replace("当前结论**：待提交。", "当前结论**：已通过。")
            )
            p_s1.write_text(
                base_s1.replace(
                    "## 7. 审批记录\n\n暂无",
                    "## 7. 审批记录\n\n- **A01（2026-11-15，Mengsk 批准）**：批准修正文档标题。",
                ),
                encoding="utf-8",
            )
            self.assert_gate(p_s1, "archive-preflight", 1, ["PL-ARC-SYM"])

            p_s1.write_text(
                base_s1.replace(
                    "## 7. 审批记录\n\n暂无",
                    "## 7. 审批记录\n\n- **A01（2026-11-15，Mengsk 核签）**：不同意 CP1 结项抽检。",
                ),
                encoding="utf-8",
            )
            self.assert_gate(p_s1, "archive-preflight", 1, ["PL-ARC-SYM"])

    # 31. H3-R2: Task ID mismatch, cross-agent receipt swap, and extra unrelated receipts in accept_*.md 4.5 all block
    def test_h3_r2_task_id_mismatch_cross_agent_swap_and_extra_receipts_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = build_valid_complex_workspace(Path(tmp), t3_author="Gemini")
            acc_orig = ws["accept"].read_text("utf-8")

            # (a) Replace one task_id in accept 4.5 with unrelated task -> MUST block CP1 with PL-REV-INDEP
            ws["accept"].write_text(acc_orig.replace("tk-rd-t4", "tk-unrelated-99"), encoding="utf-8")
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-REV-INDEP"])

            # (b) Replace ALL task_ids in accept 4.5 with unrelated tasks -> MUST block CP1 with PL-REV-INDEP
            ws["accept"].write_text(
                acc_orig.replace("tk-rd-t4", "tk-unrelated-99").replace("tk-c-t4", "tk-unrelated-88"),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-REV-INDEP"])

            # (c) Swap receipts between Codex and Claude in accept 4.5 -> MUST block CP1 with PL-REV-INDEP
            swapped = (
                acc_orig.replace(
                    "- Codex：`thread_id = th-rd-t4`, `task_id = tk-rd-t4`, `turn_id = tr-rd-t4`",
                    "- Codex：`session_id = ss-c-t4`, `task_id = tk-c-t4`, `turn_id = tr-c-t4`",
                ).replace(
                    "- Claude：`session_id = ss-c-t4`, `task_id = tk-c-t4`, `turn_id = tr-c-t4`",
                    "- Claude：`thread_id = th-rd-t4`, `task_id = tk-rd-t4`, `turn_id = tr-rd-t4`",
                )
            )
            ws["accept"].write_text(swapped, encoding="utf-8")
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-REV-INDEP"])

            # (d) Append extra unrelated turn_id or unattributed rogue receipt in accept 4.5 -> MUST block CP1 with PL-REV-INDEP
            ws["accept"].write_text(
                acc_orig.replace("`turn_id = tr-rd-t4`", "`turn_id = tr-rd-t4`, `turn_id = tr-rogue-extra`"),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-REV-INDEP"])

            ws["accept"].write_text(
                acc_orig.replace("## 5. CP1 双抽检核验单", "游离附加：`task_id = tk-extra-00`\n\n## 5. CP1 双抽检核验单"),
                encoding="utf-8",
            )
            self.assert_gate(ws["plan"], "cp1", 1, ["PL-REV-INDEP"])


if __name__ == "__main__":
    unittest.main()
