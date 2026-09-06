"""Task-local technical diagnostics; these are deliberately not customer-page content."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def write_evidence_audit(result: dict[str, Any], results_root: Path) -> Path:
    """Record unavailable evidence and rendering scope for engineering follow-up."""
    external = (result.get("evidence_manifest") or {}).get("external_sources") or {}
    training = external.get("training") or {}
    md = external.get("md") or {}
    path = results_root / result["taskid"] / "logs" / "evidence_audit.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# W-14 证据与可视化审计日志",
        "",
        "本文件供服务维护与数据导入排查使用，不进入客户 Markdown 页面。",
        "",
        "## 已在客户页面展示的图表",
        "",
        "- DeepMD Energy / Virial RMSE 训练收敛图（`lcurve.out`）",
        "- NPT 晶格参数与密度图（LAMMPS 原始输出）",
        "- 弹性常数与 VRH 模量图（小应变应力—应变原始输出）",
        "- 声速与热容图（MD 后处理输出）",
        "- 300 K 计算值—实际性能值对比图（已核验实际性能参考值）",
        "",
        "## 当前不生成的图表与原因",
        "",
        "- DFT–MLIP Energy / Force / Stress parity：缺少逐构型 MLIP 预测数组及明确的 train/validation/test 划分文件。",
        "- 误差分布与 OOD 覆盖图：缺少原子局域环境嵌入、预测残差和覆盖度计算产物。",
        "- DFT 输入参数卡：当前 W-14 归档未包含计算软件、泛函、赝势、k 点和收敛参数的原始元信息。",
        "",
        "## 当前源状态",
        "",
        f"- 训练数据帧数：{(training.get('dataset') or {}).get('frames', '未读取')}",
        f"- MD 势文件绑定已验证：{md.get('model_link_verified', False)}",
        "- 建议导入：原始 DFT 输入/输出、MLIP 逐构型预测、数据划分清单、势文件与 LAMMPS 任务的校验和对应关系。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
