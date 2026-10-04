"""Customer-facing capability fallback for discovery runs without a usable candidate."""

from __future__ import annotations


def needs_capability_fallback(result) -> bool:
    """Return true when the run produced no structurally admitted candidate."""
    if not result.generation.candidates:
        return True
    return not any(item.is_valid is True for item in result.validations)


def _outcome_text(result) -> str:
    generated = len(result.generation.candidates)
    admitted = sum(item.is_valid is True for item in result.validations)
    if generated == 0:
        return (
            "本轮没有形成可读取的候选晶体，因此没有进入结构准入、性质快速预测和热力学初筛。"
            "这只说明本次计算未产出候选，不代表该元素体系不可行。"
        )
    return (
        f"本轮生成了 {generated} 个候选，但通过基础结构检查的候选为 {admitted} 个，"
        "因此没有把不合理结构继续包装成性质或稳定性结论。"
    )


def build_capability_fallback(result) -> str:
    """Describe the implemented calculation scope when no usable result remains."""
    return "\n".join([
        "## 未形成可用候选",
        _outcome_text(result),
        "",
        "### 支持的任务与输出",
        "| 支持能力 | 可以输出 |",
        "|---|---|",
        (
            "| 周期性无机晶体候选生成 | 使用官方 MatterGen 在元素体系或晶体结构空间生成候选 CIF；"
            "现有条件模型可分别使用元素体系与 E_hull、带隙、体积模量、磁密度与 HHI、空间群等条件。"
            "输出候选化学式、晶格、原子种类和分数坐标。 |"
        ),
        (
            "| Co-Cr-Fe-Mn-Ni 高熵合金专项生成 | 对准确的 Co-Cr-Fe-Mn-Ni 元素集合且带 E_hull 条件的任务，"
            "使用 HEA MatterGen v2 微调模型生成候选结构；输出候选 CIF 和模型来源记录。 |"
        ),
        (
            "| 结构合理性检查 | 使用 pymatgen 检查 CIF 可读性、占位有序性和原子间距，"
            "并输出化学式、密度、空间群、晶系、位点数及未通过原因。 |"
        ),
        (
            "| 结构松弛与热力学初筛 | 对通过结构检查的候选使用 MatterSim 松弛，"
            "输出松弛后结构、形成能及相对 MP2020 校正参考凸包的 E_hull，并保留同元素体系竞争相信息。 |"
        ),
        (
            "| 结构—性质快速预测 | 使用 ALIGNN 输出带隙、体积模量、剪切模量、电子有效质量、"
            "空穴有效质量和 x 方向介电常数；可由体积/剪切模量给出工程级硬度估算。"
            "这些结果属于模型快速初筛，不是实验或 DFT 结论。 |"
        ),
        (
            "| 候选排序与交付 | 按结构准入和可用 E_hull 对候选排序，输出任务 manifest、候选结构文件、"
            "结构图、旋转预览和可交互三维模型（以本轮实际生成的资产为准）。 |"
        ),
        "",
        "### 需要转交或另行验证的任务",
        "- 已有材料的牌号、厂家、标准、实测性能或文献事实属于已有材料检索任务，应交给已有材料检索服务。",
        "- 合金各元素 wt.%/at.% 配比、工艺窗口和既有配方性能优化属于配比设计任务，应交给材料配比优化服务。",
        "- 当前主链适用于周期性无机晶体；聚合物配方、有机分子、复合材料宏观配比、玻璃/非晶和液体结构尚未覆盖。",
        "- 由化学式启动任务时，当前只提取元素集合，生成结构不会被视为严格化学计量比的晶体结构预测。",
        "- 超导转变温度、离子/热/电导率、熔点、蠕变、疲劳、耐蚀、抗氧化和加工工艺性尚无本服务专项模型，当前不作定量结论。",
        "- 真实合成路线、前驱体用量和热处理制度需要合成规划或实验流程；生成结构本身不是可合成性验证。",
        "- 工程决策仍需统一口径的 DFT、声子/动力学稳定性、缺陷/界面计算或实验验证；MatterSim 与 ALIGNN 结果用于候选初筛。",
        "",
        "### 建议转向",
        "若目标仍是周期性无机晶体发现，可在相同元素体系下调整生成条件或扩大候选数后重试；"
        "若目标属于已有材料查询、合金配比优化、聚合物/复合材料设计或专项性能计算，应切换到对应服务，不再由本服务泛化生成答案。",
    ])
