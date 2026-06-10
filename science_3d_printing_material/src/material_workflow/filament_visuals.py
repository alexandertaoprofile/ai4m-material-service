"""Visual assets for 3D-printing filament selection."""

from __future__ import annotations

import html
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from alpha.logs import logger

from .filament_selector import FilamentSelectionResult, build_optimization_plan


RADAR_AXES = [
    ("thermal", "热管理"),
    ("strength", "强度"),
    ("stiffness", "刚度"),
    ("layer_adhesion", "层间结合"),
    ("heat_resistance", "耐热"),
    ("dimensional_stability", "尺寸稳定"),
    ("electrical_insulation", "电绝缘"),
]


def build_filament_radar_svg(result: FilamentSelectionResult, out_path: str) -> Optional[str]:
    """Create a self-contained SVG radar chart for the top ranked candidate."""

    if not result.ranked:
        return None

    item = result.ranked[0]
    values = [float(item.requirement_scores.get(key, 0.0) or 0.0) for key, _ in RADAR_AXES]
    values = [max(0.0, min(1.0, v)) for v in values]

    width, height = 1120, 620
    cx, cy, radius = 560, 320, 230

    def point(axis_idx: int, ratio: float):
        angle = -math.pi / 2 + axis_idx * 2 * math.pi / len(RADAR_AXES)
        return cx + math.cos(angle) * radius * ratio, cy + math.sin(angle) * radius * ratio

    grid_lines: List[str] = []
    for level in range(1, 6):
        ratio = level / 5
        pts = [point(i, ratio) for i in range(len(RADAR_AXES))]
        grid_lines.append(
            '<polygon points="{}" fill="none" stroke="#d8e3ee" stroke-width="2"/>'.format(
                " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            )
        )

    axes: List[str] = []
    label_nodes: List[str] = []
    for idx, (_, label) in enumerate(RADAR_AXES):
        x, y = point(idx, 1.0)
        lx, ly = point(idx, 1.22)
        axes.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#e3ebf4" stroke-width="2"/>')
        anchor = "middle"
        if lx > cx + 60:
            anchor = "start"
        elif lx < cx - 60:
            anchor = "end"
        score = int(round(values[idx] * 10))
        label_nodes.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'font-size="28" fill="#2f3a4a" font-family="Arial, sans-serif">{html.escape(label)}</text>'
        )
        bx, by = point(idx, 1.04)
        label_nodes.append(
            f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="24" fill="#111827"/>'
            f'<text x="{bx:.1f}" y="{by + 9:.1f}" text-anchor="middle" '
            f'font-size="26" font-weight="700" fill="#ffffff" font-family="Arial, sans-serif">{score}</text>'
        )

    data_pts = [point(i, values[i]) for i in range(len(values))]
    data_polygon = " ".join(f"{x:.1f},{y:.1f}" for x, y in data_pts)

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#f7fbff"/>
  <g opacity="0.98">
    {"".join(grid_lines)}
    {"".join(axes)}
    <polygon points="{data_polygon}" fill="#6b7280" fill-opacity="0.34" stroke="#1f2937" stroke-width="7" stroke-linejoin="round"/>
    {"".join(label_nodes)}
  </g>
</svg>
'''
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")
    return str(path)


def _wrap_svg_text(text: str, max_chars: int) -> List[str]:
    text = str(text or "").strip()
    if not text:
        return []
    chunks: List[str] = []
    current = ""
    parts = re.split(r"(\s+|、|，|；|/)", text)
    parts = [part for part in parts if part and not part.isspace()]
    for part in parts:
        trial = (current + part).strip()
        if len(trial) > max_chars and current:
            chunks.append(current.strip())
            current = part
        else:
            current = trial
    if current:
        chunks.append(current.strip())
    return chunks[:3]


def build_filament_optimization_svg(result: FilamentSelectionResult, out_path: str) -> Optional[str]:
    """Create a compact visual map for modification directions."""

    rows = build_optimization_plan(result)
    if not rows:
        return None

    width = 1120
    card_h = 124
    top = 50
    gap = 18
    height = top * 2 + len(rows[:4]) * card_h + (len(rows[:4]) - 1) * gap
    palette = ["#dbeafe", "#dcfce7", "#fef3c7", "#ede9fe"]
    stroke_palette = ["#3b82f6", "#22c55e", "#f59e0b", "#8b5cf6"]

    cards: List[str] = []
    for idx, row in enumerate(rows[:4], start=1):
        y = top + (idx - 1) * (card_h + gap)
        fill = palette[(idx - 1) % len(palette)]
        stroke = stroke_palette[(idx - 1) % len(stroke_palette)]
        issue = html.escape(str(row.get("issue") or "待优化项"))
        strategy = _wrap_svg_text(str(row.get("strategy") or ""), 28)
        materials = _wrap_svg_text(str(row.get("materials") or ""), 36)
        caution = _wrap_svg_text(str(row.get("caution") or ""), 34)
        cards.append(f'<rect x="54" y="{y}" width="1012" height="{card_h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        cards.append(f'<circle cx="98" cy="{y + 42}" r="24" fill="{stroke}"/>')
        cards.append(f'<text x="98" y="{y + 51}" text-anchor="middle" font-size="24" font-weight="700" fill="#ffffff" font-family="Arial, sans-serif">{idx}</text>')
        cards.append(f'<text x="136" y="{y + 36}" font-size="24" font-weight="700" fill="#1f2937" font-family="Arial, sans-serif">{issue}</text>')
        for line_idx, line in enumerate(strategy):
            cards.append(f'<text x="136" y="{y + 70 + line_idx * 24}" font-size="19" fill="#334155" font-family="Arial, sans-serif">{html.escape(line)}</text>')
        cards.append(f'<text x="510" y="{y + 42}" font-size="19" font-weight="700" fill="#1f2937" font-family="Arial, sans-serif">可选体系</text>')
        for line_idx, line in enumerate(materials):
            cards.append(f'<text x="510" y="{y + 72 + line_idx * 22}" font-size="18" fill="#334155" font-family="Arial, sans-serif">{html.escape(line)}</text>')
        cards.append(f'<text x="795" y="{y + 42}" font-size="19" font-weight="700" fill="#1f2937" font-family="Arial, sans-serif">注意</text>')
        for line_idx, line in enumerate(caution):
            cards.append(f'<text x="795" y="{y + 72 + line_idx * 22}" font-size="18" fill="#334155" font-family="Arial, sans-serif">{html.escape(line)}</text>')

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#f8fbff"/>
  {"".join(cards)}
</svg>
'''
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")
    return str(path)


async def send_filament_visual_assets(websocket, repo_root: str, taskid: str, result: FilamentSelectionResult) -> Dict[str, Any]:
    """Generate, upload and announce visual assets for the frontend."""

    taskid_s = str(taskid).replace("/", "_")
    out_dir = Path(repo_root) / "src" / "MNS_CaseHub" / "cases" / "material_discovery_demo" / "results" / "filament_selection" / taskid_s / "assets"
    svg_path = build_filament_radar_svg(result, str(out_dir / "filament_radar.svg"))
    if not svg_path:
        return {}

    try:
        from src.storage_utils import get_image_url, oss_upload

        result_payload: Dict[str, Any] = {}
        for local_path, docs, key_name in (
            (svg_path, "材料性能判读图", "radar"),
        ):
            if not local_path:
                continue
            payload = Path(local_path).read_bytes()
            oss_key = f"XIMUAlpha_MNS/{taskid_s}/filament_selection/assets/{Path(local_path).name}"
            resp = await oss_upload("alpha", oss_key, payload)
            if resp.get("status") != 200:
                logger.warning(f"[FILAMENT_VISUAL] upload failed resp={resp}")
                result_payload[f"{key_name}_svg"] = local_path
                continue
            url = get_image_url("alpha", oss_key)
            await websocket.send_json({
                "step_id": "MATERIAL_SCREENING",
                "name": Path(local_path).name,
                "docs": docs,
                "url": url,
                "type": "MaterialsSVG",
            })
            result_payload[f"{key_name}_svg"] = local_path
            result_payload[f"{key_name}_url"] = url
        return result_payload
    except Exception as exc:
        logger.exception(f"[FILAMENT_VISUAL] failed: {exc!s}")
        payload = {}
        if svg_path:
            payload["radar_svg"] = svg_path
        return payload
