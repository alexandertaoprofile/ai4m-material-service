"""Legacy Team compatibility for the mature-material catalogue service.

The web service in :mod:`main` is the production execution path.  This module
exists only for callers that still discover a role by its historical module
path.  It deliberately has no Alpha framework, MatterGen, MatterSim, Materials
Project, GPU or new-material-discovery dependency.
"""
from __future__ import annotations

class MatureMaterialCatalogQuery:
    """Describe the deterministic existing-material lookup action."""

    name: str = "MatureMaterialCatalogQuery"
    desc: str = (
        "在已清洗的成熟材料目录中，按名称、牌号、标准、材料族、温度和性质条件检索，"
        "返回来源可追溯的性质证据、候选对比和数据缺口；不生成或模拟新材料。"
    )

    async def run(self, instruction: str, *args, **kwargs) -> str:
        return (
            "已有材料查询由 mature_material 的 /start 或 /mature-material/query 执行。"
            "请提供材料名称/牌号、服役温度及需要核验的性质条件。"
        )


class MatureMaterialCatalogService:
    """已有成熟材料数据库检索与性质核验智能体。"""

    name: str = "MatureMaterialCatalogService"
    profile: str = (
        "已有成熟材料数据库检索与性质核验智能体。面向已入库的商品材料、牌号和标准号，"
        "核验材料状态、温度范围、性质值与来源，并输出候选比较。"
        "边界：仅查询本服务已清洗的结构化目录；不进行材料生成、数值模拟或外部数据库检索，"
        "也不从未入库 PDF 或缺失数据推断性质。"
    )

    def __init__(self, **kwargs):
        self.actions = [MatureMaterialCatalogQuery]
        self.metadata = kwargs


# Source-compatibility alias for generic Alpha launchers that still import the
# historical role name.  New code should use MatureMaterialCatalogService.
XIMUAlpha_MNS = MatureMaterialCatalogService
