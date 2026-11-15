import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_md  # noqa: E402
import install  # noqa: E402
import md_format  # noqa: E402


def codes(text, extra=None, newline="\n"):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, content in (extra or {}).items():
            (root / name).write_text(content, encoding="utf-8", newline="\n")
        doc = root / "doc.md"
        doc.write_text(text, encoding="utf-8", newline=newline)
        return [(level, code) for _, level, code, _ in check_md.check_file(doc)]


class CheckMdTest(unittest.TestCase):
    def test_clean_document_passes(self):
        text = "# T\n\n## 1. A\n\n- a\n  - b\n\n### 1.1 B\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n## 2. C\n"
        self.assertEqual(codes(text), [])

    def test_crlf_line_endings_is_error(self):
        self.assertIn(("E", "eol-crlf"), codes("# T\n\n## 1. A\n", newline="\r\n"))

    def test_third_list_level_is_error(self):
        self.assertIn(("E", "list-depth"), codes("- a\n  - b\n    - c\n"))

    def test_fence_in_list_is_warning(self):
        self.assertEqual(codes("- a\n\n  ```txt\n  x\n  ```\n"), [("W", "block-in-list")])

    def test_table_columns_and_br(self):
        self.assertEqual(codes("| a | b | c | d |\n| - | - | - | - |\n| 1 | 2 | 3 | 4 |\n"), [])
        found = codes("| a | b | c | d | e |\n| - | - | - | - | - |\n| 1<br>2 | 2 | 3 | 4 | 5 |\n")
        self.assertIn(("E", "table-columns"), found)
        self.assertIn(("E", "table-br"), found)

    def test_table_width_counts_widest_cell_and_wide_chars(self):
        tokens = check_md.parser().parse("| 中文 | ab |\n| - | - |\n| x | y |\n")
        self.assertEqual(check_md.table_width(tokens, 0), 14)
        ok = "| 维度 | 说明 |\n| - | - |\n| a | " + "字" * 50 + " |\n"
        self.assertEqual(codes(ok), [])
        wide = "| 维度 | 说明 |\n| - | - |\n| a | " + "字" * 60 + " |\n"
        self.assertIn(("E", "table-width"), codes(wide))

    def test_media_table_exempt_from_column_limit(self):
        imgs = {f"{n}.png": "" for n in "abc"}
        media = "| [![a](a.png)](a.png) | [![b](b.png)](b.png) | ![c](c.png) |\n| :-: | :-: | :-: |\n| A | B | C |\n"
        self.assertEqual(codes(media, imgs), [])
        mixed = "| ![a](a.png) | ![b](b.png) | t | u | v |\n| - | - | - | - | - |\n| A | B | C | D | E |\n"
        self.assertIn(("E", "table-columns"), codes(mixed, imgs))
        video = '| <video src="a.png"></video> | <video src="b.png"></video> | <video src="c.png"></video> |\n| - | - | - |\n| A | B | C |\n'
        self.assertEqual(codes(video, imgs), [])

    def test_heading_numbering(self):
        self.assertIn(("E", "heading-number"), codes("# T\n\n## 1. A\n\n## 3. B\n"))
        self.assertIn(("E", "heading-number"), codes("# 1. T\n"))
        self.assertIn(("E", "heading-number"), codes("## 1. A\n\n### 1.1.1 X\n"))
        self.assertEqual(codes("## Intro\n\n## Usage\n"), [])

    def test_links_anchors_and_local_paths(self):
        other = {"other.md": "# O\n\n## 3.4 精华 60 秒\n"}
        self.assertEqual(codes("[x](other.md#34-精华-60-秒)\n", other), [])
        self.assertIn(("W", "anchor"), codes("[x](other.md#nope)\n", other))
        self.assertIn(("E", "broken-link"), codes("[x](missing.md)\n"))
        self.assertIn(("E", "local-path"), codes("[x](C:/Users/a.md)\n"))
        self.assertIn(("E", "local-path"), codes("see C:\\Users\\me\\a.md\n"))
        self.assertEqual(codes("see `C:/Users/a.md` and [w](https://e.com)\n"), [])

    def test_controlled_rd_review_and_acceptance_fail_closed(self):
        def write_lf(path: Path, content: str) -> None:
            path.write_text(content, encoding="utf-8", newline="\n")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ("docs/plan", "docs/reviews", "docs/acceptance", "docs/archive/reviews", "docs/design"):
                (root / rel).mkdir(parents=True)
            plan_f = root / "docs/plan/2026.11.demo.plan.md"
            write_lf(plan_f, "# Demo Plan\n")

            # AGENTS.md and README.md inside docs/{reviews,acceptance}/ are excluded from report schema
            for special in ("docs/reviews/AGENTS.md", "docs/reviews/README.md", "docs/acceptance/AGENTS.md", "docs/acceptance/README.md"):
                p = root / special
                write_lf(p, "# Spec\n\n## 1. Scope\n\nOk.\n")
                self.assertIsNone(check_md.detect_doc_type(p))
                self.assertEqual(check_md.check_file(p), [])

            # Creative *.review.md outside docs/reviews/ keeps 'review' type without rd-review schema errors
            creative = root / "docs/design/shots.review.md"
            write_lf(creative, "# 分镜看片\n\n## 1. 第一轮\n\n待审。\n")
            self.assertEqual(check_md.detect_doc_type(creative), "review")
            self.assertEqual(check_md.check_file(creative), [])

            # Valid rd-review in docs/reviews/ and docs/archive/reviews/
            valid_rev_txt = (
                "# T1 评审报告\n\n"
                "负责人：Mengsk\n评审助手：Claude（架构）、Codex（细节）\n"
                "关联计划：[计划](../plan/2026.11.demo.plan.md)\n关联阶段：T1\n"
                "评审对象哈希：sha256:" + "a" * 64 + "\n评审结论：已通过\n更新时间：2026-11-15\n\n"
                "## 1. 当前依据\n\n- [计划](../plan/2026.11.demo.plan.md)\n\n"
                "## 2. 总体架构核验与符合性审计\n\n通过。\n\n"
                "## 3. 工程细节与防漏评审\n\n通过。\n\n"
                "## 4. 架构疑难升级裁定\n\n无。\n\n"
                "## 5. 问题闭环与放行结论\n\n未闭环 HIGH/MEDIUM：0\n"
            )
            rev_ok = root / "docs/reviews/review_20261115_demo_t2-r2.md"
            write_lf(rev_ok, valid_rev_txt)
            self.assertEqual(check_md.detect_doc_type(rev_ok), "rd-review")
            self.assertEqual(check_md.check_file(rev_ok), [])

            # Fail-closed on illegal filename or bad status/chapters inside docs/reviews/
            rev_bad = root / "docs/reviews/random_notes.md"
            write_lf(rev_bad, "# Notes\n\n## 1. Foo\n")
            bad_codes = {c for _, _, c, _ in check_md.check_file(rev_bad)}
            self.assertEqual(bad_codes, {"rd-review-name", "rd-review-meta", "rd-review-chapters", "rd-review-summary"})

            # Valid acceptance & fail-closed illegal acceptance
            valid_acc_txt = (
                "# 专项验收\n\n"
                "负责人：Mengsk\n执行助手：Gemini\n验真助手：待 T4 登记\n"
                "关联计划：[计划](../plan/2026.11.demo.plan.md)\n验收状态：考卷已锁定\n更新时间：2026-11-15\n\n"
                "## 1. 当前依据\n\n- [计划](../plan/2026.11.demo.plan.md)\n\n"
                "## 2. 锁定验收场景与判据\n\n### 2.1 S1：测试\n\n说明。\n\n"
                "## 3. 智能体实跑与演示证据\n\n待 T4 实跑回填。\n\n"
                "## 4. 防伪锚点与双轨验真\n\n待 T4 实跑回填。\n\n"
                "## 5. CP1 双抽检核验单\n\n待提交。\n"
            )
            acc_ok = root / "docs/acceptance/accept_20261115_demo.md"
            write_lf(acc_ok, valid_acc_txt)
            self.assertEqual(check_md.detect_doc_type(acc_ok), "acceptance")
            self.assertEqual(check_md.check_file(acc_ok), [])

            acc_bad = root / "docs/acceptance/bad_name.md"
            write_lf(acc_bad, valid_acc_txt.replace("验收状态：考卷已锁定", "验收状态：随便写"))
            acc_bad_codes = {c for _, _, c, _ in check_md.check_file(acc_bad)}
            self.assertEqual(acc_bad_codes, {"acceptance-name", "acceptance-meta"})


class DiscoverTest(unittest.TestCase):
    def test_explicit_missing_reports_reason(self):
        cmd, reason = md_format.discover(explicit="/no/such/prettier")
        self.assertIsNone(cmd)
        self.assertIn("not found", reason)

    def test_editor_extension_picks_highest_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            for root, version in ((".vscode", "3.7.4"), (".cursor", "3.3.0")):
                pkg = Path(tmp, root, "extensions", f"esbenp.prettier-vscode-{version}", "node_modules", "prettier")
                (pkg / "bin").mkdir(parents=True)
                (pkg / "bin" / "prettier.cjs").write_text("", encoding="utf-8")
                (pkg / "package.json").write_text(f'{{"version": "{version}"}}', encoding="utf-8")
            original = md_format.shutil.which
            md_format.shutil.which = lambda name: "node" if name == "node" else None
            try:
                cmd, source = md_format.discover(start=tmp, home=tmp)
            finally:
                md_format.shutil.which = original
            self.assertEqual(source, "editor extension")
            self.assertIn("3.7.4", cmd[1])


class InstallTest(unittest.TestCase):
    def test_install_idempotent_and_guarded_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "skills")
            with contextlib.redirect_stdout(io.StringIO()):
                target = install.install(root)
                self.assertTrue((target / "SKILL.md").is_file())
                self.assertFalse(list(target.rglob("__pycache__")))
                install.install(root)
                (target / "SKILL.md").write_text("changed", encoding="utf-8")
                with self.assertRaises(SystemExit):
                    install.install(root)
                install.install(root, replace=True)
            self.assertEqual(install.diff(Path(install.__file__).resolve().parents[1], target), [])
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["skills"])


if __name__ == "__main__":
    unittest.main()
