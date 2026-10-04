"""配方优化服务对母服务、角色发现和通用兜底公开的统一能力说明。"""
from __future__ import annotations

from typing import Any


SERVICE_ID = "alloy_composition_optimization"
ROLE_NAME = "材料配方设计与性能筛选（计算模拟）"
ACTION_NAME = "Coding"


# 只登记已经存在专项 runner、模型工件和客户结果页的能力。训练样本数属于
# 随模型版本变化的验证元数据，不写进路由契约；角色发现只公开稳定的输入、输出和边界。
MODEL_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {
        "model_domain": "hea_mpea",
        "name": "HEA/MPEA 高熵与多主元合金",
        "when": "明确提出 HEA/MPEA、高熵/多主元、at.% 多元素成分空间，或强度—硬度—相稳定性配比探索",
        "input": "允许元素、at.% 边界、制备工艺、评价温度和性能目标",
        "output": "屈服强度、硬度、相组成倾向、数据适用域和候选排序",
        "boundary": "实验标签分别训练；只在已记录元素与成分邻域内作模型初筛",
    },
    {
        "model_domain": "ni_superalloy_hot_end",
        "name": "热端镍基高温合金",
        "when": "发动机热端、涡轮叶片、单晶/定向凝固、蠕变或持久寿命条件下的镍基新配比筛选",
        "input": "元素 wt.% 边界、制造路线、热处理、温度、载荷和性能门槛",
        "output": "短时 UTS、0.2% 屈服强度、蠕变断裂寿命、延伸率辅助值和候选排序",
        "boundary": "围绕同路线 Cambridge 来源锚点局部探索；延伸率仅辅助展示",
    },
    {
        "model_domain": "copper_hot_end_local_composition_v1",
        "name": "铜基再生冷却热壁局部配比",
        "when": "GRCop/NARloy/CuCrZr 等铜基热壁体系且明确要求新配比、元素含量或局部成分优化",
        "input": "Cu-Cr-Nb-Zr-Ag 等 wt.% 边界、工艺状态和评价温度",
        "output": "短时 UTS、0.2% 屈服、室温 IACS 辅助预测、适用域和验证优先级",
        "boundary": "严格局部模型；已有牌号性质核验交给 1105，热导/硬度/CTE 仅作分级工程估算",
    },
    {
        "model_domain": "reusable_rocket_stainless",
        "name": "火箭奥氏体不锈钢",
        "when": "可回收火箭贮箱、承压壳体、外壳或低温奥氏体不锈钢的成分—工艺筛选",
        "input": "元素 wt.% 边界、固溶处理、温度、板厚和焊接状态",
        "output": "293–1273 K 短时屈服/UTS/延伸率；低温参考与焊接、疲劳、LOX 验证路径",
        "boundary": "相对扎实的短时拉伸模型；低温、焊接、疲劳和相容性仍需专项验证",
    },
    {
        "model_domain": "aerospace_aluminum_composition_v1",
        "name": "航空铝合金与 Al-Li",
        "when": "航空铝、Al-Li、AA2xxx/6xxx/7xxx 的新配比与室温性能筛选",
        "input": "25 元素 wt.%、已记录工艺类别、候选数量和性能目标",
        "output": "名义室温 UTS、0.2% 屈服强度、延伸率、组成留出误差和适用域",
        "boundary": "不输出精确固溶/时效制度；Al-Li 小样本子域需优先复核",
    },
    {
        "model_domain": "chip_glass_thermomechanical_family_v1",
        "name": "芯片封装玻璃基板",
        "when": "低硼无碱铝硼硅酸盐玻璃基板的氧化物 mol.% 配方和热机械初筛",
        "input": "氧化物 mol.% 边界、CTE/E/SOC 目标或门槛、候选数量；仿真另需层堆与热历史",
        "output": "CTE、密度、弹性模量、应力光学系数、黏度特征温度和同家族候选排序",
        "boundary": "仅在同一可追溯专利家族局部筛选，禁止跨域自由配比",
    },
    {
        "model_domain": "short_cf_thermomechanical_rve_v1",
        "name": "短碳纤维热塑性 RVE",
        "when": "短碳纤维增强 PLA/PETG/ABS/ASA/PC 或自定义基体的正交各向异性线弹性本构初筛",
        "input": "基体 E/ν/密度、纤维体积分数、有效长度、主方向取向和候选数量",
        "output": "9 个独立工程常数、导出泊松比、6×6 正定刚度矩阵和候选排序",
        "boundary": "仿真代理标签，不等同于真实打印件强度；连续纤维和热固性复材不在本模型域",
    },
    {
        "model_domain": "perovskite_transport_stability_v2",
        "name": "卤化物钙钛矿电输运",
        "when": "Cs/FA/MA–Pb(I,Br)3 组分的变温离子/电输运稳定性配比初筛",
        "input": "A 位与卤素位组分、170–330 K 目标温区和短名单数量",
        "output": "变温 ln[σ(T)T]、有效活化能、经验不确定度和可追溯组分短名单",
        "boundary": "只覆盖 65 个独立组分邻域；不输出器件 T80、PCE 或环境分解寿命",
    },
)

SUPPORTED_MODEL_DOMAINS = frozenset(item["model_domain"] for item in MODEL_CAPABILITIES)


def role_model_routes() -> dict[str, dict[str, str]]:
    """Return the existing ``routing.model_routes`` shape from one source of truth."""
    return {
        item["model_domain"]: {
            "name": item["name"],
            "when": item["when"],
            "input": item["input"],
            "output": f"{item['output']}；边界：{item['boundary']}",
        }
        for item in MODEL_CAPABILITIES
    }


def public_capability_cards() -> list[dict[str, str]]:
    """Stable customer-facing capability rows used at the start of fallback pages."""
    return [
        {
            "model_domain": item["model_domain"],
            "name": item["name"],
            "input": item["input"],
            "output": item["output"],
            "boundary": item["boundary"],
        }
        for item in MODEL_CAPABILITIES
    ]


SERVICE_BOUNDARY = (
    "本服务只对已经部署专项模型的材料域执行性能计算：HEA/MPEA、热端镍基、铜基再生冷却热壁局部配比、"
    "火箭奥氏体不锈钢、航空铝合金与 Al-Li、芯片封装玻璃基板、短碳纤维热塑性 RVE、"
    "以及 Cs/FA/MA–Pb(I,Br)3 卤化物钙钛矿电输运。已有牌号和商品材料的事实/性质查询进入 1105；"
    "数据库外新晶体结构生成进入 1115；未接入专项模型的材料只返回明确标注的通用配比与试验设计框架，"
    "不输出性能预测或工程放行结论。"
)

ROLE_PROFILE = (
    "承担材料配方设计、配方优化、配比优化和按性能目标调整组分的任务。可计算 HEA/MPEA 强度—硬度—相风险、热端镍基"
    "短时强度与蠕变寿命、铜基热壁局部新配比、火箭奥氏体不锈钢短时拉伸、航空铝合金室温强度—延性、"
    "芯片玻璃热机械性质、短碳纤维热塑性 RVE 线弹性本构，以及卤化物钙钛矿变温电输运代理。"
    "未接入专项模型的材料可形成具体的配方变量、工艺变量和最少试验数据方案，不提供未经验证的性能数值。"
)

ACTION_DESCRIPTION = (
    "提供受约束的材料配方候选及模型支持的性能、本构或电输运评价，给出适用范围和验证优先级。"
    "未接入专项模型的材料提供组分变量、工艺变量和试验数据方案；这类方案不包含性能预测数值。"
)

ROUTING_MATCH_WHEN = (
    "当前任务要求材料配方设计、配方优化、配比或组分优化、调整组分比例，或根据性能目标提出候选配方时调用。"
    "本服务覆盖专项模型计算及未接入模型材料的配方变量与试验设计；已有牌号性质查询和新晶体结构生成分别由 1105、1115 承接。"
)
