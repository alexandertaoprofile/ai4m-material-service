from __future__ import annotations

import unittest

from src.material_workflow.capability_fallback import needs_capability_fallback
from src.material_workflow.schemas import (
    GeneratedCandidate,
    GenerationConstraint,
    GenerationManifest,
    NewMaterialPipelineResult,
    RankedCandidate,
    ValidationResult,
)
from src.material_workflow.upstream_api import result_summary


class CapabilityFallbackTest(unittest.TestCase):
    def test_zero_candidate_result_explains_supported_and_unsupported_work(self) -> None:
        result = NewMaterialPipelineResult(
            taskid="no-candidate",
            status="unavailable",
            constraints=GenerationConstraint(
                taskid="no-candidate",
                allowed_elements=["Co", "Cr", "Fe", "Mn", "Ni"],
            ),
            generation=GenerationManifest(
                taskid="no-candidate",
                status="unavailable",
                message="checkpoint unavailable",
            ),
        )

        summary = result_summary(result)

        self.assertTrue(needs_capability_fallback(result))
        self.assertIn("支持的任务与输出", summary)
        self.assertIn("需要转交或另行验证的任务", summary)
        self.assertIn("MatterGen", summary)
        self.assertIn("Co-Cr-Fe-Mn-Ni", summary)
        self.assertIn("电子有效质量", summary)
        self.assertIn("严格化学计量比", summary)
        self.assertIn("真实合成路线", summary)
        self.assertIn("已有材料检索服务", summary)
        self.assertIn("材料配比优化服务", summary)
        self.assertNotIn("补齐对应 MatterGen 模型权重", summary)

    def test_all_rejected_candidates_also_receive_the_fallback(self) -> None:
        candidate = GeneratedCandidate(candidate_id="mg-001", formula_pretty="MnCr")
        validation = ValidationResult(
            candidate_id="mg-001",
            status="rejected",
            is_valid=False,
            errors=["interatomic_distance_below_covalent_threshold"],
        )
        result = NewMaterialPipelineResult(
            taskid="all-rejected",
            status="ok",
            constraints=GenerationConstraint(taskid="all-rejected", allowed_elements=["Mn", "Cr"]),
            generation=GenerationManifest(
                taskid="all-rejected", status="ok", candidates=[candidate],
            ),
            validations=[validation],
            ranked_candidates=[RankedCandidate(
                candidate=candidate, rank=1, score=0.0, validation=validation,
            )],
        )

        summary = result_summary(result)

        self.assertTrue(needs_capability_fallback(result))
        self.assertIn("通过基础结构检查的候选为 0 个", summary)
        self.assertIn("支持的任务与输出", summary)

    def test_admitted_candidate_does_not_append_the_fallback(self) -> None:
        candidate = GeneratedCandidate(candidate_id="mg-001", formula_pretty="LiPS")
        validation = ValidationResult(
            candidate_id="mg-001", status="ok", is_valid=True, formula_pretty="LiPS",
        )
        result = NewMaterialPipelineResult(
            taskid="admitted",
            status="ok",
            constraints=GenerationConstraint(taskid="admitted", allowed_elements=["Li", "P", "S"]),
            generation=GenerationManifest(
                taskid="admitted", status="ok", candidates=[candidate],
            ),
            validations=[validation],
            ranked_candidates=[RankedCandidate(
                candidate=candidate, rank=1, score=10.0, validation=validation,
            )],
        )

        summary = result_summary(result)

        self.assertFalse(needs_capability_fallback(result))
        self.assertNotIn("未形成可用候选", summary)


if __name__ == "__main__":
    unittest.main()
