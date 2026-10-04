"""Missing results have stage-specific causes, not guessed execution states."""
from copy import deepcopy
from pathlib import Path
import json
import os
import subprocess
import tempfile
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from tests import test_omatg_report_readability as readable
from src.material_workflow.omatg_output_status import candidate_issues, missing_output_issue, generation_issue
from src.material_workflow.omatg_report import build_result_report, output_cell
from src.material_workflow.omatg_chart import build_comparison_data
from src.material_workflow.mattersim import enrich_validations_with_mattersim
from src.material_workflow.alignn import enrich_omatg_with_alignn
from src.material_workflow.emitters import build_scientific_conclusion, build_frontend_payload


def sample(**kwargs):
    return readable.ReadableReportTest().result(**kwargs)


class OutputReasonTest(TestCase):
    def test_nonconvergence_uses_real_force_and_steps_without_claiming_iteration_limit(self):
        result = sample(state="incomplete", hull=None, predictions=False)
        v = result.validations[0]
        v.metadata["mattersim"].update(status="not_converged", relaxation_converged=False,
            max_generalized_force_ev_angstrom=.050301972, fmax_ev_angstrom=.05, relaxation_steps=47)
        text = build_result_report(result)
        self.assertIn("0.050302 eV/Å", text)
        self.assertIn("47 步优化", text)
        self.assertIn("—（结构优化未收敛）", text)
        self.assertIn("从已保存结构继续优化", text)
        self.assertNotIn("迭代上限", text)
        self.assertNotIn("待补充", text)
        self.assertEqual(len(candidate_issues(v, ("band_gap", "bulk_modulus"))), 1)
        self.assertEqual(build_comparison_data(result.to_dict())["rows"][0]["reason"], "relaxation")

    def test_geometry_failure_is_not_misdescribed_as_model_nonconvergence(self):
        result = sample(state="failed", hull=None, predictions=False)
        v = result.validations[0]
        v.is_valid = False
        v.errors = ["周期边界下 2 个近邻距离低于共价半径下限。"]
        v.metadata.pop("mattersim")
        text = build_result_report(result)
        self.assertIn("—（原子排布未通过）", text)
        self.assertIn(v.errors[0], text)
        self.assertIn("修正原子排布或重新生成", text)
        self.assertNotIn("结构优化未收敛", text)

    def test_relaxed_geometry_failure_names_the_correct_stage(self):
        result = sample(state="failed", hull=None)
        v = result.validations[0]
        v.is_valid = False
        v.metadata["mattersim"]["relaxed_geometry"] = {"is_valid": False, "errors": ["优化后原子过近"]}
        detail = candidate_issues(v)[0]
        self.assertEqual(detail["stage"], "优化后结构检查")
        self.assertIn("优化后原子过近", detail["cause"])

    def test_reference_phase_failure_preserves_completed_properties(self):
        result = sample(state="incomplete", hull=None)
        v = result.validations[0]
        v.metadata["mattersim"].update(status="unavailable", error="Unable to get decomposition for None ComputedStructureEntry - Ac6 Yb2")
        text = build_result_report(result)
        self.assertIn("—（参考相分解计算失败）", text)
        self.assertIn("重新计算竞争相凸包", text)
        self.assertIn("236.3900", text)
        self.assertNotIn("Ac6 Yb2", text)
        self.assertNotIn("结构优化未收敛", text)

    def test_unknown_thermodynamic_exception_is_honest_and_sanitized(self):
        result = sample(state="incomplete", hull=None)
        v = result.validations[0]
        v.metadata["mattersim"].update(status="unavailable", error="traceback /internal/private/path token=EXAMPLE_SECRET")
        text = build_result_report(result)
        self.assertIn("参考能量计算报错", text)
        self.assertIn("未提供可进一步归类的原因", text)
        self.assertNotIn("EXAMPLE_SECRET", text)
        self.assertNotIn("参考相缺失", text)

    def test_absent_record_does_not_assert_computation_was_not_started(self):
        result = sample(state="incomplete", hull=None, predictions=False)
        v = result.validations[0]
        v.metadata.pop("mattersim")
        text = build_result_report(result)
        self.assertIn("结构优化记录缺失", text)
        self.assertIn("确认是否执行", text)
        self.assertNotIn("结构优化未收敛", text)
        self.assertNotIn("未启动结构优化", text)

    def test_optimization_execution_errors_remain_distinct(self):
        for code, title in (("timeout", "结构优化超时"), ("unavailable", "结构优化环境无法启动"),
                            ("process_failed", "结构优化进程报错"), ("disabled", "本次未启用结构优化"),
                            ("invalid_results", "结构优化结果无法读取"), ("missing_results", "结构优化结果文件缺失")):
            with self.subTest(code=code):
                v = sample(state="incomplete", hull=None).validations[0]
                v.metadata["mattersim"] = {"status": code, "error_code": code}
                self.assertIn(title, output_cell(v, "energy_above_hull"))
                self.assertNotIn("未收敛", output_cell(v, "energy_above_hull"))

    def test_missing_property_is_distinct_from_hull_failure(self):
        result = sample(state="incomplete", hull=None, predictions=False)
        v = result.validations[0]
        v.metadata["mattersim"].update(status="unavailable", error="Unable to get decomposition")
        self.assertIn("参考相分解计算失败", output_cell(v, "energy_above_hull"))
        self.assertIn("性质结果未记录", output_cell(v, "bulk_modulus"))

    def test_property_codes_are_explained_individually_and_completed_zero_is_preserved(self):
        cases = {"checkpoint_missing": "预测权重缺失", "graph_failed": "结构特征构建失败", "model_load_failed": "预测模型加载失败",
                 "nonfinite": "预测值无效", "timeout": "预测超时", "process_failed": "预测进程报错", "disabled": "本次未启用性质预测"}
        for code, title in cases.items():
            with self.subTest(code=code):
                v = sample().validations[0]
                v.property_predictions.pop("bulk_modulus")
                v.property_predictions["band_gap"]["value"] = 0.
                v.metadata["alignn_property_screening"] = {"property_status": {"bulk_modulus": {"code": code}}}
                self.assertIn(title, output_cell(v, "bulk_modulus"))
                self.assertEqual(output_cell(v, "band_gap"), "0.0000")

    def test_nonfinite_property_never_looks_like_a_genuine_zero(self):
        for value in (float("nan"), float("inf"), True, "0"):
            v = sample().validations[0]
            v.property_predictions["bulk_modulus"]["value"] = value
            self.assertIn("预测值无效", output_cell(v, "bulk_modulus"))

    def test_specific_old_alignn_error_can_explain_property_failure(self):
        v = sample(predictions=False).validations[0]
        v.metadata["alignn_errors"] = ["bulk_modulus: Nonfinite ALIGNN prediction"]
        self.assertIn("预测值无效", output_cell(v, "bulk_modulus"))
        self.assertIn("性质结果未记录", output_cell(v, "band_gap"))

    def test_not_requested_property_and_duplicate_have_distinct_next_steps(self):
        v = sample(predictions=False).validations[0]
        v.metadata["alignn_property_screening"] = {"status": "completed", "requested_properties": ["band_gap"]}
        self.assertIn("本次未安排该性质", output_cell(v, "bulk_modulus"))
        v.metadata.pop("alignn_property_screening")
        v.metadata["duplicate_of"] = "other"
        self.assertIn("相似结构未单独预测", output_cell(v, "bulk_modulus"))
        self.assertIn("无法由代表结构", missing_output_issue(v, "bulk_modulus")["effect"])

    def test_generation_queue_timeout_and_sampling_timeout_are_not_conflated(self):
        self.assertEqual(generation_issue({"status": "timeout", "message": "队列等待超过时限"})["stage"], "排队")
        self.assertEqual(generation_issue({"status": "timeout", "message": "采样超时"})["stage"], "结构生成")

    def test_empty_generation_report_explains_dependencies_and_retry(self):
        result = sample()
        result.generation.candidates.clear()
        result.validations.clear()
        result.ranked_candidates.clear()
        result.generation.status = "timeout"
        result.generation.message = "计算队列等待超过时限"
        text = build_result_report(result)
        for content in ("计算队列等待超时", "结构生成未启动", "后续结构与性质评价均未启动", "按原条件重试"):
            self.assertIn(content, text)
        self.assertNotIn("待补充", text)

    def test_opt_in_csp_skipped_is_explained_and_default_dng_is_not_distracted(self):
        result = sample(state="incomplete", hull=None)
        self.assertNotIn("追加 CSP 的执行情况", build_result_report(result))
        result.constraints.refine_with_csp = True
        self.assertIn("因此未启动追加 CSP", build_result_report(result))

    def test_missing_symmetry_has_analysis_action(self):
        v = sample().validations[0]
        v.metadata["screening"]["missing"] = ["non_centrosymmetric"]
        detail = candidate_issues(v)[0]
        self.assertEqual(detail["stage"], "结构对称性分析")
        self.assertIn("重新做空间群", detail["action"])

    def test_missing_visual_and_delivery_failures_are_explained_without_claiming_no_calculation(self):
        result = sample()
        result.artifacts["presentation"] = {"status": "unavailable", "message": "Presentation rendering timed out.",
            "delivery_status": [{"name": "旋转预览", "code": "upload_rejected"}]}
        text = build_result_report(result)
        self.assertIn("可视化渲染超过本次时限", text)
        self.assertIn("旋转预览：资产上传未成功", text)
        self.assertIn("236.3900", text)

    def test_status_rendering_does_not_mutate_any_scientific_record(self):
        result = sample(state="incomplete", hull=None, duplicate=True)
        before = deepcopy(result.to_dict())
        build_result_report(result)
        build_comparison_data(result.to_dict())
        self.assertEqual(result.to_dict(), before)

    def test_http_conclusion_explains_real_stop_instead_of_listing_missing_keys(self):
        result = sample(state="incomplete", hull=None, predictions=False)
        result.validations[0].metadata["mattersim"].update(status="not_converged", relaxation_converged=False,
            max_generalized_force_ev_angstrom=.066059, fmax_ev_angstrom=.05)
        text = build_scientific_conclusion(result)["text"]
        self.assertIn("0.066059 eV/Å", text)
        self.assertIn("从已保存结构继续优化", text)
        self.assertNotIn("松弛收敛、凸包能估算", text)

    def test_http_hull_error_does_not_invent_a_second_missing_structure_field(self):
        result = sample(state="incomplete", hull=None)
        result.validations[0].metadata["mattersim"].update(status="unavailable", error="Unable to get decomposition")
        text = build_scientific_conclusion(result)["text"]
        self.assertIn("凸包分解计算失败", text)
        self.assertNotIn("结构数据未记录", text)

    def test_stale_completion_message_cannot_override_real_nonconvergence(self):
        result = sample(state="incomplete", hull=None)
        result.validations[0].metadata["mattersim"].update(status="not_converged", relaxation_converged=False)
        result.message = "DNG 组成与晶体结构生成、松弛、稳定性和性质初筛已完成。"
        before = deepcopy(result.to_dict())
        self.assertNotIn(result.message, build_result_report(result))
        self.assertIn("0 个结构优化收敛", build_frontend_payload(result)["message"])
        self.assertEqual(result.to_dict(), before)

    def test_hard_property_failure_names_actual_value_and_confirmed_bounds(self):
        result = sample(state="failed")
        result.constraints.property_constraints = {"bulk_modulus": {"min": 300, "unit": "GPa"}}
        result.validations[0].metadata["screening"]["failures"] = ["性质范围未满足：bulk_modulus"]
        text = build_result_report(result)
        self.assertIn("体积模量预测 236.39 GPa，要求 下限 300 GPa", text)
        self.assertIn("按原条件追加候选探索", text)


class ExecutionStatusCaptureTest(TestCase):
    def test_mattersim_process_failure_timeout_and_missing_file_are_preserved(self):
        cases = [(subprocess.TimeoutExpired("worker", 5), "timeout"), (OSError("worker missing"), "unavailable"),
                 (SimpleNamespace(returncode=1, stdout="", stderr="failure"), "process_failed"),
                 (SimpleNamespace(returncode=0, stdout="", stderr=""), "missing_results")]
        for response, code in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                result = sample()
                c, v = result.generation.candidates[0], result.validations[0]
                c.cif_path = root / "candidate.cif"
                mock = {"side_effect": response} if isinstance(response, Exception) else {"return_value": response}
                with patch.dict(os.environ, {"NEW_MATERIAL_GENERATOR": "omatg", "MATTERSIM_ENABLED": "1"}), patch("src.material_workflow.mattersim._run", **mock):
                    enrich_validations_with_mattersim([v], [c], root / "validation")
                self.assertEqual(v.metadata["mattersim"]["error_code"], code)
                self.assertIsNone(v.energy_above_hull)
                self.assertTrue(v.is_valid)

    def test_alignn_execution_failure_timeout_missing_file_and_invalid_json_are_preserved(self):
        cases = [(subprocess.TimeoutExpired("worker", 5), "timeout"), (OSError("worker missing"), "unavailable"),
                 (SimpleNamespace(returncode=1, stdout="", stderr="failure"), "process_failed"),
                 (SimpleNamespace(returncode=0, stdout="", stderr=""), "missing_results")]
        for response, code in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                result = sample(predictions=False)
                c, v = result.generation.candidates[0], result.validations[0]
                c.cif_path = root / "candidate.cif"
                mock = {"side_effect": response} if isinstance(response, Exception) else {"return_value": response}
                with patch("src.material_workflow.alignn.subprocess.run", **mock):
                    enrich_omatg_with_alignn([v], [c], root / "validation", {}, {})
                self.assertEqual(v.metadata["alignn_property_screening"]["status"], code)
                self.assertTrue(v.is_valid)

    def test_property_head_failure_and_parallel_success_are_captured(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = sample(predictions=False)
            c, v = result.generation.candidates[0], result.validations[0]
            c.cif_path = root / "candidate.cif"
            def worker(*args, **kwargs):
                (root / "alignn" / "predictions.json").write_text(json.dumps({c.candidate_id: {"predictions": {"band_gap": {"value": 0.}},
                    "errors": [], "property_status": {"bulk_modulus": {"code": "checkpoint_missing"}}}}))
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch("src.material_workflow.alignn.subprocess.run", side_effect=worker):
                enrich_omatg_with_alignn([v], [c], root / "validation", {}, {})
            self.assertIn("预测权重缺失", output_cell(v, "bulk_modulus"))
            self.assertEqual(output_cell(v, "band_gap"), "0.0000")

    def test_invalid_alignn_file_is_not_silently_treated_as_a_missing_prediction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = sample(predictions=False)
            c, v = result.generation.candidates[0], result.validations[0]
            c.cif_path = root / "candidate.cif"
            def worker(*args, **kwargs):
                (root / "alignn" / "predictions.json").write_text("not-json")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch("src.material_workflow.alignn.subprocess.run", side_effect=worker):
                enrich_omatg_with_alignn([v], [c], root / "validation", {}, {})
            self.assertEqual(v.metadata["alignn_property_screening"]["status"], "invalid_results")

    def test_invalid_mattersim_file_is_not_a_nonconvergence_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = sample()
            c, v = result.generation.candidates[0], result.validations[0]
            c.cif_path = root / "candidate.cif"
            def worker(*args, **kwargs):
                (root / "mattersim" / "mattersim_results.json").write_text("not-json")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch.dict(os.environ, {"NEW_MATERIAL_GENERATOR": "omatg", "MATTERSIM_ENABLED": "1"}), patch("src.material_workflow.mattersim._run", side_effect=worker):
                enrich_validations_with_mattersim([v], [c], root / "validation")
            self.assertEqual(v.metadata["mattersim"]["error_code"], "invalid_results")

    def test_asset_upload_failure_keeps_a_customer_reason_and_no_broken_url(self):
        import asyncio
        from src.material_workflow import presentation
        with tempfile.TemporaryDirectory() as temporary:
            result = sample()
            image = Path(temporary) / "preview.png"
            image.write_bytes(b"test-placeholder")
            result.artifacts["presentation"] = {"assets": [{"name": "比较图", "path": str(image), "type": "MaterialsPNG"}]}
            class Socket:
                async def send_json(self, event):
                    raise AssertionError("Failed asset must not be emitted")
            async def upload(*args):
                return {"status": 503}
            with patch.object(presentation, "oss_upload", upload):
                markdown = asyncio.run(presentation.emit_presentation_assets(Socket(), result))
            self.assertEqual(markdown, "")
            self.assertEqual(result.artifacts["presentation"]["delivery_status"][0]["code"], "upload_rejected")
            self.assertIn("比较图：资产上传未成功", build_result_report(result))
