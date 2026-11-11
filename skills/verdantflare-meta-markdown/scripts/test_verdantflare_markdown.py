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
