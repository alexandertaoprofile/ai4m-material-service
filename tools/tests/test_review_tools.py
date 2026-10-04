from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


def load_tool(name):
    path = Path(__file__).resolve().parents[1] / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


comparison = load_tool("compare_service_versions")
documentation = load_tool("verify_documentation")


class ReviewToolTest(unittest.TestCase):
    def test_comparison_preserves_meaningful_differences(self):
        result = comparison.compare_records(
            {"same.py": b"a\r\n", "change.py": b"x \n", "root.json": b"{}"},
            {"same.py": b"a\n", "change.py": b"x\n", "new.py": b"pass\n"})
        self.assertEqual(result["identical"], ["same.py"])
        self.assertEqual(result["changed"], ["change.py"])
        self.assertEqual(result["added_in_development"], ["new.py"])
        self.assertEqual(result["only_in_root"], ["root.json"])

    def test_missing_files_do_not_hide_as_identical(self):
        result = comparison.compare_records({}, {"new.py": b"pass"})
        self.assertEqual(result["root_files"], 0)
        self.assertEqual(result["development_files"], 1)
        self.assertEqual(result["added_in_development"], ["new.py"])

    def test_document_links_and_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            counts, errors = documentation.check_text("README.md", (
                '# Title\n\n[missing](missing.md)\n\n```json\n{"x":}\n```\n'), root, False)
        self.assertEqual(counts["links"], 1)
        self.assertEqual(counts["json_examples"], 1)
        self.assertTrue(any("broken local link" in error for error in errors))
        self.assertTrue(any("invalid JSON" in error for error in errors))

    def test_code_examples_are_not_treated_as_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            counts, errors = documentation.check_text("README.md", (
                '# Title\n\n```text\n[example](not-a-link.py)\n```\n'), Path(temporary), False)
        self.assertEqual(counts["links"], 0)
        self.assertEqual(errors, [])

    def test_detects_unclosed_fence_and_heading_spacing(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, errors = documentation.check_text("README.md", '# Title\ntext\n```text\n',
                                                  Path(temporary), False)
        self.assertTrue(any("blank line" in error for error in errors))
        self.assertTrue(any("unbalanced" in error for error in errors))

    def test_detects_inconsistent_table(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, errors = documentation.check_text("README.md", '| a | b |\n| c |\n',
                                                  Path(temporary), False)
        self.assertTrue(any("table columns" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
