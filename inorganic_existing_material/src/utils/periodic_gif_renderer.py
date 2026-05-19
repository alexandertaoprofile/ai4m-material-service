#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(
    "/data/se42/alpha_project/alpha_material_sync/inorganic_existing_material/"
    "src/MNS_CaseHub/cases/material_discovery_demo"
)
TEMPLATE = ROOT / "public/gif_templates/periodic_template.png"
DEFAULT_OUT = ROOT / "results/gif_prototypes_periodic/periodic_elements_demo.gif"

ELEMENTS = [
    "H", "He",
    "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "In", "Sn", "Sb", "Te", "I", "Xe",
    "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy",
    "Ho", "Er", "Tm", "Yb", "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt",
    "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn",
    "Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf",
    "Es", "Fm", "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds",
    "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
]


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


F_TITLE = load_font(28, True)
F_BODY = load_font(22)
F_SMALL = load_font(18)
F_CHIP = load_font(21, True)


def parse_formula_elements(formulas: list[str]) -> list[str]:
    found: list[str] = []
    for formula in formulas:
        for symbol in re.findall(r"([A-Z][a-z]?)", str(formula or "")):
            if symbol in ELEMENTS and symbol not in found:
                found.append(symbol)
    return found


def parse_selected_json(selected_json: Path) -> tuple[list[str], str]:
    data = json.loads(selected_json.read_text())
    items = data.get("items") if isinstance(data, dict) else []
    top = items[0] if isinstance(items, list) and items else {}
    formula = ""
    elements: list[str] = []
    if isinstance(top, dict):
        formula = str(top.get("formula_pretty") or "")
        raw_elements = top.get("elements")
        if isinstance(raw_elements, list):
            elements = [str(x) for x in raw_elements if str(x) in ELEMENTS]
    if not formula and isinstance(data, dict):
        formula = str(data.get("formula") or "")
    if not elements:
        elements = parse_formula_elements([formula])
    return elements, formula


def element_positions() -> dict[str, tuple[int, int, int, int]]:
    # Coordinates are calibrated for public/gif_templates/periodic_template.png (1672x941).
    x_by_col = {
        1: 52, 2: 138, 3: 224, 4: 310, 5: 394, 6: 480, 7: 564, 8: 650, 9: 734,
        10: 818, 11: 902, 12: 986, 13: 1074, 14: 1166, 15: 1262, 16: 1356,
        17: 1448, 18: 1542,
    }
    w_by_col = {
        1: 76, 2: 78, 3: 76, 4: 74, 5: 76, 6: 76, 7: 78, 8: 76, 9: 76,
        10: 74, 11: 74, 12: 74, 13: 82, 14: 86, 15: 84, 16: 82, 17: 82, 18: 78,
    }
    y_by_row = {1: 116, 2: 200, 3: 286, 4: 372, 5: 456, 6: 538, 7: 622}
    h_by_row = {1: 76, 2: 74, 3: 74, 4: 74, 5: 72, 6: 72, 7: 72}
    positions: dict[str, tuple[int, int, int, int]] = {}

    def put(symbol: str, col: int, row: int) -> None:
        x = x_by_col[col]
        y = y_by_row[row]
        positions[symbol] = (x, y, x + w_by_col[col], y + h_by_row[row])

    # Main table groups.
    rows = {
        1: {1: "H", 18: "He"},
        2: {1: "Li", 2: "Be", 13: "B", 14: "C", 15: "N", 16: "O", 17: "F", 18: "Ne"},
        3: {1: "Na", 2: "Mg", 13: "Al", 14: "Si", 15: "P", 16: "S", 17: "Cl", 18: "Ar"},
        4: {
            1: "K", 2: "Ca", 3: "Sc", 4: "Ti", 5: "V", 6: "Cr", 7: "Mn", 8: "Fe",
            9: "Co", 10: "Ni", 11: "Cu", 12: "Zn", 13: "Ga", 14: "Ge", 15: "As",
            16: "Se", 17: "Br", 18: "Kr",
        },
        5: {
            1: "Rb", 2: "Sr", 3: "Y", 4: "Zr", 5: "Nb", 6: "Mo", 7: "Tc", 8: "Ru",
            9: "Rh", 10: "Pd", 11: "Ag", 12: "Cd", 13: "In", 14: "Sn", 15: "Sb",
            16: "Te", 17: "I", 18: "Xe",
        },
        6: {
            1: "Cs", 2: "Ba", 4: "Hf", 5: "Ta", 6: "W", 7: "Re", 8: "Os", 9: "Ir",
            10: "Pt", 11: "Au", 12: "Hg", 13: "Tl", 14: "Pb", 15: "Bi", 16: "Po",
            17: "At", 18: "Rn",
        },
        7: {
            1: "Fr", 2: "Ra", 4: "Rf", 5: "Db", 6: "Sg", 7: "Bh", 8: "Hs", 9: "Mt",
            10: "Ds", 11: "Rg", 12: "Cn", 13: "Nh", 14: "Fl", 15: "Mc", 16: "Lv",
            17: "Ts", 18: "Og",
        },
    }
    for row, cols in rows.items():
        for col, symbol in cols.items():
            put(symbol, col, row)

    # Lanthanides and actinides.
    f_x0, f_y0 = 224, 722
    f_dx, f_dy = 86, 80
    f_w, f_h = 76, 70
    for idx, symbol in enumerate(["La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu"]):
        x = f_x0 + idx * f_dx
        positions[symbol] = (x, f_y0, x + f_w, f_y0 + f_h)
    for idx, symbol in enumerate(["Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr"]):
        x = f_x0 + idx * f_dx
        y = f_y0 + f_dy
        positions[symbol] = (x, y, x + f_w, y + f_h)

    return positions


def draw_highlight(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], alpha: int, pulse: float) -> None:
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    radius = max(x1 - x0, y1 - y0) // 2 + 8 + int(3 * pulse)
    green = (171, 226, 41)
    blue = (42, 112, 226)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=(*green, alpha), width=5)
    draw.rounded_rectangle((x0 - 5, y0 - 5, x1 + 5, y1 + 5), radius=12, outline=(*blue, alpha), width=3)


def draw_summary(draw: ImageDraw.ImageDraw, elements: list[str], formula: str, active_count: int, alpha: int) -> None:
    # The template already has a rounded instruction panel here. Fill its interior
    # only, preserving the original border so we do not create a misaligned double box.
    panel = (324, 185, 1012, 358)
    draw.rounded_rectangle(panel, radius=16, fill=(255, 255, 255, 255), outline=None)
    draw.text((panel[0] + 28, panel[1] + 26), "Element-aware screening", font=F_TITLE, fill=(20, 45, 86, alpha))
    subtitle = f"Formula: {formula}" if formula else "Formula-driven element parsing"
    draw.text((panel[0] + 28, panel[1] + 64), subtitle, font=F_SMALL, fill=(74, 91, 120, alpha))

    chip_x = panel[0] + 28
    chip_y = panel[1] + 108
    for idx, symbol in enumerate(elements):
        selected = idx < active_count
        chip_w, chip_h = 58, 42
        fill = (190, 229, 44, 255) if selected else (235, 240, 248, 255)
        outline = (91, 161, 74, alpha) if selected else (205, 215, 228, alpha)
        chip_box = (chip_x, chip_y, chip_x + chip_w, chip_y + chip_h)
        draw.rounded_rectangle(chip_box, radius=10, fill=fill, outline=outline, width=2)
        text_box = draw.textbbox((0, 0), symbol, font=F_CHIP)
        tw = text_box[2] - text_box[0]
        th = text_box[3] - text_box[1]
        tx = chip_x + (chip_w - tw) / 2 - text_box[0]
        ty = chip_y + (chip_h - th) / 2 - text_box[1] - 1
        draw.text((tx, ty), symbol, font=F_CHIP, fill=(24, 45, 68, alpha))
        chip_x += 68


def build_gif(elements: list[str], formula: str, out_gif: Path) -> None:
    template = Image.open(TEMPLATE).convert("RGBA")
    positions = element_positions()
    elements = [e for e in elements if e in positions]
    if not elements:
        elements = parse_formula_elements([formula])

    frames: list[Image.Image] = []
    frame_count = 28 + 8 * max(1, len(elements))

    for i in range(frame_count):
        t = i / max(1, frame_count - 1)
        img = template.copy()
        overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        intro_alpha = int(255 * min(1.0, t / 0.15))
        per_element = max(1, (frame_count - 12) // max(1, len(elements)))
        active_count = min(len(elements), max(0, (i - 8) // per_element + 1))

        for idx, symbol in enumerate(elements[:active_count]):
            box = positions.get(symbol)
            if not box:
                continue
            local_phase = ((i - 8 - idx * per_element) % per_element) / max(1, per_element)
            pulse = math.sin(local_phase * math.pi)
            alpha = 235 if idx == active_count - 1 else 185
            draw_highlight(draw, box, alpha, pulse)

        draw_summary(draw, elements, formula, active_count, intro_alpha)
        img = Image.alpha_composite(img, overlay).convert("RGB")
        frames.append(img)

    out_gif.parent.mkdir(parents=True, exist_ok=True)
    # Let Pillow quantize from RGB frames at save time; this avoids the noisy per-frame
    # palette conversion artifacts that made gray element tiles look speckled.
    frames[0].save(
        out_gif,
        save_all=True,
        append_images=frames[1:],
        duration=130,
        loop=0,
        optimize=True,
        disposal=2,
    )
    frames[-1].save(out_gif.with_name(out_gif.stem + "_final.jpg"), quality=95)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a formula-driven periodic table GIF.")
    parser.add_argument("--formula", action="append", default=[])
    parser.add_argument("--selected-json", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    formula = ", ".join(args.formula)
    elements = parse_formula_elements(args.formula)
    if args.selected_json:
        elements, json_formula = parse_selected_json(args.selected_json)
        formula = formula or json_formula

    build_gif(elements, formula, args.out)
    print(args.out)
    print(args.out.with_name(args.out.stem + "_final.jpg"))


if __name__ == "__main__":
    main()
