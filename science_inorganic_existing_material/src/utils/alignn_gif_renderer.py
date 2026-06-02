#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(
    "/data/se42/alpha_project/alpha_material_sync/inorganic_existing_material/"
    "src/MNS_CaseHub/cases/material_discovery_demo"
)
TEMPLATE = ROOT / "public/gif_templates/alignn_template.png"
DEFAULT_JSON = (
    ROOT
    / "results/mp/conv_1778749023_8259_35gINHo3gVgGYmMW7c6EG/SiO2/selected_structures.json"
)
DEFAULT_OUT = ROOT / "results/gif_prototypes_v12/alignn_json_driven_sio2_v12.gif"


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for font_path in candidates:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size)
    return ImageFont.load_default()


F_TITLE = load_font(26, True)
F_CARD_TITLE = load_font(21, True)
F_BODY = load_font(17)
F_BODY_BOLD = load_font(17, True)
F_TINY_VALUE = load_font(13, True)
F_SMALL = load_font(15)
F_VALUE = load_font(16, True)
F_NOTICE = load_font(21, True)


def fmt_num(value: Any, digits: int = 4, suffix: str = "") -> str:
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{suffix}"
    return "N/A"


def parse_data(selected_json: Path) -> dict[str, Any]:
    raw = json.loads(selected_json.read_text())
    items = raw.get("items") or []
    top = items[0] if items else {}
    cards = raw.get("candidate_cards") or []
    card = cards[0] if cards else {}

    symmetry = top.get("symmetry") if isinstance(top.get("symmetry"), dict) else {}
    lattice = card.get("lattice") if isinstance(card.get("lattice"), dict) else {}

    return {
        "formula": top.get("formula_pretty") or raw.get("formula") or "N/A",
        "material_id": top.get("material_id") or raw.get("material_id") or "N/A",
        "crystal_system": symmetry.get("crystal_system") or "N/A",
        "spacegroup_symbol": symmetry.get("symbol") or "N/A",
        "spacegroup_number": symmetry.get("number")
        if symmetry.get("number") is not None
        else "N/A",
        "symmetry_full": card.get("symmetry") or "N/A",
        "lattice": lattice,
        "atoms": lattice.get("atoms")
        if isinstance(lattice.get("atoms"), (int, float))
        else top.get("nsites", "N/A"),
        "band_gap": top.get("band_gap"),
        "e_hull": top.get("energy_above_hull"),
        "e_form": top.get("formation_energy_per_atom"),
        "density": top.get("density"),
        "is_stable": top.get("is_stable"),
        "image_path": card.get("image_path_abs") or card.get("image_path") or "",
        "cif_path": top.get("cif_path_abs") or top.get("cif_path") or "",
        "json_path": str(selected_json),
    }


def fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "..."
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return text + ellipsis if text else ellipsis


def rounded_card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    color: tuple[int, int, int],
    alpha: float,
) -> None:
    x0, y0, x1, y1 = box
    a = int(255 * alpha)
    draw.rounded_rectangle(
        box,
        radius=16,
        fill=(250, 252, 255, a),
        outline=(*color, min(210, a)),
        width=2,
    )
    draw.text((x0 + 24, y0 + 22), title, font=F_CARD_TITLE, fill=(*color, a))


def paste_structure_thumb(
    canvas: Image.Image,
    image_path: str,
    box: tuple[int, int, int, int],
    alpha: float,
) -> None:
    x0, y0, x1, y1 = box
    bg = Image.new("RGBA", (x1 - x0, y1 - y0), (244, 248, 253, int(255 * alpha)))
    ImageDraw.Draw(bg).rounded_rectangle(
        (0, 0, x1 - x0 - 1, y1 - y0 - 1),
        radius=12,
        outline=(205, 218, 238, int(230 * alpha)),
        width=2,
    )
    canvas.alpha_composite(bg, (x0, y0))

    path = Path(image_path) if image_path else None
    if not path or not path.exists():
        return

    src = Image.open(path).convert("RGBA")
    # Candidate card contains a clean structure panel in the upper half.
    crop = src.crop((80, 70, src.width - 80, min(src.height, 560)))
    crop.thumbnail((x1 - x0 - 20, y1 - y0 - 20), Image.Resampling.LANCZOS)
    crop.putalpha(int(255 * alpha))
    px = x0 + (x1 - x0 - crop.width) // 2
    py = y0 + (y1 - y0 - crop.height) // 2
    canvas.alpha_composite(crop, (px, py))


def draw_key_values(
    draw: ImageDraw.ImageDraw,
    rows: list[tuple[str, str, tuple[int, int, int] | None]],
    origin: tuple[int, int],
    key_width: int,
    line_height: int,
    alpha: float,
    max_value_width: int,
) -> None:
    x, y = origin
    a = int(255 * alpha)
    for idx, (key, value, color) in enumerate(rows):
        yy = y + idx * line_height
        draw.text((x, yy), key, font=F_BODY, fill=(38, 52, 82, a))
        value_font = F_TINY_VALUE if draw.textlength(value, font=F_VALUE) > max_value_width else F_VALUE
        val = fit_text(draw, value, value_font, max_value_width)
        draw.text(
            (x + key_width, yy),
            val,
            font=value_font,
            fill=(*(color or (34, 84, 185)), a),
        )


def draw_panel_base(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    alpha: float,
    active: bool,
) -> None:
    x0, y0, x1, y1 = box
    a = int(255 * alpha)
    border = (195, 214, 235, a) if active else (215, 224, 236, int(210 * alpha))
    draw.rounded_rectangle(box, radius=10, fill=(255, 255, 255, int(235 * alpha)), outline=border, width=1)


def draw_cif_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    alpha: float,
    phase: float,
    active: bool,
) -> None:
    draw_panel_base(draw, box, alpha, active)
    x0, y0, x1, y1 = box
    a = int(255 * alpha)
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    front = [(cx - 34, cy - 16), (cx + 20, cy - 16), (cx + 20, cy + 38), (cx - 34, cy + 38)]
    back = [(x + 22, y - 22) for x, y in front]
    for shape in (front, back):
        draw.line(shape + [shape[0]], fill=(105, 143, 191, int(190 * alpha)), width=2)
    for p, q in zip(front, back):
        draw.line((p, q), fill=(105, 143, 191, int(160 * alpha)), width=2)
    atoms = [
        (front[0], (42, 112, 214), 7),
        (front[1], (220, 49, 54), 7),
        (front[2], (42, 112, 214), 7),
        (back[0], (220, 49, 54), 6),
        (back[2], (220, 49, 54), 6),
    ]
    for (px, py), color, r in atoms:
        glow = int((80 + 80 * phase) * alpha)
        draw.ellipse((px - r - 4, py - r - 4, px + r + 4, py + r + 4), fill=(*color, glow))
        draw.ellipse((px - r, py - r, px + r, py + r), fill=(*color, a))
    draw.text((x0 + 10, y0 + 8), ".cif", font=F_SMALL, fill=(74, 101, 139, int(220 * alpha)))


def draw_atom_graph_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    alpha: float,
    phase: float,
    active: bool,
) -> None:
    draw_panel_base(draw, box, alpha, active)
    x0, y0, x1, y1 = box
    a = int(255 * alpha)
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    pts = [
        (cx - 38, cy + 26),
        (cx - 10, cy - 10),
        (cx + 38, cy - 28),
        (cx + 30, cy + 28),
    ]
    edges = [(0, 1), (1, 2), (1, 3)]
    for i, j in edges:
        draw.line((pts[i], pts[j]), fill=(120, 143, 170, int(190 * alpha)), width=2)
    colors = [(220, 49, 54), (42, 112, 214), (220, 49, 54), (220, 49, 54)]
    for idx, (px, py) in enumerate(pts):
        r = 8 if idx else 7
        draw.ellipse((px - r, py - r, px + r, py + r), fill=(*colors[idx], a))

    if phase >= 0.35:
        arc_a = int(210 * alpha)
        draw.arc((cx - 26, cy - 36, cx + 42, cy + 36), 205, 15, fill=(49, 154, 69, arc_a), width=2)
        draw.arc((cx - 50, cy - 24, cx + 12, cy + 44), 300, 110, fill=(49, 154, 69, arc_a), width=2)


def draw_line_graph_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    alpha: float,
    phase: float,
    active: bool,
) -> None:
    draw_panel_base(draw, box, alpha, active)
    x0, y0, x1, y1 = box
    a = int(255 * alpha)
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    edge_nodes = [
        (cx - 38, cy + 22),
        (cx - 8, cy - 18),
        (cx + 36, cy - 10),
        (cx + 12, cy + 34),
    ]
    line_edges = [(0, 1), (1, 2), (1, 3), (2, 3)]
    for i, j in line_edges:
        draw.line((edge_nodes[i], edge_nodes[j]), fill=(95, 155, 112, int(190 * alpha)), width=2)
    for idx, (px, py) in enumerate(edge_nodes):
        color = (65, 173, 91) if idx != 1 else (42, 112, 214)
        glow = int((65 + 105 * phase) * alpha)
        draw.ellipse((px - 14, py - 14, px + 14, py + 14), fill=(*color, glow))
        draw.ellipse((px - 7, py - 7, px + 7, py + 7), fill=(*color, a))
    draw.arc((cx - 30, cy - 42, cx + 48, cy + 34), 210, 35, fill=(49, 154, 69, int(200 * alpha)), width=2)
    draw.arc((cx - 52, cy - 24, cx + 12, cy + 50), 300, 120, fill=(49, 154, 69, int(190 * alpha)), width=2)


def draw_runtime_card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    t: float,
    alpha: float,
) -> None:
    x0, y0, x1, y1 = box
    local_t = max(0.0, min(1.0, (t - 0.48) / 0.32))
    panels = [
        ((x0 + 28, y0 + 68, x0 + 158, y0 + 160), "CIF parsed", draw_cif_panel),
        ((x0 + 200, y0 + 68, x0 + 330, y0 + 160), "Atom graph built", draw_atom_graph_panel),
        ((x0 + 372, y0 + 68, x0 + 502, y0 + 160), "Line graph encoded", draw_line_graph_panel),
    ]
    for idx, (panel, label, renderer) in enumerate(panels):
        phase = max(0.0, min(1.0, local_t * 3 - idx))
        active = phase > 0.05
        renderer(draw, panel, alpha, phase, active)
        caption_color = (35, 95, 53) if active else (110, 126, 138)
        draw.text((panel[0] + 10, panel[3] + 10), label, font=F_SMALL, fill=(*caption_color, int(255 * alpha)))
        if idx < 2:
            ax = panel[2] + 20
            ay = (panel[1] + panel[3]) // 2
            arrow_alpha = int((255 if local_t > (idx + 0.55) / 3 else 120) * alpha)
            draw.line((ax, ay, ax + 24, ay), fill=(41, 113, 205, arrow_alpha), width=2)
            draw.polygon(
                [(ax + 24, ay), (ax + 16, ay - 6), (ax + 16, ay + 6)],
                fill=(41, 113, 205, arrow_alpha),
            )

    badge_alpha = int(255 * alpha if local_t > 0.84 else 105 * alpha)
    badge = (x0 + 310, y1 - 42, x1 - 28, y1 - 18)
    draw.rounded_rectangle(
        badge,
        radius=12,
        fill=(229, 247, 235, badge_alpha),
        outline=(76, 174, 100, badge_alpha),
        width=1,
    )
    draw.text(
        (badge[0] + 16, badge[1] + 3),
        "Prediction completed",
        font=F_SMALL,
        fill=(40, 132, 64, badge_alpha),
    )

    gx0, gx1, gy = x0 + 28, x0 + 284, y1 - 30
    draw.line((gx0, gy, gx1, gy), fill=(180, 218, 190, int(210 * alpha)), width=3)
    for idx in range(7):
        x = int(gx0 + (gx1 - gx0) * idx / 6)
        pulse = math.sin(((t * 2.0 + idx * 0.13) % 1.0) * math.pi)
        r = 4 + int(5 * pulse)
        draw.ellipse((x - r, gy - r, x + r, gy + r), fill=(43, 173, 88, int((85 + 150 * pulse) * alpha)))


def build_gif(selected_json: Path, out_gif: Path) -> None:
    data = parse_data(selected_json)
    template = Image.open(TEMPLATE).convert("RGBA")
    width, height = template.size

    frames: list[Image.Image] = []
    frame_count = 48
    top_h = 420
    card_y0 = 565
    left = (28, card_y0, 525, 812)
    middle = (558, card_y0, 1088, 812)
    right = (1120, card_y0, 1644, 812)
    notice = (28, 835, 1644, 918)

    for frame_idx in range(frame_count):
        t = frame_idx / (frame_count - 1)
        top_alpha = min(1.0, t / 0.18)
        img = Image.new("RGBA", (width, height), (247, 250, 254, 255))

        top_src = template.crop((0, 0, width, 590))
        top_scale = top_h / top_src.height
        top = top_src.resize((int(top_src.width * top_scale), top_h), Image.Resampling.LANCZOS)
        top.putalpha(int(255 * top_alpha))
        img.alpha_composite(top, ((width - top.width) // 2, 0))

        overlay = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        # Clean lower data region with stable whitespace.
        draw.rounded_rectangle(
            (14, top_h + 2, width - 14, height - 14),
            radius=18,
            fill=(255, 255, 255, 255),
            outline=(220, 230, 244, 255),
            width=1,
        )

        step_t = max(0.0, min(1.0, (t - 0.15) / 0.22))
        x_points = [110, 520, 900, 1325]
        y_step = 482
        draw.line((x_points[0], y_step, x_points[-1] + 250, y_step), fill=(202, 214, 233, 240), width=4)
        progress_x = int(x_points[0] + (x_points[-1] + 250 - x_points[0]) * step_t)
        draw.line((x_points[0], y_step, progress_x, y_step), fill=(46, 140, 226, 255), width=5)
        labels = ["Structure Input", "Graph Construction", "Message Passing", "Property Prediction"]
        for idx, x in enumerate(x_points):
            active = step_t >= idx / 3 if idx else step_t > 0
            color = (36, 112, 226) if idx < 2 else (34, 157, 184) if idx == 2 else (58, 177, 82)
            dot = color if active else (174, 188, 208)
            draw.ellipse((x - 17, y_step - 17, x + 17, y_step + 17), fill=(*dot, 255))
            draw.text((x - 6, y_step - 14), str(idx + 1), font=F_BODY_BOLD, fill=(255, 255, 255, 255))
            draw.text((x + 28, y_step + 18), labels[idx], font=F_BODY_BOLD, fill=(18, 48, 105, 255))

        left_alpha = max(0.0, min(1.0, (t - 0.34) / 0.10))
        if left_alpha:
            rounded_card(draw, left, "Structure Input", (32, 92, 196), left_alpha)
            lx0, ly0, _, _ = left
            paste_structure_thumb(overlay, data["image_path"], (lx0 + 22, ly0 + 66, lx0 + 174, ly0 + 218), left_alpha)
            lattice = data["lattice"]
            crystal = f"{data['crystal_system']} / {data['spacegroup_symbol']} / {data['spacegroup_number']}"
            rows = [
                ("Formula", data["formula"], None),
                ("Material ID", data["material_id"], None),
                (
                    "Crystal",
                    crystal,
                    None,
                ),
                (
                    "Lattice",
                    f"a=b=c={fmt_num(lattice.get('a') if isinstance(lattice, dict) else None, 4)} A",
                    None,
                ),
                (
                    "Angles",
                    f"alpha=beta=gamma={fmt_num(lattice.get('alpha') if isinstance(lattice, dict) else None, 1)} deg",
                    None,
                ),
                ("Atoms", str(data["atoms"]), None),
            ]
            draw_key_values(draw, rows, (lx0 + 202, ly0 + 68), 104, 31, left_alpha, 175)

        middle_alpha = max(0.0, min(1.0, (t - 0.46) / 0.10))
        if middle_alpha:
            rounded_card(draw, middle, "Message Passing", (42, 146, 72), middle_alpha)
            draw_runtime_card(draw, middle, t, middle_alpha)

        right_alpha = max(0.0, min(1.0, (t - 0.60) / 0.10))
        if right_alpha:
            rounded_card(draw, right, "Property Prediction", (96, 65, 210), right_alpha)
            rx0, ry0, _, _ = right
            rows = [
                ("Band gap", fmt_num(data["band_gap"], 4, " eV"), (92, 62, 212)),
                ("E_hull", fmt_num(data["e_hull"], 4, " eV/atom"), (92, 62, 212)),
                ("Formation energy", fmt_num(data["e_form"], 4, " eV/atom"), (92, 62, 212)),
                ("Density", fmt_num(data["density"], 4, " g/cm^3"), (92, 62, 212)),
                (
                    "Stable",
                    "Yes" if data["is_stable"] is True else ("No" if data["is_stable"] is False else "N/A"),
                    (224, 50, 66) if data["is_stable"] is False else (45, 145, 78),
                ),
                ("Symmetry", data["symmetry_full"], (92, 62, 212)),
            ]
            reveal = max(0.0, min(1.0, (t - 0.68) / 0.24))
            visible = int(math.ceil(reveal * len(rows)))
            draw_key_values(draw, rows[:visible], (rx0 + 26, ry0 + 70), 174, 31, right_alpha, 290)

        notice_alpha = max(0.0, min(1.0, (t - 0.82) / 0.10))
        if notice_alpha:
            a = int(255 * notice_alpha)
            draw.rounded_rectangle(
                notice,
                radius=14,
                fill=(245, 249, 255, a),
                outline=(204, 219, 240, a),
                width=2,
            )
            nx0, ny0, _, _ = notice
            draw.text(
                (nx0 + 34, ny0 + 16),
                "All results are read from input JSON. No random values. No placeholders.",
                font=F_NOTICE,
                fill=(32, 61, 122, a),
            )
            draw.text(
                (nx0 + 34, ny0 + 48),
                fit_text(draw, f"Source: {data['json_path']}", F_SMALL, 1500),
                font=F_SMALL,
                fill=(82, 104, 140, a),
            )

        img = Image.alpha_composite(img, overlay)
        frames.append(img.convert("P", palette=Image.Palette.ADAPTIVE, colors=256))

    out_gif.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        out_gif,
        save_all=True,
        append_images=frames[1:],
        duration=115,
        loop=0,
        optimize=False,
        disposal=2,
    )

    first = out_gif.with_name(out_gif.stem + "_first.jpg")
    final = out_gif.with_name(out_gif.stem + "_final.jpg")
    frames[0].convert("RGB").save(first, quality=92)
    frames[-1].convert("RGB").save(final, quality=92)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate JSON-driven ALIGNN GIF.")
    parser.add_argument("--selected-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    build_gif(args.selected_json, args.out)
    print(args.out)
    print(args.out.with_name(args.out.stem + "_first.jpg"))
    print(args.out.with_name(args.out.stem + "_final.jpg"))


if __name__ == "__main__":
    main()
