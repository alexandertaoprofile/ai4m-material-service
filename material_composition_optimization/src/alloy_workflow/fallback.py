"""Safe, customer-visible fallback for requests outside trained expert domains."""
from __future__ import annotations

import re
from typing import Any

from src.alloy_workflow.contracts import (
    MaterialContextError,
    is_composite_material_request,
    task_id,
    upstream_requirement,
)
from src.alloy_workflow.identity import public_capability_cards


_PLASTIC_PATTERN = re.compile(
    r"塑料|塑胶|热塑|聚合物|polymer|plastic|thermoplastic|"
    r"(?<![A-Za-z])(?:PP|PE|HDPE|LDPE|PVC|PS|ABS|ASA|PA6|PA66|PC|POM|PET|PBT|PLA|PEEK|PEKK|PPS)(?![A-Za-z])",
    re.IGNORECASE,
)
_EXPLICIT_REINFORCED_COMPOSITE_PATTERN = re.compile(
    r"复合材料|复材|连续纤维|碳纤维|玻璃纤维|玻纤|增强相|CFRP|GFRP|composite|fiber",
    re.IGNORECASE,
)


def _blueprint(text: str) -> dict[str, Any]:
    """Build a material-family-specific experiment scaffold, never a fake predictor."""
    lowered = text.casefold()
    if _PLASTIC_PATTERN.search(text) and not _EXPLICIT_REINFORCED_COMPOSITE_PATTERN.search(text):
        return {
            "material_family": "热塑性塑料/聚合物配方",
            "composition_basis": "wt.%；基础树脂作为平衡组分，所有组分合计 100 wt.%",
            "formulation_components": [
                {"role": "基础树脂", "symbol": "w_r", "range": "60–100 wt.%（余量）", "purpose": "先明确树脂种类、完整牌号、熔体流动特征与批次"},
                {"role": "共混树脂/增韧剂", "symbol": "w_b", "range": "0–25 wt.%", "purpose": "只围绕相容性、冲击或耐热目标设置局部梯度"},
                {"role": "功能填料", "symbol": "w_f", "range": "0–30 wt.%", "purpose": "按增强、导热、阻燃、阻隔等单一主目标选择粒径与表面处理"},
                {"role": "增塑/加工助剂", "symbol": "w_p", "range": "0–15 wt.%", "purpose": "记录流变、脱模或柔韧作用；不得与稳定剂包合并记账"},
                {"role": "稳定剂/相容剂包", "symbol": "w_s", "range": "0.1–5 wt.%", "purpose": "分别记录抗氧、光稳、热稳、相容或偶联组分的实际加入量"},
            ],
            "process_variables": [
                {"variable": "原料预处理", "levels": "树脂与填料干燥温度/时间、含湿量、回料比例", "purpose": "控制水解、气泡和批次波动"},
                {"variable": "混炼过程", "levels": "加料顺序、螺杆组合/转速、各区温度、扭矩与停留时间", "purpose": "区分配方效应与分散/热降解效应"},
                {"variable": "成形与冷却", "levels": "注塑/挤出/压制温度、压力、模温、冷却速度和试样方向", "purpose": "固定结晶度、取向和残余应力"},
            ],
            "minimum_record_fields": ["基础树脂和全部助剂完整牌号", "各组分实际 wt.%", "原料含湿量与干燥制度", "混炼加料顺序和温度/扭矩", "成形及冷却条件", "试样方向与调湿状态", "测试标准、温度和原始数据"],
            "first_round_strategy": "先确定一种基础树脂牌号和一个主性能目标，固定干燥、混炼与成形条件；设置纯树脂基线，并只对一个增韧剂或功能填料做低—中—高三级梯度。首轮确认相容性、分散和加工窗口后，再调整稳定剂包或开展双变量设计。",
            "next_actions": ["确认基础树脂种类、牌号和是否允许共混", "确认主性能指标、测试标准与使用温度", "确认填料/助剂禁限、加工方式和阻燃/环保要求"],
        }
    if is_composite_material_request(text, {}):
        return {
            "material_family": "复合材料/多相体系",
            "composition_basis": "wt.%；若采用体积分数须同时记录各相密度和换算方法",
            "formulation_components": [
                {"role": "连续相或基体", "symbol": "w_m", "range": "65–90 wt.%", "purpose": "固定树脂/金属/陶瓷基体牌号与批次"},
                {"role": "增强相或功能填料", "symbol": "w_r", "range": "5–30 wt.%", "purpose": "围绕强度、导热、导电或阻隔目标设置低—中—高梯度"},
                {"role": "界面/相容组分", "symbol": "w_i", "range": "0–5 wt.%", "purpose": "只有在界面处理或润湿需求明确时纳入"},
                {"role": "加工助剂", "symbol": "w_a", "range": "0–5 wt.%", "purpose": "记录流变、分散和稳定作用，不与基体牌号混写"},
            ],
            "process_variables": [
                {"variable": "增强体形态与界面", "levels": "粒径/长径比、表面处理、含湿状态", "purpose": "避免把不同界面状态当成同一配方"},
                {"variable": "成形与固化", "levels": "混合顺序、温度、压力/剪切、固化或冷却历史", "purpose": "建立孔隙、取向和界面质量的来源记录"},
            ],
            "minimum_record_fields": ["基体与增强体完整牌号", "质量/体积分数及换算", "几何形态与取向", "界面处理", "成形/固化参数", "试样方向与测试条件", "目标性质与原始数据"],
            "first_round_strategy": "固定基体、增强体来源和工艺，仅对增强体含量做 1 个基线加低—中—高三个梯度；确认单因素趋势后再引入界面剂或第二填料。",
            "next_actions": ["确认基体、增强相及界面处理的完整身份", "选择一个主变量建立基线+三级梯度", "同步记录孔隙率、取向和同批测试条件"],
        }
    if any(token in lowered for token in ("钛合金", "钛基", "titanium", "ti-6al-4v", "tc4", "ta15")):
        return {
            "material_family": "钛合金探索体系",
            "composition_basis": "wt.%；Ti 作为平衡元素",
            "formulation_components": [
                {"role": "Ti 基体", "symbol": "c_Ti", "range": "余量", "purpose": "先锁定 α、α+β 或 β 合金路线"},
                {"role": "α 稳定/中性元素", "symbol": "c_α", "range": "Al/Sn/Zr 合计 2–12 wt.%", "purpose": "建立强度、蠕变与焊接性的首轮局部窗口"},
                {"role": "β 稳定元素", "symbol": "c_β", "range": "V/Mo/Nb/Cr/Fe 合计 1–15 wt.%", "purpose": "围绕相稳定与淬透性做受控梯度"},
                {"role": "间隙元素", "symbol": "c_i", "range": "O/N/C/H 按目标规范限值", "purpose": "作为独立控制量记录，不用缺失值代替"},
            ],
            "process_variables": [
                {"variable": "相区与热处理", "levels": "相对 β 转变温度的固溶位置、冷却方式、时效温度/时间", "purpose": "成分与组织状态必须成对设计"},
                {"variable": "制造状态", "levels": "铸造、锻造、增材或板材；方向与缺陷状态", "purpose": "防止跨工艺合并性能"},
            ],
            "minimum_record_fields": ["完整 wt.%", "α/β 路线或 β 转变温度", "固溶/时效参数", "产品形态与方向", "晶粒/相分数", "测试温度与应变率", "拉伸/疲劳原始结果"],
            "first_round_strategy": "先选一个有可追溯工艺与性能的基准钛合金，固定热处理，仅对一个 β 稳定元素做基线+三级局部梯度；第二轮再联合调整 Al 当量和间隙氧。",
            "next_actions": ["确认目标 α/α+β/β 路线和基准牌号", "补齐热处理及测试温度/方向", "围绕一个 β 稳定元素建立首轮局部梯度"],
        }
    if any(token in lowered for token in ("高强钢", "超高强钢", "工具钢", "马氏体时效钢", "合金钢", "steel", "300m", "4340", "18ni")):
        return {
            "material_family": "高强钢/合金钢探索体系",
            "composition_basis": "wt.%；Fe 作为平衡元素",
            "formulation_components": [
                {"role": "Fe 基体", "symbol": "c_Fe", "range": "余量", "purpose": "锁定低合金钢、工具钢或马氏体时效钢路线"},
                {"role": "碳及基础合金元素", "symbol": "c_base", "range": "C 0.05–0.60；Cr/Ni/Mn/Si 按基准牌号局部变化", "purpose": "先固定强韧化机制，避免跨钢种自由组合"},
                {"role": "回火/二次硬化元素", "symbol": "c_h", "range": "Mo/W/Co/V 按基准体系设置局部梯度", "purpose": "与淬火回火制度联合记录"},
                {"role": "微合金与洁净度", "symbol": "c_t", "range": "Nb/Ti/B 及 P/S/O/N 按规范限值", "purpose": "独立记录夹杂、晶粒和韧性风险"},
            ],
            "process_variables": [
                {"variable": "奥氏体化—淬火—回火", "levels": "温度、保温、冷却介质、回火温度/时间", "purpose": "把性能与真实热处理状态绑定"},
                {"variable": "产品与方向", "levels": "板/棒/锻件、厚度、取样方向、表面状态", "purpose": "控制尺寸和各向异性影响"},
            ],
            "minimum_record_fields": ["完整化学成分", "熔炼与洁净度", "奥氏体化/淬火/回火参数", "产品尺寸和取样方向", "硬度与显微组织", "测试温度/应变率", "强度—延性—韧性原始结果"],
            "first_round_strategy": "以一个可追溯基准钢种和固定热处理为中心，先对一个主合金化元素做基线+三级局部梯度；确认强度—韧性趋势后再联合调整回火制度。",
            "next_actions": ["确认钢种路线、基准牌号和强韧性目标", "固定一套可复现热处理", "先对一个主合金元素建立局部梯度并同步测硬度/拉伸/韧性"],
        }
    if any(token in lowered for token in ("镁合金", "镁基", "magnesium")):
        return {
            "material_family": "镁合金探索体系", "composition_basis": "wt.%；Mg 作为平衡元素",
            "formulation_components": [
                {"role": "Mg 基体", "symbol": "c_Mg", "range": "余量", "purpose": "锁定铸造或变形镁合金路线"},
                {"role": "主合金元素", "symbol": "c_m", "range": "Al/Zn 或稀土/Y/Gd/Nd 按同一路线局部变化", "purpose": "不混合不同强化机制的数据"},
                {"role": "晶粒/耐蚀调节", "symbol": "c_t", "range": "Zr/Mn/Ca 等按基准牌号设置小梯度", "purpose": "记录晶粒、第二相和腐蚀副作用"},
            ],
            "process_variables": [{"variable": "铸造/变形与热处理", "levels": "熔炼保护、冷却速度、挤压/轧制、固溶与时效", "purpose": "控制织构、孔隙和析出状态"}],
            "minimum_record_fields": ["完整 wt.%", "熔炼保护与铸造/变形状态", "热处理", "织构/晶粒/孔隙", "腐蚀环境", "测试方向与温度", "力学原始数据"],
            "first_round_strategy": "选择单一强化路线和基准牌号，固定制造状态，对一个主合金元素建立基线+三级局部梯度。",
            "next_actions": ["确认铸造或变形路线", "选择同一路线基准牌号", "补齐织构、腐蚀和力学测试条件"],
        }
    if any(token in lowered for token in ("玻璃", "氧化物", "陶瓷", "cement", "水泥")):
        return {
            "material_family": "无机多组元体系", "composition_basis": "mol.% 或 wt.%；同一任务只使用一种基准",
            "formulation_components": [
                {"role": "网络形成体/主相", "symbol": "x_b", "range": "70–95%", "purpose": "固定主体网络或目标晶相"},
                {"role": "网络调节/第二相", "symbol": "x_m", "range": "5–25%", "purpose": "围绕热学、烧结或加工窗口设置梯度"},
                {"role": "微量功能组分", "symbol": "x_t", "range": "0–5%", "purpose": "逐个引入并验证析晶、挥发或相分离副作用"},
            ],
            "process_variables": [{"variable": "热历史", "levels": "熔制/烧结温度、保温、气氛、冷却/退火", "purpose": "区分同配方不同结构状态"}],
            "minimum_record_fields": ["原料纯度与完整配方", "计量基准", "热历史与气氛", "相组成/非晶度", "密度/孔隙", "试样状态", "目标性质原始数据"],
            "first_round_strategy": "固定主网络/主相与热历史，仅对一个调节组分设置基线+三级梯度；先验证相组成和致密度，再比较目标性质。",
            "next_actions": ["确认主网络或目标晶相", "统一 mol.%/wt.% 计量基准", "固定热历史并建立单组分梯度"],
        }
    return {
        "material_family": "单一金属/合金探索体系", "composition_basis": "wt.%；若为多主元 at.% 应改走 HEA/MPEA 专项",
        "formulation_components": [
            {"role": "基体元素", "symbol": "c_b", "range": "余量", "purpose": "按已知牌号族或目标相结构确定"},
            {"role": "主合金化元素", "symbol": "c_m", "range": "围绕一个可追溯基准成分做局部低—中—高梯度", "purpose": "避免无数据支撑的全空间自由配比"},
            {"role": "微合金化元素", "symbol": "c_t", "range": "按基准体系和标准限值逐项变化", "purpose": "单独验证组织与工艺敏感性"},
            {"role": "杂质/残余元素", "symbol": "c_i", "range": "按标准或试验限值", "purpose": "不由通用建议擅自放宽"},
        ],
        "process_variables": [{"variable": "制造与热处理", "levels": "熔炼/成形路线、固溶/时效或退火、冷却方式", "purpose": "使每组配方对应唯一可复现状态"}],
        "minimum_record_fields": ["完整成分与平衡元素", "原料/熔次", "制造和热处理", "产品形态与方向", "显微组织", "测试温度与方法", "目标性质原始数据"],
        "first_round_strategy": "先选一个可追溯基准材料，固定制造与热处理，对一个主合金化元素建立基线+三级局部梯度；首轮不同时改变多个强化机制。",
        "next_actions": ["确认基体、牌号族或目标相结构", "固定可复现制造/热处理状态", "选择一个主变量建立基线+三级局部梯度"],
    }


def generic_fallback_plan(payload: dict[str, Any], reason: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        text, keys = upstream_requirement(payload)
    except MaterialContextError as exc:
        effective = {"model_domain": "routing_confirmation", "raw_requirement": exc.request}
        return effective, {
            "parser": "rule_template_v0", "template": "alloy_domain_confirmation",
            "raw_requirement": exc.request, "upstream_context_keys": exc.keys,
            "effective_model_input": effective, "field_provenance": {}, "default_assumptions": [],
            "requires_domain_confirmation": True, "confirmation_message": str(exc),
            "questions_to_confirm": [str(exc)],
            "evidence_notice": "确认材料身份后再选择预测模型或配方试验设计，不预设材料家族。",
        }
    blueprint = _blueprint(text)
    effective = {
        "model_domain": "generic_composition_design_fallback_v1",
        **blueprint,
        "raw_requirement": text,
        "fallback_reason": reason,
    }
    plan = {
        "parser": "generic_rule_assisted_fallback_v2",
        "template": "generic_composition_design_fallback",
        "raw_requirement": text,
        "upstream_context_keys": keys,
        "effective_model_input": effective,
        "field_provenance": {"material_family": "rule_assisted", "formulation_components": "rule_assisted_experiment_design", "process_variables": "rule_assisted_experiment_design", "fallback_reason": "routing"},
        "default_assumptions": [{"field": "trained_expert", "value": "本次输入尚未匹配到可执行的预测模型", "status": "fallback_notice"}],
        "questions_to_confirm": blueprint["next_actions"],
        "evidence_notice": "本次输出配方变量与首轮试验方案；数值窗口是建议探索范围，不是预测配方。",
    }
    return effective, plan


def generic_fallback_result(payload: dict[str, Any], reason: str, service_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    effective, plan = generic_fallback_plan(payload, reason)
    taskid = task_id(payload)
    if plan.get("requires_domain_confirmation"):
        return {
            "taskid": taskid, "status": "waiting_for_input", "service": service_name,
            "model_domain": "routing_confirmation", "requirement_interpretation": plan,
            "user_conclusion": plan["confirmation_message"],
            "initial_candidates": [], "all_candidates": [],
            "sampling": {"generated": 0, "feasible": 0}, "elapsed_seconds": 0.0,
        }, {"taskid": taskid, "raw_scope": effective, "model_domain": "routing_confirmation"}
    capability_cards = public_capability_cards()
    stages = [
        {"label": "专项能力核对", "count": 1, "description": f"核对 {len(capability_cards)} 类已接入模型能力。"},
        {"label": "材料家族识别", "count": 1, "description": f"识别为{effective['material_family']}。"},
        {"label": "配方与工艺变量", "count": 1, "description": "形成组分角色、计量基准和不可混并的工艺变量。"},
        {"label": "首轮验证数据", "count": 1, "description": "形成基线+三级局部梯度和最少记录字段。"},
    ]
    result = {
        "taskid": taskid,
        "status": "completed_with_fallback",
        "service": service_name,
        "model_domain": "generic_composition_design_fallback_v1",
        "mode": "rule_assisted_generic_formulation",
        "model_version": "generic_rule_assisted_fallback_v2",
        "fallback_reason": reason,
        "supported_model_capabilities": capability_cards,
        "requirement_interpretation": plan,
        "screening_conditions": {"material_family": effective["material_family"], "composition_basis": effective["composition_basis"], "recommendation_scope": "配方变量与首轮试验设计"},
        "sampling": {"generated": 1, "feasible": 1, "funnel_stages": stages},
        "initial_candidates": [{"candidate_id": "GEN-01", "material_family": effective["material_family"], "composition_basis": effective["composition_basis"], "formulation_components": effective["formulation_components"], "process_variables": effective["process_variables"], "first_round_strategy": effective["first_round_strategy"], "minimum_record_fields": effective["minimum_record_fields"], "candidate_kind": "rule_assisted_experiment_template"}],
        "all_candidates": [{"candidate_id": "GEN-01", "material_family": effective["material_family"], "composition_basis": effective["composition_basis"], "formulation_components": effective["formulation_components"], "process_variables": effective["process_variables"], "first_round_strategy": effective["first_round_strategy"], "minimum_record_fields": effective["minimum_record_fields"], "candidate_kind": "rule_assisted_experiment_template"}],
        "model_evidence": {"model_version": "本次未选择预测模型", "data_type": "材料家族规则辅助的配方与试验设计框架", "validation": "本次未调用性能预测模型。"},
        "nonlinear_response_function": {"name": "not_available", "meaning": "本次输入尚未匹配到可执行的成分—性质预测函数。", "input": "待补充组成、工艺和服役条件", "output": "配方变量与首轮试验数据模板"},
        "next_actions": plan["questions_to_confirm"],
        "user_conclusion": f"针对{effective['material_family']}，建议以一个可追溯基准材料和固定工艺为起点：{effective['first_round_strategy']} 按上述字段记录组成、工艺及性质，形成下一轮配方比较的依据。",
        "downstream_handoff_text": "交接通用配比变量、闭合约束、待补工艺字段和首轮试验记录模板；在形成可追溯数据集前不得将本结果用于性能门槛筛选。",
        "elapsed_seconds": 0.0,
    }
    return result, {"taskid": taskid, "raw_scope": effective, "model_domain": result["model_domain"]}
