"""Verify a saved customer preview against unchanged scientific records."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.preview_saved_omatg import load_result
from src.material_workflow.omatg_chart import build_comparison_data, stability_number
from src.material_workflow.omatg_report import build_result_report, hull_limit, output_cell


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--preview-dir", type=Path, required=True)
    args = parser.parse_args()
    source = args.result_dir / "new_material_pipeline_manifest.json"
    original_bytes = source.read_bytes()
    result = load_result(source)
    original = deepcopy(result.to_dict())
    preview = (args.preview_dir / "frontend_preview.md").read_text(encoding="utf-8")
    import re
    visuals = {}
    for markdown in re.findall(r"!\[[^\]]+\]\([^\n]+\)", preview):
        path = re.search(r"\]\(([^\n]+)\)$", markdown).group(1)
        visuals[Path(path).stem] = markdown
    text = build_result_report(result, visuals) + "\n"
    assert text == preview
    assert result.to_dict() == original
    data = build_comparison_data(original)
    names = {row["candidate_id"]: row["display_name"] for row in data["rows"]}
    for item in result.ranked_candidates:
        label = names[item.candidate.candidate_id]
        line = next(line for line in text.splitlines() if line.startswith("| " + label + " |") or line.startswith("| " + label + "（优选结构） |") or line.startswith("| " + label + "（待复核结构） |"))
        assert item.candidate.candidate_id not in text
        if item.validation:
            assert output_cell(item.validation, "energy_above_hull", reference=hull_limit(result.constraints)) in line
    parent = None
    width = None
    for line in text.splitlines():
        if line.startswith("### "):
            parent = line[4]
        if line.startswith("#### "):
            assert line[5] == parent, line
        if line.startswith("|"):
            count = line.count("|")
            if width is None:
                width = count
            assert count == width, line
        else:
            width = None
        if "$$" in line:
            assert line == "$$"
    assert text.count("$$") % 2 == 0
    assert "随机采样种子" not in text and "seed=" not in text and "时间网格" not in text
    assert "待补充计算" not in text and "计算待补充" not in text
    if data["outcomes"]["passed"] == 0:
        assert "### 5. 优选晶体" not in text
    assert source.read_bytes() == original_bytes
    verification = {"status": "ok", "source_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "scientific_result_unchanged": True, "candidate_labels_match_chart": True,
        "table_columns_and_heading_numbers_valid": True, "uploads": False,
        "formula": result.constraints.target_formula,
        "generated": len(result.generation.candidates), "outcomes": data["outcomes"]}
    (args.preview_dir / "readability_verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(verification, ensure_ascii=False))


if __name__ == "__main__":
    main()
