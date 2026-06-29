"""Build prompt context from uploaded design assets.

This module is intentionally lightweight so it can later move into a shared
package used by all material workflow services.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


JsonDict = Dict[str, Any]
MAX_STL_BYTES = 10 * 1024 * 1024
DOWNLOAD_TIMEOUT = 1.5


PART_HINTS = [
    ("腕部", "机器人腕部"),
    ("手腕", "机器人腕部"),
    ("HS-225BB", "Hitec HS-225BB 舵机/关节电机"),
    ("hs225bb", "Hitec HS-225BB 舵机/关节电机"),
    ("舵机", "舵机/关节电机"),
    ("电机", "电机/关节驱动组件"),
    ("腰部", "机器人腰部"),
    ("肩部", "机器人肩部"),
    ("肘部", "机器人肘部"),
    ("关节", "机器人关节"),
    ("转子", "转子/旋转件"),
    ("齿轮", "齿轮/传动件"),
    ("顶盖", "顶盖/盖板"),
    ("盖板", "盖板/外壳覆盖件"),
    ("外壳", "外壳/壳体"),
    ("壳体", "外壳/壳体"),
    ("支架", "支架/承载结构件"),
    ("底座", "底座/安装结构件"),
    ("连接", "连接件/转接结构件"),
    ("法兰", "法兰/连接结构件"),
    ("散热", "散热结构件"),
    ("风道", "风道/导流结构件"),
]


def stl_assets(file_metadata: Iterable[JsonDict]) -> List[JsonDict]:
    assets: List[JsonDict] = []
    for item in file_metadata or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("original_filename") or item.get("filename") or "")
        content_type = str(item.get("content_type") or "").lower()
        storage_key = str(item.get("storage_key") or "")
        suffix = Path(name or storage_key).suffix.lower()
        if suffix == ".stl" or "stl" in content_type:
            assets.append(item)
    return assets


def infer_part_context_from_filename(filename: str) -> JsonDict:
    stem = Path(str(filename or "")).stem
    cleaned = re.sub(r"[_\-]+", " ", stem).strip()
    cleaned = re.sub(r"^\s*\d+\s*", "", cleaned)
    cleaned_no_space = re.sub(r"\s+", "", cleaned)

    matched = []
    for keyword, label in PART_HINTS:
        if keyword in cleaned_no_space and label not in matched:
            matched.append(label)

    numbers = re.findall(r"\d+", cleaned_no_space)
    if matched:
        part_name = _compose_part_name(cleaned_no_space, matched)
    else:
        part_name = cleaned or "未命名STL零件"

    category = "结构件"
    if any(word.lower() in cleaned_no_space.lower() for word in ("hs-225bb", "hs225bb")) or any(word in cleaned_no_space for word in ("舵机", "电机")):
        category = "关节电机/舵机组件外壳"
    elif any(word in cleaned_no_space for word in ("齿轮", "转子", "转轴", "同步轮")):
        category = "旋转/传动类结构件"
    elif any(word in cleaned_no_space for word in ("顶盖", "盖板", "外壳", "壳体")):
        category = "盖板/壳体类结构件"
    elif any(word in cleaned_no_space for word in ("支架", "底座", "法兰", "连接")):
        category = "支架/连接类承载结构件"
    elif any(word in cleaned_no_space for word in ("散热", "风道")):
        category = "热管理/导流结构件"

    focus = ["尺寸稳定", "装配可靠性", "刚度", "轻量化"]
    if any(word in cleaned_no_space for word in ("腕部", "手腕", "腰部", "肩部", "肘部", "关节")):
        focus.extend(["周期载荷", "疲劳", "层间结合"])
    if any(word.lower() in cleaned_no_space.lower() for word in ("hs-225bb", "hs225bb")) or any(word in cleaned_no_space for word in ("舵机", "电机")):
        focus.extend(["外壳刚度", "安装孔尺寸稳定", "内部金属核心散热", "热源耦合", "壳体-金属件界面"])
    if any(word in cleaned_no_space for word in ("齿轮", "转子", "转轴", "同步轮")):
        focus.extend(["耐磨性", "齿形精度", "扭转载荷", "冲击韧性"])
    if any(word in cleaned_no_space for word in ("顶盖", "盖板", "外壳", "壳体")):
        focus.extend(["翘曲控制", "孔位稳定", "表面质量"])
    if any(word in cleaned_no_space for word in ("散热", "热", "风道")):
        focus.extend(["导热/热管理", "热循环"])

    return {
        "filename_stem": cleaned,
        "part_name": part_name,
        "part_category": category,
        "semantic_labels": matched,
        "sequence_numbers": numbers,
        "inferred_focus": _dedupe_text(focus),
        "confidence": "medium" if matched else "low",
        "source": "filename",
    }


def build_asset_context_prompt(file_metadata: Iterable[JsonDict]) -> str:
    assets = stl_assets(file_metadata)
    if not assets:
        return ""

    lines = ["已选择 STL 模型资产，请将其作为本轮耗材选型和计算优化的重要上下文："]
    for idx, asset in enumerate(assets[:3], start=1):
        filename = str(asset.get("original_filename") or asset.get("filename") or asset.get("storage_key") or "未命名STL")
        context = infer_part_context_from_filename(filename)
        storage_key = str(asset.get("storage_key") or "").strip()
        size = _format_size(asset.get("size"))
        lines.append(f"{idx}. 文件名：{filename}")
        lines.append(f"   - 初步零件语义：{context['part_name']}")
        lines.append(f"   - 零件类别：{context['part_category']}")
        if context.get("semantic_labels"):
            lines.append(f"   - 文件名线索：{'、'.join(context['semantic_labels'])}")
        lines.append(f"   - 推断关注点：{'、'.join(context['inferred_focus'])}")
        if storage_key:
            lines.append(f"   - 资产路径：{storage_key}")
        if size:
            lines.append(f"   - 文件大小：{size}")
        geometry = build_geometry_context(asset)
        if geometry:
            lines.append("   - STL 几何摘要：" + geometry)

    lines.append("使用要求：")
    lines.append("- 默认制造方式：若用户没有显式指定 CNC、注塑、烧结、铸造或其他工艺，请将机器人/机械臂 STL 视为待 FDM/FFF 丝材 3D 打印制造的结构件。")
    lines.append("- 若 STL 或用户文本指向关节电机、舵机、电机壳体或 HS-225BB：本服务只为外部可打印壳体/安装结构选择耗材；内部电机、轴、齿轮、轴承、螺丝、铜绕组和磁钢等按等效金属/机电核心处理，并可用于生成统一热场仿真输入。")
    lines.append("- 候选材料边界：优先考虑商用 3D 打印耗材、可打印工程塑料和可打印复合耗材；氮化硼、氮化铝、陶瓷、金属等高性能材料应优先作为填料、改性方向或文献对照，不应直接当作可打印耗材首选。")
    lines.append("- 请优先围绕上述 STL 对应零件的应用部位、结构形式和可能工况开展耗材筛选。")
    lines.append("- 将推断关注点转化为耗材性能、打印工艺窗口、验证口径和风险项。")
    lines.append("- 如果用户显式描述与 STL 文件名线索冲突，以用户显式描述为准。")
    lines.append("- 几何摘要仅用于快速验证和兜底，不等同于完整 CAD 特征识别或仿真。")
    return "\n".join(lines)


def build_geometry_context(asset: JsonDict) -> str:
    data = _read_stl_bytes(asset)
    if not data:
        return ""
    summary = _parse_stl_geometry(data)
    if not summary:
        return ""

    dims = summary.get("bbox_mm") or []
    dims_text = " x ".join(f"{v:g}" for v in dims) + " mm" if len(dims) == 3 else "待确认"
    shape = summary.get("shape_hint") or "通用结构件"
    parts = [
        f"包围盒约 {dims_text}",
        f"三角面约 {summary.get('triangles', 0)} 个",
        f"形态倾向：{shape}",
    ]
    area = summary.get("surface_area_mm2")
    volume = summary.get("volume_mm3")
    if isinstance(area, (int, float)) and area > 0:
        parts.append(f"表面积约 {area:g} mm2")
    if isinstance(volume, (int, float)) and volume > 0:
        parts.append(f"封闭体积估算约 {volume:g} mm3")
    return "；".join(parts)


def _read_stl_bytes(asset: JsonDict) -> bytes:
    size = asset.get("size")
    if isinstance(size, (int, float)) and size > MAX_STL_BYTES:
        return b""
    url = str(asset.get("download_url") or asset.get("preview_url") or "").strip()
    if not url:
        return b""
    try:
        import requests

        session = requests.Session()
        # The service may inherit HTTP(S)_PROXY from the shell/Codex runtime.
        # STL assets are already public MinIO URLs for the platform, so fetch
        # them directly instead of routing through an unrelated local proxy.
        session.trust_env = False
        resp = session.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True)
        resp.raise_for_status()
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_STL_BYTES:
                return b""
            chunks.append(chunk)
        return b"".join(chunks)
    except Exception:
        return b""


def _parse_stl_geometry(data: bytes) -> Optional[JsonDict]:
    vertices = _binary_stl_vertices(data)
    triangles = len(vertices) // 3
    if not vertices:
        vertices = _ascii_stl_vertices(data)
        triangles = len(vertices) // 3
    if not vertices or triangles <= 0:
        return None

    xs = [p[0] for p in vertices]
    ys = [p[1] for p in vertices]
    zs = [p[2] for p in vertices]
    bbox = [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)]
    bbox = [round(abs(v), 2) for v in bbox]
    area, volume = _mesh_area_volume(vertices)
    return {
        "bbox_mm": bbox,
        "triangles": triangles,
        "surface_area_mm2": round(area, 1) if area > 0 else None,
        "volume_mm3": round(abs(volume), 1) if abs(volume) > 0 else None,
        "shape_hint": _shape_hint_from_bbox(bbox),
    }


def _binary_stl_vertices(data: bytes) -> List[tuple]:
    if len(data) < 84:
        return []
    try:
        tri_count = struct.unpack_from("<I", data, 80)[0]
    except Exception:
        return []
    expected = 84 + tri_count * 50
    if tri_count <= 0 or expected > len(data):
        return []
    vertices = []
    offset = 84
    try:
        for _ in range(tri_count):
            # normal: 12 bytes, vertices: 36 bytes, attribute: 2 bytes
            vals = struct.unpack_from("<12f", data, offset)
            vertices.extend((vals[3], vals[4], vals[5]))
            vertices.extend((vals[6], vals[7], vals[8]))
            vertices.extend((vals[9], vals[10], vals[11]))
            offset += 50
    except Exception:
        return []
    return [(vertices[i], vertices[i + 1], vertices[i + 2]) for i in range(0, len(vertices), 3)]


def _ascii_stl_vertices(data: bytes) -> List[tuple]:
    try:
        text = data[:MAX_STL_BYTES].decode("utf-8", errors="ignore")
    except Exception:
        return []
    matches = re.findall(
        r"vertex\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)",
        text,
    )
    vertices = []
    for x, y, z in matches:
        try:
            vertices.append((float(x), float(y), float(z)))
        except Exception:
            continue
    return vertices


def _mesh_area_volume(vertices: List[tuple]) -> tuple:
    area = 0.0
    volume = 0.0
    for i in range(0, len(vertices) - 2, 3):
        a, b, c = vertices[i], vertices[i + 1], vertices[i + 2]
        ab = _sub3(b, a)
        ac = _sub3(c, a)
        cross = _cross3(ab, ac)
        area += 0.5 * _norm3(cross)
        volume += _dot3(a, cross) / 6.0
    return area, volume


def _shape_hint_from_bbox(bbox: List[float]) -> str:
    dims = sorted([v for v in bbox if v > 0])
    if len(dims) != 3:
        return "通用结构件"
    small, mid, large = dims
    flat = small / large < 0.18 if large else False
    elongated = large / max(mid, 1e-9) > 2.5
    near_square = mid / large > 0.75 if large else False
    if flat and near_square:
        return "扁平盘状/盖板类零件"
    if flat:
        return "薄壁/扁平类零件"
    if elongated:
        return "长条支架/梁类零件"
    return "块状/壳体类结构件"


def _sub3(a: tuple, b: tuple) -> tuple:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross3(a: tuple, b: tuple) -> tuple:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot3(a: tuple, b: tuple) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm3(a: tuple) -> float:
    return (a[0] ** 2 + a[1] ** 2 + a[2] ** 2) ** 0.5


def _format_size(value: Any) -> Optional[str]:
    if not isinstance(value, (int, float)):
        return None
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.2f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{int(value)} B"


def _dedupe_text(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _compose_part_name(filename_stem: str, labels: List[str]) -> str:
    location = ""
    if any(word in filename_stem for word in ("腰部", "腰")):
        location = "机器人腰部"
    elif any(word in filename_stem for word in ("腕部", "手腕")):
        location = "机器人腕部"
    elif "肩部" in filename_stem:
        location = "机器人肩部"
    elif "肘部" in filename_stem:
        location = "机器人肘部"
    elif "关节" in filename_stem:
        location = "机器人关节"

    part = ""
    if "齿轮" in filename_stem and "转子" in filename_stem:
        part = "转子齿轮组件"
    elif "齿轮" in filename_stem:
        part = "齿轮组件"
    elif "转子" in filename_stem:
        part = "转子/旋转件"
    elif "顶盖" in filename_stem:
        part = "顶盖"
    elif "盖板" in filename_stem:
        part = "盖板"
    elif any(word in filename_stem for word in ("外壳", "壳体")):
        part = "壳体"

    if location and part:
        return f"{location}{part}"
    if part:
        return part
    if location:
        return f"{location}结构件"
    return "".join(_dedupe_text([label.split("/")[0] for label in labels[:2]]))
