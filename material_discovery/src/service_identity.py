"""无机新材料服务对角色发现公开的统一身份描述。"""

SERVICE_ID = "inorganic_new_material_generation"
ROLE_NAME = "新材料发现与候选生成"
ACTION_NAME = "新材料候选生成与计算评估"

ROLE_PROFILE = (
    "面向周期性无机晶体的候选发现。结合当前任务与完整上文，从材料方向、应用场景、性能目标或已给元素体系"
    "归纳可追溯的无机元素体系，"
    "使用 MatterGen 或已验证的 Co-Cr-Fe-Mn-Ni HEA 微调模型生成候选结构，"
    "经 pymatgen 做结构准入，再以 ALIGNN 进行已接入性质的快速预测，并用 MatterSim 与 MP2020 参考凸包做"
    "结构松弛和热力学初筛。输出候选 CIF、结构与性质初筛结果、排序和可视化资产。"
    "本服务不查询已有材料事实，不做合金元素配比优化、聚合物/复合材料配方、严格化学计量保证、真实合成路线预测，"
    "也不把模型初筛等同于 DFT 或实验结论。"
)
ACTION_DESCRIPTION = (
    "结合当前任务与完整上文归纳可追溯的无机元素体系，生成周期性晶体候选，完成结构准入、"
    "ALIGNN 已接入性质快速预测、MatterSim 松弛与 E_hull 初筛，并交付候选结构、排序和可视化。"
)

import os
MATTERGEN_ROLE_PROFILE = ROLE_PROFILE
MATTERGEN_ACTION_DESCRIPTION = ACTION_DESCRIPTION
OMATG_ROLE_PROFILE = (
        "面向周期性无机晶体发现，结合当前任务与完整上文确认组成与目标。"
        "已确认化学式走 OMatG CSP（固定整数原子计数），已确认元素集合走白名单 DNG，直接评估生成的组成与结构。"
        "多元素白名单探索默认至少两种元素，采用服务侧部分元素位点锚定，并复核必含元素；明确的单质任务保留单质路线。"
        "DNG 默认不追加 CSP；用户选择某个组成后，可继续固定计量探索，或明确开启追加 CSP。"
        "只有材料方向、应用场景、性能目标而无元素时，需用户确认开放化学空间或补充组成。"
        "当前检查点每胞最多 20 原子，每个生成阶段默认 10 个候选。"
        "输出 CIF、组成/周期几何检查、MatterSim 松弛与混合参考凸包能初筛、"
        "实际完成的 ALIGNN 性质快速预测、结构去重与可视化。"
        "性质目标通过后筛实现；合金配比走1111，聚合物、有机分子、非晶、合成工艺和工程服役性能需要专项流程。"
    )
OMATG_ACTION_DESCRIPTION = "使用已确认化学式进行 OMatG CSP，或进行限定元素/已确认开放空间的 DNG，完成松弛、稳定性估算、性质快速预测与候选交付。"


def current_role_profile():
    return OMATG_ROLE_PROFILE if os.getenv("NEW_MATERIAL_GENERATOR", "mattergen").lower() == "omatg" else MATTERGEN_ROLE_PROFILE


def current_action_description():
    return OMATG_ACTION_DESCRIPTION if os.getenv("NEW_MATERIAL_GENERATOR", "mattergen").lower() == "omatg" else MATTERGEN_ACTION_DESCRIPTION


ROLE_PROFILE = current_role_profile()
ACTION_DESCRIPTION = current_action_description()
