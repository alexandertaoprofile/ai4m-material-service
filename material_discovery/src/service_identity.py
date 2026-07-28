"""无机新材料服务对角色发现公开的统一身份描述。"""

SERVICE_ID = "inorganic_new_material_generation"
ROLE_NAME = "inorganic_new_material"
ACTION_NAME = "inorganic_new_material_discovery"

ROLE_PROFILE = (
    "生成式无机新材料发现服务：将数据库外新无机晶体的设计需求转为可执行的生成与热力学初筛任务。"
    "输入：化学式、元素体系、无机材料类别、应用场景或可追溯的上游材料结论之一；可选结构化 new_material 合同"
    "（allowed_elements、target_properties、validation_targets、max_candidates）。"
    "输出：候选 CIF/松弛结构路径、MatterSim--MP 近似形成能与高于凸包能、排序、阶段判断结论及完整 manifest；"
    "若无法从完整上游信息归纳无机材料起点，则向用户提示需补充的材料信息并等待补充，不宣称生成失败。"
    "执行链：约束规范化 → MatterGen 条件生成 → pymatgen 结构准入 → MatterSim 松弛 → MP 同元素体系竞争相查询与局部相图。"
    "边界：不用于已有材料查询、商品牌号、材料筛选/选型、FDM/FFF 丝材、商用耗材性质对比，"
    "也不处理合金或高温合金的元素配比、原子百分比与成分空间优化；这些分别应进入成熟材料或合金配比优化服务。"
    "结论只代表 MLFF--MP 热力学初筛；高温强度、蠕变、氧化、电导等目标性质必须由专项模型、DFT 或实验确认。"
)
ACTION_DESCRIPTION = (
    "输入数据库外新无机晶体的设计需求：化学式、元素体系、材料类别、应用场景或上游材料结论，"
    "可选结构化 new_material 约束（allowed_elements、target_properties、validation_targets、max_candidates）。"
    "将其规范化为 MatterGen 条件，生成候选晶体，执行 pymatgen 准入与 MatterSim--MP 热力学初筛，"
    "输出候选结构、初筛证据、排序和 manifest。若无法从完整上游信息归纳可追溯的无机材料起点，"
    "输出用户友好的补充信息请求，不将其报告为计算失败。"
)
