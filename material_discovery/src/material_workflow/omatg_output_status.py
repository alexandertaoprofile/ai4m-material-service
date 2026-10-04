"""Explain missing output from recorded evidence, never infer execution from None."""
from __future__ import annotations

import math


LABELS = {"band_gap": "带隙", "bulk_modulus": "体积模量", "shear_modulus": "剪切模量",
          "electron_effective_mass": "电子有效质量", "hole_effective_mass": "空穴有效质量",
          "dielectric_constant_x": "x 方向介电常数", "non_centrosymmetric": "非中心对称",
          "space_group_number": "空间群编号"}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def as_record(validation):
    return validation.to_dict() if hasattr(validation, "to_dict") else validation or {}


def readable_reason(text):
    for key, label in LABELS.items():
        text = str(text).replace(key, label)
    return str(text).replace("凸包能超过上限", "稳定性差值超过上限").replace("凸包能估算", "稳定性估计").replace("松弛收敛", "结构优化收敛")


def issue(code, title, stage, cause, effect, action):
    return dict(code=code, title=title, stage=stage, cause=cause, effect=effect, action=action)


def prerequisite_issue(validation):
    v = as_record(validation)
    if not v:
        return issue("record", "评价记录缺失", "结构评价", "已生成候选，但保存记录中没有该候选的评价结果或执行状态。",
                     "稳定性与性能均无法据此判断。", "核对该任务的评价日志和结果文件，确认停止阶段后恢复对应计算。")
    meta = v.get("metadata") or {}
    thermo = meta.get("mattersim") or {}
    if meta.get("composition_ok") is False:
        reasons = v.get("errors") or ["组成未满足允许、必含、排除元素或固定化学计量要求。"]
        return issue("composition", "元素组成不符合要求", "组成检查", "；".join(map(readable_reason, reasons)),
                     "该候选的后续能量和性能评价不用于本次推荐。", "按原有组成约束重新生成候选。")
    if v.get("is_valid") is False:
        relaxed_invalid = (thermo.get("relaxed_geometry") or {}).get("is_valid") is False
        reasons = v.get("errors") or (thermo.get("relaxed_geometry") or {}).get("errors") or ["原子排布检查未通过；记录未提供具体检查项。"]
        return issue("geometry", "优化后原子排布未通过" if relaxed_invalid else "原子排布未通过", "优化后结构检查" if relaxed_invalid else "原子排布检查",
                     "；".join(map(readable_reason, reasons)), "该结构未进入后续稳定性与性能比较。",
                     "核对周期近邻距离和晶胞，修正原子排布或重新生成候选。")
    if v.get("is_valid") is not True:
        return issue("record", "结构检查记录缺失", "原子排布检查", "本次记录没有明确的原子排布检查结论。",
                     "结构优化及性能评价的前置条件尚无法确认。", "核对检查日志，并对该候选补做周期结构检查。")
    if thermo.get("relaxation_converged") is True:
        return None
    status = thermo.get("status")
    if status == "not_converged" or thermo.get("relaxation_converged") is False and _finite(thermo.get("max_generalized_force_ev_angstrom")):
        force, threshold, steps = (thermo.get(key) for key in ("max_generalized_force_ev_angstrom", "fmax_ev_angstrom", "relaxation_steps"))
        cause = "本次结构优化已结束，保存的收敛结论为未收敛。"
        if _finite(force) and _finite(threshold):
            cause += f"最终力指标 {force:.6f} eV/Å，收敛阈值 {threshold:g} eV/Å。"
        if _finite(steps):
            cause += f"记录了 {int(steps)} 步优化。"
        return issue("relaxation", "结构优化未收敛", "结构优化", cause,
                     "本次稳定性估算和性质预测缺少已收敛的结构输入。", "从已保存结构继续优化，核对原子力与晶胞应力；收敛后再计算稳定性和性质。")
    code = thermo.get("error_code") or status
    if code in {"timeout", "unavailable", "process_failed", "invalid_results", "missing_results", "disabled", "missing_structure"}:
        titles = {"timeout": "结构优化超时", "unavailable": "结构优化环境无法启动", "process_failed": "结构优化进程报错",
                  "invalid_results": "结构优化结果无法读取", "missing_results": "结构优化结果文件缺失",
                  "disabled": "本次未启用结构优化", "missing_structure": "优化所需结构文件缺失"}
        return issue("relaxation_error", titles[code], "结构优化", titles[code] + "，未形成可核对的收敛结果。",
                     "后续稳定性和性质评价缺少结构输入。", "核对该阶段的运行环境、日志和结构文件，恢复结构优化后继续评价。")
    return issue("record", "结构优化记录缺失", "结构优化", "候选已通过结构检查，但记录没有优化完成结论，也没有可识别的停止原因。",
                 "当前无法确认稳定性与性质评价是否具备收敛结构输入。", "核对该任务的优化日志及结果文件，确认是否执行、停止原因和可恢复的结构。")


def missing_output_issue(validation, key):
    """Explain only this missing quantity; preserve completed parallel properties."""
    v = as_record(validation)
    pre = prerequisite_issue(v)
    if pre:
        return pre
    meta = v.get("metadata") or {}
    thermo = meta.get("mattersim") or {}
    if key in {"energy_above_hull", "formation_energy_per_atom"}:
        error = str(thermo.get("error") or "")
        if "decomposition" in error.lower():
            return issue("hull_missing", "参考相分解计算失败", "稳定性估算", "结构优化已收敛，但同组成参考相的凸包分解计算失败。",
                         "稳定性差值未形成；已完成的性质预测仍保留。", "核对该元素体系的参考相和能量校正，重新计算竞争相凸包。")
        if error or thermo.get("status") in {"failed", "unavailable", "error"}:
            return issue("hull_missing", "参考能量计算报错", "参考能量与稳定性估算", "结构优化已收敛，后续参考能量计算发生异常；记录未提供可进一步归类的原因。",
                         "对应的形成能或稳定性值未形成；已完成的性质预测仍保留。", "核对参考相、能量校正及该阶段日志，再恢复对应计算。")
        return issue("hull_missing", "稳定性结果未记录", "参考能量与稳定性估算", "结构优化已收敛，但记录缺少该项数值及可识别的计算状态。",
                     "稳定性仍无法判断；已完成的性质预测仍保留。", "核对参考相计算日志与结果文件，恢复或补做该项计算。")
    if key in LABELS:
        label = LABELS[key]
        prediction = (v.get("property_predictions") or {}).get(key) or {}
        stage = meta.get("alignn_property_screening") or {}
        per_property = (stage.get("property_status") or {}).get(key) or {}
        code = per_property.get("code") or stage.get("status")
        errors = [str(e) for e in meta.get("alignn_errors") or [] if key in str(e)]
        if "value" in prediction and not _finite(prediction["value"]):
            code = "nonfinite"
        if code is None and errors:
            code = "nonfinite" if any("nonfinite" in e.lower() for e in errors) else "failed"
        descriptions = {"timeout": ("预测超时", "性质预测进程超过本次时限。"), "unavailable": ("预测环境无法启动", "性质预测环境无法启动。"),
                        "checkpoint_missing": ("预测权重缺失", "该性质的本地预训练权重缺失。"), "model_load_failed": ("预测模型加载失败", "该性质的预训练模型加载失败。"),
                        "graph_failed": ("结构特征构建失败", "优化结构的原子图或键角特征构建失败。"), "nonfinite": ("预测值无效", "模型返回非有限数值，无法作为性能结果。"),
                        "failed": ("性质预测报错", "该项性质预测发生异常；已完成的其他性质仍保留。"), "process_failed": ("预测进程报错", "性质预测进程异常退出。"),
                        "missing_results": ("预测结果文件缺失", "性质预测进程结束，但对应结果文件未形成。"), "invalid_results": ("预测结果无法读取", "对应性质的结果文件无法解析。"),
                        "disabled": ("本次未启用性质预测", "本次任务关闭了性质预测阶段。"), "not_requested": ("本次未安排该性质", "本次性质预测范围未包含该项。"),
                        "duplicate": ("已合并至代表结构", "该候选已合并到相似结构组，本次只评价代表结构。")}
        if code in descriptions:
            title, cause = descriptions[code]
            action = "核对该性质模型的环境、权重及预测日志，恢复后对已保存结构补算。"
            if code in {"disabled", "not_requested"}:
                action = "若需比较该项性能，可对已保存的收敛结构追加对应性质预测。"
            if code == "duplicate":
                action = "查看代表结构的性质结果；如需比较细微结构差异，可单独追加预测。"
            return issue("property", title, label + "预测", cause, label + "没有可用于比较的数值。", action)
        if meta.get("duplicate_of") and not stage and not errors:
            return issue("property", "相似结构未单独预测", label + "预测", "该候选记录了相似结构合并关系，记录未包含本候选的独立性质预测。",
                         label + "无法由代表结构的结果直接代填。", "查看代表结构结果；需要独立比较时对本候选追加预测。")
        if stage.get("requested_properties") is not None and key not in stage["requested_properties"]:
            return issue("property", "本次未安排该性质", label + "预测", "保存的性质预测范围未包含该项。",
                         label + "没有对应结果。", "需要比较该项时，可对已保存的收敛结构追加对应性质预测。")
        return issue("property", "性质结果未记录", label + "预测", "本次记录缺少该性质数值，也没有可识别的执行或异常原因。",
                     label + "尚无法比较，已完成的其他性质仍保留。", "核对该项预测日志与结果文件，确认原因后补算对应性质。")
    return issue("record", "结构数据未记录", "结构信息整理", "保存记录中缺少该项结构数据。", "该项信息无法展示。", "核对对应 CIF 文件并重新提取结构信息。")


def candidate_issues(validation, property_keys=()):
    pre = prerequisite_issue(validation)
    if pre:
        return [pre]
    v = as_record(validation)
    issues = []
    if not _finite(v.get("energy_above_hull")):
        issues.append(missing_output_issue(v, "energy_above_hull"))
    for key in property_keys:
        if key not in LABELS or key in {"non_centrosymmetric", "space_group_number"}:
            continue
        if not _finite(((v.get("property_predictions") or {}).get(key) or {}).get("value")):
            issues.append(missing_output_issue(v, key))
    for key in (v.get("metadata") or {}).get("screening", {}).get("missing", []):
        if key in {"non_centrosymmetric", "space_group_number"}:
            issues.append(issue("symmetry", "对称性分析结果未记录", "结构对称性分析",
                                LABELS[key] + "要求缺少对应分析结果。", "该项结构要求尚无法判断。",
                                "对优化后的 CIF 重新做空间群和对称性分析，再核对已确认的结构要求。"))
    if not issues and (v.get("metadata") or {}).get("screening", {}).get("state") not in {"passed", "failed", "incomplete"}:
        issues.append(issue("record", "条件核对结论未记录", "条件核对", "已保存结构或性能结果，但记录缺少本次条件核对的结论。",
                            "当前无法确认是否满足全部已设要求。", "按保存的组成、性能与对称性约束逐项核对，并恢复对应评价记录。"))
    return issues


def generation_issue(generation, fallback_message=""):
    data = generation.to_dict() if hasattr(generation, "to_dict") else generation or {}
    status = data.get("status")
    message = str(data.get("message") or fallback_message or "")
    if "没有合规起点" in message:
        return issue("generation", "没有合规生成起点", "组成与生成条件核对", "本次记录说明没有合规起点，未形成可供后续评价的完整候选。",
                     "后续稳定性与性质评价缺少合规结构输入。", "核对已确认的组成与生成条件；恢复对应评价或追加候选探索。")
    if status == "timeout" and "队列" in message:
        return issue("generation", "计算队列等待超时", "排队", "本次等待计算资源超过时限，结构生成未启动。", "后续结构与性质评价均未启动。", "检查计算队列负载，资源可用后按原条件重试。")
    if status == "timeout":
        return issue("generation", "结构生成超时", "结构生成", "OMatG 未在本次时限内产出完整候选记录。", "后续结构与性质评价没有完整候选输入。", "检查运行资源，可减少候选数或调整任务时限后重试。")
    if status == "unavailable":
        return issue("generation", "生成环境或权重不可用", "结构生成", "OMatG 的运行环境或本地模型权重未就绪。", "本次没有可供后续评价的候选结构。", "检查生成模型的环境、权重和启动日志，恢复后按原条件重试。")
    return issue("generation", "生成未形成可读取候选", "结构生成", "生成结果中没有可读取的候选结构；具体进程原因需核对任务日志。", "后续稳定性及性能评价缺少结构输入。", "核对采样日志和候选文件，修复结果读取或重新生成候选。")
