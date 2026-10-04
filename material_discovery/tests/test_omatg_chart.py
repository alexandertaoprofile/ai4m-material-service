"""Screening-chart views preserve all real statuses without new decisions."""
from __future__ import annotations

from copy import deepcopy
from unittest import TestCase

from src.material_workflow.omatg_chart import build_comparison_data, finite_number


def record(identifier, *, hull=0.0, state="passed", geometry=True,
           composition=True, converged=True, duplicate=None, formula="CdCu2SnTe4"):
    candidate = {"candidate_id": identifier, "formula_pretty": formula,
        "metadata": {"element_counts": {"Cd": 1, "Cu": 2, "Sn": 1, "Te": 4}}}
    validation = {"candidate_id": identifier, "is_valid": geometry, "energy_above_hull": hull,
        "metadata": {"composition_ok": composition, "mattersim": {"relaxation_converged": converged, "status": "ok" if converged else "not_converged"},
            "screening": {"state": state, "failures": ["凸包能超过上限"] if state == "failed" and isinstance(hull, (int, float)) and hull > 0.05 else []}, "duplicate_of": duplicate}}
    return {"candidate": candidate, "validation": validation}


def manifest(*records, mode="dng", allowed=(), threshold=0.05):
    return {"generation": {"candidates": [row["candidate"] for row in records], "metadata": {"mode": mode}},
        "constraints": {"allowed_elements": list(allowed), "target_properties": {"energy_above_hull": threshold}},
        "validations": [row["validation"] for row in records], "ranked_candidates": list(records)}


class OMatGChartDataTest(TestCase):
    def open_case(self):
        return manifest(record("004"), record("009", formula="TePb"),
            record("010", hull=0.0593, state="failed"), record("001", hull=0.15879, state="failed"),
            *[record(identifier, hull=None, converged=False, state="incomplete") for identifier in ("002", "003", "005", "006")],
            record("007", hull=None, state="incomplete"), record("008", hull=None, geometry=False, converged=False, state="failed"))

    def test_open_case_has_exact_real_funnel_and_exclusive_reasons(self):
        data = build_comparison_data(self.open_case())
        self.assertEqual([stage["count"] for stage in data["stages"]], [10, 10, 9, 5, 4, 2])
        self.assertEqual(data["outcomes"], {"passed": 2, "review": 0, "failed": 3, "incomplete": 5, "duplicate": 0})
        self.assertEqual({row["code"]: row["count"] for row in data["reasons"]}, {"geometry": 1, "hull": 2, "relaxation": 4, "hull_missing": 1})
        self.assertEqual(sum(row["count"] for row in data["reasons"]), 8)

    def test_input_results_are_not_mutated(self):
        source = self.open_case()
        old = deepcopy(source)
        build_comparison_data(source)
        self.assertEqual(source, old)

    def test_display_scheme_labels_follow_report_ranking_without_changing_raw_ids(self):
        source = manifest(record("dng-009"), record("dng-003"))
        source["generation"]["candidates"].reverse()
        rows = build_comparison_data(source)["rows"]
        self.assertEqual([row["candidate_id"] for row in rows], ["dng-009", "dng-003"])
        self.assertRegex(rows[0]["display_name"], r"^JC-[0-9A-F]{10}-01$")
        self.assertRegex(rows[1]["display_name"], r"^JC-[0-9A-F]{10}-02$")
        self.assertNotEqual(rows[0]["display_name"], rows[1]["display_name"])

    def test_no_passing_case_retains_actual_values(self):
        data = build_comparison_data(manifest(record("ternary", hull=0.09083, state="failed")))
        self.assertEqual(data["stages"][-1]["count"], 0)
        self.assertIn("暂未", data["title"])
        self.assertEqual(data["comparison_rows"][0]["hull"], 0.09083)
        self.assertIn("暂未获得可比较结构", data["stages"][-1]["caption"])
        self.assertNotIn("建议进一步验证", data["stages"][-1]["caption"])

    def test_above_reference_is_reviewed_not_failed_when_screening_passes(self):
        row = record("reviewed", hull=0.09083)
        row["validation"]["metadata"]["screening"]["stability_review"] = True
        data = build_comparison_data(manifest(row))
        self.assertEqual(data["rows"][0]["state"], "review")
        self.assertEqual(data["stages"][-1]["count"], 1)
        self.assertEqual(data["outcomes"]["review"], 1)
        self.assertNotIn("hull", {reason["code"] for reason in data["reasons"]})

    def test_low_hull_does_not_promote_other_failed_conditions(self):
        data = build_comparison_data(manifest(record("symmetry-failed", hull=0.0, state="failed")))
        self.assertEqual(data["outcomes"]["passed"], 0)
        self.assertEqual(data["rows"][0]["reason"], "conditions")

    def test_high_hull_does_not_hide_other_hard_failure(self):
        row = record("symmetry-failed", hull=0.09083, state="failed")
        row["validation"]["metadata"]["screening"]["failures"] = ["对称性条件未满足"]
        data = build_comparison_data(manifest(row))
        self.assertEqual(data["rows"][0]["reason"], "conditions")
        self.assertEqual(data["outcomes"]["failed"], 1)

    def test_low_hull_does_not_promote_incomplete_properties(self):
        data = build_comparison_data(manifest(record("band-gap-missing", state="incomplete")))
        self.assertEqual(data["outcomes"]["passed"], 0)
        self.assertEqual(data["rows"][0]["reason"], "other_missing")

    def test_missing_hull_remains_none_not_a_zero(self):
        data = build_comparison_data(manifest(record("missing", hull=None, state="incomplete")))
        self.assertIsNone(data["comparison_rows"][0]["hull"])
        self.assertEqual(data["rows"][0]["reason"], "hull_missing")

    def test_nonfinite_or_boolean_values_are_missing_not_zero(self):
        for value in (float("nan"), float("inf"), float("-inf"), True, "0", None):
            with self.subTest(value=value):
                self.assertIsNone(finite_number(value))
                data = build_comparison_data(manifest(record("missing", hull=value, state="incomplete")))
                self.assertIsNone(data["comparison_rows"][0]["hull"])
        self.assertEqual(finite_number(0.0), 0.0)

    def test_actual_negative_values_are_not_clipped_or_rewritten(self):
        data = build_comparison_data(manifest(record("negative", hull=-0.0123)))
        self.assertEqual(data["comparison_rows"][0]["hull"], -0.0123)

    def test_duplicate_passing_members_are_not_new_independent_candidates(self):
        data = build_comparison_data(manifest(record("representative"), record("copy", duplicate="representative")))
        self.assertEqual(data["outcomes"], {"passed": 1, "review": 0, "failed": 0, "incomplete": 0, "duplicate": 1})
        self.assertEqual(data["stages"][-1]["count"], 1)
        self.assertEqual(len(data["comparison_rows"]), 2)

    def test_duplicate_marker_does_not_hide_a_failure(self):
        data = build_comparison_data(manifest(record("failed-copy", geometry=False, state="failed", duplicate="other")))
        self.assertEqual(data["rows"][0]["reason"], "geometry")
        self.assertEqual(data["outcomes"]["failed"], 1)
        self.assertEqual(data["outcomes"]["duplicate"], 0)

    def test_composition_failure_precedes_missing_later_calculations(self):
        data = build_comparison_data(manifest(record("wrong-element", composition=False, geometry=False, converged=False, hull=None, state="failed")))
        self.assertEqual(data["rows"][0]["reason"], "composition")
        self.assertEqual([stage["count"] for stage in data["stages"]], [1, 0, 0, 0, 0, 0])

    def test_not_converged_hull_is_not_shown_as_a_completed_comparison(self):
        data = build_comparison_data(manifest(record("unconverged", converged=False, state="incomplete")))
        self.assertEqual(data["comparison_rows"], [])
        self.assertEqual(data["rows"][0]["reason"], "relaxation")

    def test_generation_count_does_not_depend_on_ranked_list_length(self):
        source = manifest(record("ranked"), record("pending", state="incomplete"))
        source["ranked_candidates"] = source["ranked_candidates"][:1]
        source["validations"] = source["validations"][:1]
        data = build_comparison_data(source)
        self.assertEqual(data["stages"][0]["count"], 2)
        self.assertEqual(data["outcomes"]["incomplete"], 1)

    def test_validation_join_uses_ids_not_list_positions(self):
        source = manifest(record("passed"), record("failed", hull=0.5, state="failed"))
        source["validations"].reverse()
        self.assertEqual([row["state"] for row in build_comparison_data(source)["rows"]], ["passed", "failed"])

    def test_scopes_explain_csp_restricted_and_open_dng(self):
        self.assertIn("固定化学式", build_comparison_data(manifest(mode="csp"))["scope"])
        self.assertIn("指定元素", build_comparison_data(manifest(allowed=["Fe", "Cr"]))["scope"])
        self.assertIn("开放元素", build_comparison_data(manifest())["scope"])

    def test_chart_scope_distinguishes_allowed_from_required_elements(self):
        source = manifest(allowed=["Fe", "Cr", "Ni"])
        source["constraints"].update(required_elements=["Fe"], min_distinct_elements=2)
        scope = build_comparison_data(source)["scope"]
        self.assertIn("Fe / Cr / Ni", scope)
        self.assertIn("必含 Fe；", scope)
        self.assertIn("至少 2 种元素", scope)
        self.assertNotIn("必含 Fe / Cr / Ni", scope)

    def test_chinese_identity_keeps_exact_saved_formula(self):
        row = record("formula", formula="TePb")
        row["candidate"]["metadata"]["element_counts"] = {"Pb": 4, "Te": 4}
        data = build_comparison_data(manifest(row))
        self.assertEqual(data["rows"][0]["name"], "铅-碲晶体")
        self.assertEqual(data["rows"][0]["formula"], "TePb")

    def test_empty_and_missing_threshold_do_not_invent_a_limit(self):
        data = build_comparison_data(manifest(threshold=None))
        self.assertIsNone(data["threshold"])
        self.assertEqual(data["comparison_rows"], [])
        self.assertEqual(sum(data["outcomes"].values()), 0)
