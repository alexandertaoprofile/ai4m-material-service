#!/usr/bin/env python3
import argparse
import json
import math
import os
from typing import Dict, Tuple, List, Optional, Iterable, Set

import numpy as np
import trimesh

from pymatgen.core import Structure
from pymatgen.io.cif import CifParser
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from pymatgen.analysis.local_env import CrystalNN


# ---------- Colors (RGB 0-255) ----------
# 尽量贴近你截图里的观感：Li浅绿、P淡紫、S黄、Cl亮绿；键用白/浅灰
ELEMENT_COLORS: Dict[str, Tuple[int, int, int]] = {
    "H": (255, 255, 255),
    "C": (60, 60, 60),
    "N": (0, 0, 255),
    "O": (255, 0, 0),

    "F":  (0, 255, 0),
    "Cl": (0, 255, 0),
    "Br": (165, 42, 42),
    "I":  (148, 0, 211),

    "S":  (255, 255, 0),
    "P":  (190, 150, 210),   # 更接近 MP 里 P5+ 那种淡紫
    "Li": (170, 235, 170),   # 更接近 MP 里 Li+ 那种浅绿

    "Na": (171, 92, 242),
    "K":  (143, 64, 212),
    "Mg": (138, 255, 0),
    "Al": (200, 200, 200),
    "Si": (240, 200, 160),
    "Ca": (61, 255, 0),
    "Fe": (224, 102, 51),
    "Ni": (80, 208, 80),
    "Co": (240, 144, 160),
    "Cu": (200, 128, 51),
    "Zn": (125, 128, 176),
    "W":  (80, 80, 100),
}
DEFAULT_COLOR = (160, 160, 160)

# 典型阴离子（用于 polyhedra 顶点过滤）
TYPICAL_ANIONS: Set[str] = {"O", "S", "Se", "Te", "F", "Cl", "Br", "I", "N"}


# ---------- Helpers ----------
def element_rgb(el: str) -> Tuple[int, int, int]:
    return ELEMENT_COLORS.get(el, DEFAULT_COLOR)

def rgba01(rgba255: Tuple[int, int, int, int]) -> Tuple[float, float, float, float]:
    r, g, b, a = rgba255
    return (r/255.0, g/255.0, b/255.0, a/255.0)

def load_structure(path: str) -> Structure:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".cif":
        parser = CifParser(path)
        return parser.get_structures(primitive=False)[0]
    if ext == ".json":
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and "structure" in d:
            d = d["structure"]
        return Structure.from_dict(d)
    raise ValueError(f"Unsupported input: {path} (use .cif or .json)")

def standardize_structure(structure: Structure) -> Structure:
    """
    尽量对齐 MP：conventional standardized cell。
    """
    try:
        sga = SpacegroupAnalyzer(structure, symprec=1e-3)
        return sga.get_conventional_standard_structure()
    except Exception:
        return structure

def center_shift_cart(structure: Structure) -> np.ndarray:
    """
    用晶胞几何中心 (0.5,0.5,0.5) 做居中，比 coords.mean 更“对称/稳”。
    """
    return structure.lattice.get_cartesian_coords([0.5, 0.5, 0.5])


# ---------- Materials / visuals ----------
def apply_pbr_material(mesh: trimesh.Trimesh,
                       rgba255: Tuple[int, int, int, int],
                       double_sided: bool = False) -> None:
    """
    强制 glTF PBR 材质，保证 three.js 里透明度能工作。
    """
    base = rgba01(rgba255)
    alpha = base[3]
    mat = trimesh.visual.material.PBRMaterial(
        baseColorFactor=base,
        metallicFactor=0.0,
        roughnessFactor=1.0,
        alphaMode=("BLEND" if alpha < 0.999 else "OPAQUE"),
        doubleSided=bool(double_sided),
    )
    # 用 face_colors 更稳定（vertex alpha 有时在导出链路里被吃掉）
    fc = np.tile(np.array(rgba255, dtype=np.uint8), (len(mesh.faces), 1))
    mesh.visual = trimesh.visual.ColorVisuals(mesh, face_colors=fc)
    mesh.visual.material = mat

def make_sphere(center: np.ndarray, radius: float, rgb: Tuple[int, int, int]) -> trimesh.Trimesh:
    m = trimesh.creation.icosphere(subdivisions=3, radius=float(radius))
    m.apply_translation(center)
    apply_pbr_material(m, (rgb[0], rgb[1], rgb[2], 255), double_sided=False)
    return m

def make_cylinder(p0: np.ndarray, p1: np.ndarray, radius: float, rgb: Tuple[int, int, int]) -> trimesh.Trimesh:
    v = p1 - p0
    h = float(np.linalg.norm(v))
    if h < 1e-8:
        return trimesh.Trimesh()

    cyl = trimesh.creation.cylinder(radius=float(radius), height=h, sections=20)

    z = np.array([0.0, 0.0, 1.0])
    vhat = v / h
    axis = np.cross(z, vhat)
    axis_n = float(np.linalg.norm(axis))
    if axis_n > 1e-8:
        axis = axis / axis_n
        angle = float(math.acos(np.clip(np.dot(z, vhat), -1.0, 1.0)))
        R = trimesh.transformations.rotation_matrix(angle, axis)
        cyl.apply_transform(R)
    else:
        if float(np.dot(z, vhat)) < 0:
            R = trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0])
            cyl.apply_transform(R)

    cyl.apply_translation((p0 + p1) / 2.0)
    apply_pbr_material(cyl, (rgb[0], rgb[1], rgb[2], 255), double_sided=False)
    return cyl


# ---------- Neighbors / polyhedra ----------
def get_crystalnn_bonds(structure: Structure,
                        max_per_site: Optional[int] = None) -> List[Tuple[int, int, np.ndarray]]:
    """
    PBC-aware bonds via CrystalNN.
    返回 (i, j, image)
    """
    cnn = CrystalNN()
    bonds: List[Tuple[int, int, np.ndarray]] = []
    seen = set()

    for i in range(len(structure)):
        nn_info = cnn.get_nn_info(structure, i)
        if max_per_site is not None:
            nn_info = nn_info[: int(max_per_site)]

        for nn in nn_info:
            j = int(nn["site_index"])
            img = np.array(nn["image"], dtype=int)

            # 去重：无向边 + image
            key = (min(i, j), max(i, j), int(img[0]), int(img[1]), int(img[2]))
            if key in seen:
                continue
            seen.add(key)
            bonds.append((i, j, img))

    return bonds


def pick_poly_centers_mp_like(structure: Structure, mode: str) -> List[str]:
    """
    mode:
      - "none": 不画 polyhedra
      - "p":    只画 P-centered（SSE 最直观：PS4）
      - "li":   只画 Li-centered
      - "mp":   MP-like：优先 P；如果没有 P 再退化
    """
    elems = {site.specie.symbol for site in structure}

    mode = mode.lower()
    if mode == "none":
        return []
    if mode == "p":
        return ["P"] if "P" in elems else []
    if mode == "li":
        return ["Li"] if "Li" in elems else []
    if mode == "mp":
        if "P" in elems:
            return ["P"]
        # fallback：一些常见网络形成中心
        for e in ["Si", "Al", "B", "Ge", "As"]:
            if e in elems:
                return [e]
        # 再 fallback：如果有 Li 就画 Li（但默认 mp 里我还是更倾向 P）
        if "Li" in elems:
            return ["Li"]
        return []
    # default:
    return []


def get_polyhedra(
    structure: Structure,
    centers: Iterable[str],
    cn_allowed: Set[int],
    max_neighbors: int,
    # 顶点过滤：比如 P 的顶点只要阴离子（S/Cl...）
    vertex_filter: Optional[Set[str]] = None,
) -> List[Tuple[np.ndarray, np.ndarray, str]]:
    """
    返回 (center_cart, vertices_cart[N,3], center_element)
    """
    centers = set(centers)
    cnn = CrystalNN()
    polys: List[Tuple[np.ndarray, np.ndarray, str]] = []

    for i, site in enumerate(structure):
        el = site.specie.symbol
        if el not in centers:
            continue

        nn_info = cnn.get_nn_info(structure, i)
        if len(nn_info) > max_neighbors:
            nn_info = nn_info[:max_neighbors]

        # 顶点元素过滤
        verts: List[np.ndarray] = []
        for nn in nn_info:
            j = int(nn["site_index"])
            img = np.array(nn["image"], dtype=int)
            v_site = structure[j]
            v_el = v_site.specie.symbol
            if (vertex_filter is not None) and (v_el not in vertex_filter):
                continue
            v = v_site.coords + img @ structure.lattice.matrix
            verts.append(v)

        cn = len(verts)
        if cn not in cn_allowed:
            continue

        if len(verts) >= 4:
            polys.append((site.coords.copy(), np.array(verts, dtype=float), el))

    return polys


# ---------- Export ----------
def export_glb_mpstyle(
    structure: Structure,
    out_glb: str,
    supercell: Tuple[int, int, int] = (1, 1, 1),
    # atoms/bonds
    atom_radius: float = 0.40,
    bond_radius: float = 0.07,
    draw_atoms: bool = True,
    draw_bonds: bool = True,
    bond_rgb: Tuple[int, int, int] = (245, 245, 245),  # 更接近你截图的白色键
    max_bonds_per_site: Optional[int] = None,
    # polyhedra
    poly_mode: str = "mp",              # mp / p / li / none
    poly_cn: Set[int] = frozenset({4}), # SSE 默认只画四面体最直观
    poly_alpha: int = 85,               # 0-255，越小越透明
) -> Dict:
    """
    目标：视觉尽量贴近 MP（清晰可读、对称感强、polyhedra 不乱）。
    """
    # 0) standardize
    structure = standardize_structure(structure)

    # 1) supercell
    sc = tuple(int(x) for x in supercell)
    if sc != (1, 1, 1):
        structure = structure.copy()
        structure.make_supercell(list(sc))

    # 2) shift to cell center (better symmetry)
    shift = center_shift_cart(structure)

    scene = trimesh.Scene()

    # atoms
    if draw_atoms:
        for site in structure:
            el = site.specie.symbol
            c = site.coords - shift
            scene.add_geometry(make_sphere(c, atom_radius, element_rgb(el)))

    # bonds
    bonds = []
    if draw_bonds:
        bonds = get_crystalnn_bonds(structure, max_per_site=max_bonds_per_site)
        for i, j, img in bonds:
            p0 = structure[i].coords - shift
            p1 = (structure[j].coords + img @ structure.lattice.matrix) - shift
            scene.add_geometry(make_cylinder(p0, p1, bond_radius, bond_rgb))

    # polyhedra centers
    centers = pick_poly_centers_mp_like(structure, poly_mode)
    polys = []
    if centers:
        # 顶点过滤：P-centered 用阴离子做顶点（更像 PS4，而不是乱七八糟的凸包）
        vertex_filter = None
        if "P" in centers:
            vertex_filter = set(TYPICAL_ANIONS)

        polys = get_polyhedra(
            structure=structure,
            centers=centers,
            cn_allowed=set(poly_cn),
            max_neighbors=max(poly_cn) if poly_cn else 6,
            vertex_filter=vertex_filter,
        )

        # polyhedra mesh
        for center_cart, verts_cart, el in polys:
            verts_s = verts_cart - shift
            try:
                hull = trimesh.convex.convex_hull(verts_s)
            except Exception:
                continue

            # polyhedra 颜色：用中心元素颜色，但更淡、更透明
            rgb = element_rgb(el)
            apply_pbr_material(hull, (rgb[0], rgb[1], rgb[2], int(poly_alpha)), double_sided=True)
            scene.add_geometry(hull)

    # write glb
    os.makedirs(os.path.dirname(os.path.abspath(out_glb)), exist_ok=True)
    with open(out_glb, "wb") as f:
        f.write(trimesh.exchange.gltf.export_glb(scene))

    return {
        "ok": True,
        "out_glb": out_glb,
        "standardized": True,
        "supercell": list(sc),
        "draw_atoms": bool(draw_atoms),
        "draw_bonds": bool(draw_bonds),
        "poly_mode": poly_mode,
        "poly_centers": centers,
        "poly_cn": sorted(list(poly_cn)),
        "poly_alpha": int(poly_alpha),
        "n_sites": len(structure),
        "n_bonds": len(bonds),
        "n_polyhedra": len(polys),
    }


# ---------- CLI ----------
def parse_int_set(csv_or_list: str) -> Set[int]:
    parts = [p.strip() for p in csv_or_list.replace(";", ",").split(",") if p.strip()]
    return {int(p) for p in parts}

def main():
    ap = argparse.ArgumentParser(description="Export MP-style crystal GLB (atoms/bonds + MP-like polyhedra).")
    ap.add_argument("--in", dest="inp", required=True, help="Input .cif or .json")
    ap.add_argument("--out", dest="out", required=True, help="Output .glb")

    ap.add_argument("--supercell", nargs=3, type=int, default=[1, 1, 1], help="e.g. 1 1 1 (recommended first)")
    ap.add_argument("--atom-radius", type=float, default=0.40)
    ap.add_argument("--bond-radius", type=float, default=0.07)
    ap.add_argument("--no-atoms", action="store_true")
    ap.add_argument("--no-bonds", action="store_true")
    ap.add_argument("--max-bonds-per-site", type=int, default=None)

    ap.add_argument("--poly-mode", type=str, default="mp",
                    choices=["mp", "p", "li", "none"],
                    help="mp: prefer P-centered (SSE), p: only P, li: only Li, none: no polyhedra")
    ap.add_argument("--poly-cn", type=str, default="4", help="Allowed CN, e.g. 4 or 4,6")
    ap.add_argument("--poly-alpha", type=int, default=85, help="0-255, lower => more transparent")

    args = ap.parse_args()

    s = load_structure(args.inp)
    poly_cn = parse_int_set(args.poly_cn)

    info = export_glb_mpstyle(
        structure=s,
        out_glb=args.out,
        supercell=tuple(args.supercell),
        atom_radius=args.atom_radius,
        bond_radius=args.bond_radius,
        draw_atoms=not args.no_atoms,
        draw_bonds=not args.no_bonds,
        max_bonds_per_site=args.max_bonds_per_site,
        poly_mode=args.poly_mode,
        poly_cn=poly_cn,
        poly_alpha=args.poly_alpha,
    )
    print(json.dumps(info, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
