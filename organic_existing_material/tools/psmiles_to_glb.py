#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import argparse
from collections import Counter

import numpy as np
import trimesh
from rdkit import Chem
from rdkit.Chem import AllChem, rdMolDescriptors


# =========================
# Visual style config
# =========================

ATOM_COLORS = {
    "H": [1.00, 1.00, 1.00, 1.0],
    "C": [0.72, 0.72, 0.72, 1.0],
    "N": [0.35, 0.45, 1.00, 1.0],
    "O": [1.00, 0.10, 0.10, 1.0],
    "F": [0.55, 0.90, 0.20, 1.0],
    "P": [1.00, 0.62, 0.00, 1.0],
    "S": [1.00, 0.82, 0.10, 1.0],
    "Cl": [0.10, 0.80, 0.10, 1.0],
    "Br": [0.60, 0.20, 0.20, 1.0],
    "I": [0.50, 0.00, 0.60, 1.0],

    # internal dummy atom symbol
    "*": [0.96, 0.77, 0.26, 1.0],  # gold
}

ATOM_RADII = {
    "H": 0.18,
    "C": 0.30,
    "N": 0.29,
    "O": 0.29,
    "F": 0.28,
    "P": 0.36,
    "S": 0.36,
    "Cl": 0.34,
    "Br": 0.38,
    "I": 0.42,
    "*": 0.34,
}

ELEMENT_LABELS = {
    "H": "Hydrogen",
    "C": "Carbon",
    "N": "Nitrogen",
    "O": "Oxygen",
    "F": "Fluorine",
    "P": "Phosphorus",
    "S": "Sulfur",
    "Cl": "Chlorine",
    "Br": "Bromine",
    "I": "Iodine",
    "*": "Polymer Link Site",
}

BOND_RADIUS = 0.11
BOND_COLOR = [0.78, 0.78, 0.78, 1.0]


# =========================
# Helpers
# =========================

def normalize_psmiles(psmiles: str) -> str:
    """
    Normalize bare * into [*], but keep already-bracketed [*] unchanged.
    """
    s = str(psmiles or "").strip()
    s = re.sub(r"(?<!\[)\*(?!\])", "[*]", s)
    return s


def atom_symbol(atom) -> str:
    if atom.GetAtomicNum() == 0:
        return "*"
    return atom.GetSymbol()


def display_symbol(sym: str) -> str:
    return "R" if sym == "*" else sym


def atom_color(atom):
    return ATOM_COLORS.get(atom_symbol(atom), [0.70, 0.70, 0.70, 1.0])


def atom_radius(atom):
    return ATOM_RADII.get(atom_symbol(atom), 0.30)


def rgba_to_hex(color):
    r = int(round(color[0] * 255))
    g = int(round(color[1] * 255))
    b = int(round(color[2] * 255))
    return f"#{r:02X}{g:02X}{b:02X}"


def create_colored_mesh(mesh: trimesh.Trimesh, color):
    rgba = (np.array(color) * 255).astype(np.uint8)
    mesh.visual.vertex_colors = np.tile(rgba, (len(mesh.vertices), 1))
    return mesh


# =========================
# Molecule build / embed
# =========================

def build_mol(psmiles: str):
    mol = Chem.MolFromSmiles(psmiles)
    if mol is None:
        raise ValueError(f"RDKit failed to parse: {psmiles}")
    mol = Chem.AddHs(mol)
    return mol


def embed_mol_3d(mol):
    params = AllChem.ETKDGv3()
    params.randomSeed = 42
    code = AllChem.EmbedMolecule(mol, params)
    if code != 0:
        raise RuntimeError("3D embedding failed")

    has_dummy = any(atom.GetAtomicNum() == 0 for atom in mol.GetAtoms())
    if not has_dummy:
        try:
            AllChem.UFFOptimizeMolecule(mol, maxIters=500)
        except Exception:
            pass

    return mol


# =========================
# Geometry helpers
# =========================

def create_sphere(center, radius, color):
    mesh = trimesh.creation.icosphere(subdivisions=2, radius=radius)
    mesh.apply_translation(center)
    return create_colored_mesh(mesh, color)


def rotation_matrix_from_vectors(vec1, vec2):
    a = vec1 / np.linalg.norm(vec1)
    b = vec2 / np.linalg.norm(vec2)
    v = np.cross(a, b)
    c = np.dot(a, b)

    if np.isclose(c, 1.0):
        T = np.eye(4)
        return T

    if np.isclose(c, -1.0):
        axis = np.array([1.0, 0.0, 0.0])
        if np.allclose(a, axis):
            axis = np.array([0.0, 1.0, 0.0])
        v = np.cross(a, axis)
        v = v / np.linalg.norm(v)
        R = -np.eye(3) + 2 * np.outer(v, v)
        T = np.eye(4)
        T[:3, :3] = R
        return T

    s = np.linalg.norm(v)
    kmat = np.array([
        [0, -v[2], v[1]],
        [v[2], 0, -v[0]],
        [-v[1], v[0], 0]
    ])
    R = np.eye(3) + kmat + kmat @ kmat * ((1 - c) / (s ** 2))
    T = np.eye(4)
    T[:3, :3] = R
    return T


def create_cylinder(p1, p2, radius, color):
    vec = p2 - p1
    length = np.linalg.norm(vec)
    if length < 1e-8:
        return None
    mesh = trimesh.creation.cylinder(radius=radius, height=length, sections=24)
    T = rotation_matrix_from_vectors(np.array([0.0, 0.0, 1.0]), vec)
    mesh.apply_transform(T)
    mesh.apply_translation((p1 + p2) / 2.0)
    return create_colored_mesh(mesh, color)


def orthonormal_perp(vec):
    vec = vec / np.linalg.norm(vec)
    trial = np.array([1.0, 0.0, 0.0], dtype=float)
    if abs(np.dot(vec, trial)) > 0.9:
        trial = np.array([0.0, 1.0, 0.0], dtype=float)
    perp = np.cross(vec, trial)
    perp = perp / np.linalg.norm(perp)
    return perp


# =========================
# Special marker for [*]
# =========================

def create_dummy_star_marker(center):
    """
    Create a highlighted marker for polymer link site [*]:
    - gold core sphere
    - pale gold outer shell
    - 6 spikes
    """
    parts = []

    # core sphere
    core = trimesh.creation.icosphere(subdivisions=2, radius=0.34)
    core.apply_translation(center)
    create_colored_mesh(core, [0.96, 0.77, 0.26, 1.0])  # gold
    parts.append(core)

    # outer shell (visual fake glow)
    shell = trimesh.creation.icosphere(subdivisions=2, radius=0.48)
    shell.apply_translation(center)
    create_colored_mesh(shell, [1.00, 0.92, 0.55, 0.35])
    parts.append(shell)

    # spikes
    directions = [
        np.array([1, 0, 0], dtype=float),
        np.array([-1, 0, 0], dtype=float),
        np.array([0, 1, 0], dtype=float),
        np.array([0, -1, 0], dtype=float),
        np.array([0, 0, 1], dtype=float),
        np.array([0, 0, -1], dtype=float),
    ]

    for d in directions:
        p1 = center + d * 0.18
        p2 = center + d * 0.72
        spike = create_cylinder(
            p1, p2,
            radius=0.05,
            color=[1.00, 0.84, 0.20, 1.0]
        )
        if spike is not None:
            parts.append(spike)

    return parts


# =========================
# Bond drawing
# =========================

def bond_order_as_float(bond):
    bt = bond.GetBondType()
    if bt == Chem.BondType.SINGLE:
        return 1.0
    if bt == Chem.BondType.DOUBLE:
        return 2.0
    if bt == Chem.BondType.TRIPLE:
        return 3.0
    if bt == Chem.BondType.AROMATIC:
        return 1.5
    return 1.0


def add_bond_geometry(scene, p1, p2, order, color):
    """
    Draw:
    - single / aromatic -> single cylinder
    - double -> two parallel cylinders
    - triple -> three parallel cylinders
    """
    vec = p2 - p1
    if np.linalg.norm(vec) < 1e-8:
        return

    if order < 1.75:
        cyl = create_cylinder(p1, p2, BOND_RADIUS, color)
        if cyl is not None:
            scene.add_geometry(cyl)
        return

    perp = orthonormal_perp(vec)

    if order < 2.75:
        # double bond
        offset = perp * 0.10
        cyl1 = create_cylinder(p1 + offset, p2 + offset, BOND_RADIUS * 0.85, color)
        cyl2 = create_cylinder(p1 - offset, p2 - offset, BOND_RADIUS * 0.85, color)
        if cyl1 is not None:
            scene.add_geometry(cyl1)
        if cyl2 is not None:
            scene.add_geometry(cyl2)
        return

    # triple bond
    offset = perp * 0.13
    cyl0 = create_cylinder(p1, p2, BOND_RADIUS * 0.75, color)
    cyl1 = create_cylinder(p1 + offset, p2 + offset, BOND_RADIUS * 0.70, color)
    cyl2 = create_cylinder(p1 - offset, p2 - offset, BOND_RADIUS * 0.70, color)

    for cyl in (cyl0, cyl1, cyl2):
        if cyl is not None:
            scene.add_geometry(cyl)


# =========================
# Scene build
# =========================

def molecule_to_scene(mol):
    conf = mol.GetConformer()
    scene = trimesh.Scene()

    # Atoms
    for atom in mol.GetAtoms():
        idx = atom.GetIdx()
        pos = conf.GetAtomPosition(idx)
        center = np.array([pos.x, pos.y, pos.z], dtype=float)

        if atom.GetAtomicNum() == 0:
            parts = create_dummy_star_marker(center)
            for k, part in enumerate(parts):
                scene.add_geometry(part, node_name=f"dummy_{idx}_{k}")
        else:
            sphere = create_sphere(center, atom_radius(atom), atom_color(atom))
            scene.add_geometry(sphere, node_name=f"atom_{idx}")

    # Bonds
    for bond in mol.GetBonds():
        i = bond.GetBeginAtomIdx()
        j = bond.GetEndAtomIdx()

        pi = conf.GetAtomPosition(i)
        pj = conf.GetAtomPosition(j)

        p1 = np.array([pi.x, pi.y, pi.z], dtype=float)
        p2 = np.array([pj.x, pj.y, pj.z], dtype=float)

        order = bond_order_as_float(bond)
        add_bond_geometry(scene, p1, p2, order, BOND_COLOR)

    return scene


# =========================
# Metadata
# =========================

def summarize_molecule(mol, normalized_psmiles: str):
    atoms = list(mol.GetAtoms())
    raw_counts = Counter(atom_symbol(a) for a in atoms)

    legend = []
    # Keep stable ordering: common elements first, R last if present
    sort_key = lambda item: (item[0] == "*", item[0])
    for sym, count in sorted(raw_counts.items(), key=sort_key):
        color = ATOM_COLORS.get(sym, [0.7, 0.7, 0.7, 1.0])
        legend.append({
            "symbol": display_symbol(sym),
            "raw_symbol": sym,
            "label": ELEMENT_LABELS.get(sym, sym),
            "count": int(count),
            "color_rgba": color,
            "color_hex": rgba_to_hex(color),
        })

    formula = rdMolDescriptors.CalcMolFormula(mol).replace("*", "R")

    return {
        "normalized_psmiles": normalized_psmiles,
        "formula": formula,
        "atom_count": len(atoms),
        "molecular_weight": rdMolDescriptors.CalcExactMolWt(mol),
        "element_counts": {display_symbol(k): int(v) for k, v in raw_counts.items()},
        "legend": legend,
    }


# =========================
# Main
# =========================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--psmiles", required=True, help="Input PSMILES/SMILES")
    parser.add_argument("--out", required=True, help="Output GLB path")
    parser.add_argument("--meta", required=True, help="Output metadata JSON path")
    args = parser.parse_args()

    normalized = normalize_psmiles(args.psmiles)
    mol = build_mol(normalized)
    mol = embed_mol_3d(mol)
    scene = molecule_to_scene(mol)

    out_path = os.path.abspath(args.out)
    meta_path = os.path.abspath(args.meta)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)

    scene.export(out_path)
    meta = summarize_molecule(mol, normalized)

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "ok": True,
        "glb": out_path,
        "meta": meta_path,
        **meta
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()