#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import math
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import numpy as np

from ase.io import read
from ase.atoms import Atoms

from pygltflib import (
    GLTF2, Scene, Node, Mesh, Primitive, Buffer, BufferView, Accessor,
    Asset, Material, PbrMetallicRoughness,
    Animation, AnimationChannel, AnimationChannelTarget, AnimationSampler,
    FLOAT, UNSIGNED_SHORT, UNSIGNED_INT,
    ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER,
)

# ----------------------------
# parsing helpers
# ----------------------------

def parse_kv_map(s: str) -> Dict[str, str]:
    """
    "Li=0.28,P=0.22,S=0.32" -> {"Li":"0.28",...}
    """
    out = {}
    if not s:
        return out
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"Bad kv part: {part}")
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out

def parse_float_map(s: str) -> Dict[str, float]:
    kv = parse_kv_map(s)
    return {k: float(v) for k, v in kv.items()}

def parse_color_map(s: str) -> Dict[str, Tuple[float, float, float, float]]:
    """
    "Li=#B07CFF,P=#F2B248" -> rgba floats
    """
    kv = parse_kv_map(s)
    out = {}
    for k, v in kv.items():
        v = v.strip()
        if v.startswith("#"):
            hexv = v[1:]
            if len(hexv) != 6:
                raise ValueError(f"Color must be #RRGGBB: {v}")
            r = int(hexv[0:2], 16) / 255.0
            g = int(hexv[2:4], 16) / 255.0
            b = int(hexv[4:6], 16) / 255.0
            out[k] = (r, g, b, 1.0)
        else:
            nums = [float(x) for x in re.split(r"[,\s]+", v) if x]
            if len(nums) == 3:
                out[k] = (nums[0], nums[1], nums[2], 1.0)
            elif len(nums) == 4:
                out[k] = (nums[0], nums[1], nums[2], nums[3])
            else:
                raise ValueError(f"Bad color value: {v}")
    return out

# ----------------------------
# geometry: robust icosphere
# ----------------------------

def _normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    if n < 1e-12:
        return v
    return v / n

def make_icosphere(radius: float = 1.0, subdivisions: int = 2) -> Tuple[np.ndarray, np.ndarray]:
    """
    Returns (verts, faces) for an icosphere.
    Robust: keeps verts as Python list while subdividing, converts to numpy at end.
    """
    t = (1.0 + math.sqrt(5.0)) / 2.0

    verts = [
        _normalize(np.array([-1,  t,  0], dtype=np.float64)),
        _normalize(np.array([ 1,  t,  0], dtype=np.float64)),
        _normalize(np.array([-1, -t,  0], dtype=np.float64)),
        _normalize(np.array([ 1, -t,  0], dtype=np.float64)),

        _normalize(np.array([ 0, -1,  t], dtype=np.float64)),
        _normalize(np.array([ 0,  1,  t], dtype=np.float64)),
        _normalize(np.array([ 0, -1, -t], dtype=np.float64)),
        _normalize(np.array([ 0,  1, -t], dtype=np.float64)),

        _normalize(np.array([ t,  0, -1], dtype=np.float64)),
        _normalize(np.array([ t,  0,  1], dtype=np.float64)),
        _normalize(np.array([-t,  0, -1], dtype=np.float64)),
        _normalize(np.array([-t,  0,  1], dtype=np.float64)),
    ]

    faces = [
        (0,11,5),(0,5,1),(0,1,7),(0,7,10),(0,10,11),
        (1,5,9),(5,11,4),(11,10,2),(10,7,6),(7,1,8),
        (3,9,4),(3,4,2),(3,2,6),(3,6,8),(3,8,9),
        (4,9,5),(2,4,11),(6,2,10),(8,6,7),(9,8,1),
    ]

    def midpoint_cache_key(i: int, j: int) -> Tuple[int, int]:
        return (i, j) if i < j else (j, i)

    for _ in range(subdivisions):
        cache: Dict[Tuple[int,int], int] = {}
        new_faces = []

        def midpoint(i: int, j: int) -> int:
            key = midpoint_cache_key(i, j)
            if key in cache:
                return cache[key]
            m = _normalize((verts[i] + verts[j]) * 0.5)
            verts.append(m)
            idx = len(verts) - 1
            cache[key] = idx
            return idx

        for (a,b,c) in faces:
            ab = midpoint(a,b)
            bc = midpoint(b,c)
            ca = midpoint(c,a)
            new_faces.extend([
                (a, ab, ca),
                (b, bc, ab),
                (c, ca, bc),
                (ab, bc, ca),
            ])
        faces = new_faces

    v = np.array(verts, dtype=np.float32) * float(radius)
    f = np.array(faces, dtype=np.int32)
    return v, f

# ----------------------------
# trajectory processing
# ----------------------------

def frac_coords(pos: np.ndarray, cell: np.ndarray) -> np.ndarray:
    inv = np.linalg.inv(cell.T)
    return pos @ inv

def cart_coords(frac: np.ndarray, cell: np.ndarray) -> np.ndarray:
    return frac @ cell.T

def unwrap_positions(frames: List[Atoms]) -> List[np.ndarray]:
    """
    Make positions continuous across PBC using minimum-image in fractional coordinates.
    """
    if len(frames) <= 1:
        return [frames[0].get_positions().astype(np.float32)]

    out: List[np.ndarray] = []
    prev_cell = frames[0].cell.array
    prev_pos = frames[0].get_positions()
    prev_frac = frac_coords(prev_pos, prev_cell)
    acc_frac = prev_frac.copy()
    out.append(prev_pos.astype(np.float32))

    for i in range(1, len(frames)):
        cell = frames[i].cell.array
        pos = frames[i].get_positions()
        f = frac_coords(pos, cell)

        d = f - prev_frac
        d = d - np.round(d)   # wrap to [-0.5, 0.5)
        acc_frac = acc_frac + d

        pos_unwrapped = cart_coords(acc_frac, cell)
        out.append(pos_unwrapped.astype(np.float32))

        prev_frac = f
        prev_cell = cell

    return out

def compute_center(pos: np.ndarray) -> np.ndarray:
    return pos.mean(axis=0).astype(np.float32)

# ----------------------------
# glTF packing helpers
# ----------------------------

@dataclass
class PackedAccessor:
    accessor_index: int
    byte_length: int

class GLBBuilder:
    def __init__(self):
        self.gltf = GLTF2(asset=Asset(version="2.0"))
        self.gltf.scenes = [Scene(nodes=[])]
        self.gltf.scene = 0

        self._bin_chunks: List[bytes] = []
        self.gltf.buffers = [Buffer(byteLength=0)]
        self.gltf.bufferViews = []
        self.gltf.accessors = []
        self.gltf.meshes = []
        self.gltf.nodes = []
        self.gltf.materials = []
        self.gltf.animations = []

    def _align4(self, b: bytes) -> bytes:
        pad = (-len(b)) % 4
        if pad:
            b += b"\x00" * pad
        return b

    def add_bufferview_and_accessor(self, array: np.ndarray, target: int, component_type: int, type_str: str) -> int:
        raw = array.tobytes()
        raw = self._align4(raw)
        offset = sum(len(c) for c in self._bin_chunks)
        self._bin_chunks.append(raw)

        bv_idx = len(self.gltf.bufferViews)
        self.gltf.bufferViews.append(BufferView(
            buffer=0,
            byteOffset=offset,
            byteLength=len(raw),
            target=target
        ))

        a = Accessor(
            bufferView=bv_idx,
            byteOffset=0,
            componentType=component_type,
            count=len(array),
            type=type_str
        )
        if type_str == "VEC3" and array.dtype in (np.float32, np.float64):
            a.min = array.min(axis=0).tolist()
            a.max = array.max(axis=0).tolist()

        acc_idx = len(self.gltf.accessors)
        self.gltf.accessors.append(a)
        return acc_idx

    def add_material(self, rgba: Tuple[float,float,float,float]) -> int:
        r,g,b,a = rgba
        mat = Material(
            pbrMetallicRoughness=PbrMetallicRoughness(
                baseColorFactor=[float(r), float(g), float(b), float(a)],
                metallicFactor=0.0,
                roughnessFactor=0.85
            ),
            doubleSided=True
        )
        idx = len(self.gltf.materials)
        self.gltf.materials.append(mat)
        return idx

    def add_sphere_mesh(self, radius: float, subdivisions: int, material_index: int) -> int:
        verts, faces = make_icosphere(radius=radius, subdivisions=subdivisions)

        if verts.shape[0] > 65535:
            idx_dtype = np.uint32
            comp = UNSIGNED_INT
        else:
            idx_dtype = np.uint16
            comp = UNSIGNED_SHORT
        indices = faces.astype(idx_dtype).reshape(-1)

        pos_acc = self.add_bufferview_and_accessor(verts.astype(np.float32), ARRAY_BUFFER, FLOAT, "VEC3")
        idx_acc = self.add_bufferview_and_accessor(indices, ELEMENT_ARRAY_BUFFER, comp, "SCALAR")

        prim = Primitive(
            attributes={"POSITION": pos_acc},
            indices=idx_acc,
            material=material_index
        )
        mesh = Mesh(primitives=[prim])
        m_idx = len(self.gltf.meshes)
        self.gltf.meshes.append(mesh)
        return m_idx

    def add_node(self, mesh_idx: Optional[int] = None, name: str = "", translation=None) -> int:
        n = Node(name=name)
        if mesh_idx is not None:
            n.mesh = mesh_idx
        if translation is not None:
            n.translation = [float(x) for x in translation]
        idx = len(self.gltf.nodes)
        self.gltf.nodes.append(n)
        return idx

    def add_animation_translation(self, node_idx: int, times: np.ndarray, translations: np.ndarray) -> None:
        """
        times: (T,) float32
        translations: (T,3) float32
        """
        # NOTE: pygltflib 的 Accessor 对 SCALAR 更常见的是 (T,)；(T,1) 也能用但更不“标准”
        t_acc = self.add_bufferview_and_accessor(times.astype(np.float32), ARRAY_BUFFER, FLOAT, "SCALAR")
        v_acc = self.add_bufferview_and_accessor(translations.astype(np.float32), ARRAY_BUFFER, FLOAT, "VEC3")

        sampler = AnimationSampler(input=t_acc, output=v_acc, interpolation="LINEAR")
        channel = AnimationChannel(
            sampler=0,
            target=AnimationChannelTarget(node=node_idx, path="translation")
        )
        anim = Animation(samplers=[sampler], channels=[channel])
        self.gltf.animations.append(anim)

    def save_glb(self, out_path: str) -> None:
        bin_blob = b"".join(self._bin_chunks)
        self.gltf.buffers[0].byteLength = len(bin_blob)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        self.gltf.set_binary_blob(bin_blob)
        self.gltf.save_binary(out_path)

# ----------------------------
# Defaults: MP-like colors & radii
# ----------------------------

DEFAULT_ELEMENT_COLORS: Dict[str, Tuple[float,float,float,float]] = {
    "Li": (170/255.0, 235/255.0, 170/255.0, 1.0),
    "P":  (190/255.0, 150/255.0, 210/255.0, 1.0),
    "S":  (255/255.0, 255/255.0,   0/255.0, 1.0),
    "Cl": (  0/255.0, 255/255.0,   0/255.0, 1.0),
}

DEFAULT_ELEMENT_RADII: Dict[str, float] = {
    "Li": 0.30,
    "P":  0.28,
    "S":  0.32,
    "Cl": 0.34,
}

def default_radius(el: str) -> float:
    return float(DEFAULT_ELEMENT_RADII.get(el, 0.30))

def default_color(el: str) -> Tuple[float,float,float,float]:
    return DEFAULT_ELEMENT_COLORS.get(el, (0.80, 0.80, 0.80, 1.0))

# ----------------------------
# main
# ----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extxyz", required=True)
    ap.add_argument("--out", required=True)

    ap.add_argument("--fps", type=float, default=20.0)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--max-frames", type=int, default=200, help="auto cap frames to avoid huge GLB")
    ap.add_argument("--radii", default="")
    ap.add_argument("--colors", default="")
    ap.add_argument("--subdiv", type=int, default=2, help="icosphere subdivisions (2~3 ok)")
    ap.add_argument("--unwrap", action="store_true", help="unwrap PBC to avoid jumps (recommended)")
    ap.add_argument("--no-unwrap", dest="unwrap", action="store_false")
    ap.set_defaults(unwrap=True)
    ap.add_argument("--center-mode", choices=["none", "first", "each"], default="first")
    ap.add_argument("--scale", type=float, default=1.0, help="global scale applied to positions")
    args = ap.parse_args()

    radii_map = parse_float_map(args.radii)
    color_map = parse_color_map(args.colors)

    frames: List[Atoms] = read(args.extxyz, index=":")
    if len(frames) == 0:
        raise RuntimeError("No frames read from extxyz")

    # stride first
    frames = frames[::max(1, int(args.stride))]

    # cap frames (uniform downsample)
    max_frames = int(args.max_frames)
    if max_frames > 0 and len(frames) > max_frames:
        step = max(1, len(frames) // max_frames)
        frames = frames[::step]

    # positions (possibly unwrapped)
    if args.unwrap and all(f.pbc.any() for f in frames):
        pos_list = unwrap_positions(frames)
    else:
        pos_list = [f.get_positions().astype(np.float32) for f in frames]

    # center handling
    if args.center_mode == "first":
        c0 = compute_center(pos_list[0])
        pos_list = [(p - c0) for p in pos_list]
    elif args.center_mode == "each":
        pos_list = [(p - compute_center(p)) for p in pos_list]
    # none -> do nothing

    # scale
    pos_list = [(p * float(args.scale)).astype(np.float32) for p in pos_list]

    symbols = frames[0].get_chemical_symbols()
    unique_elems = sorted(set(symbols))

    builder = GLBBuilder()

    # build materials & meshes per element
    elem_to_mesh: Dict[str, int] = {}
    for el in unique_elems:
        rgba = color_map.get(el, default_color(el))
        rad = float(radii_map.get(el, default_radius(el)))
        mat_idx = builder.add_material(rgba)
        mesh_idx = builder.add_sphere_mesh(radius=rad, subdivisions=int(args.subdiv), material_index=mat_idx)
        elem_to_mesh[el] = mesh_idx

    # create nodes per atom
    atom_nodes: List[int] = []
    for i, el in enumerate(symbols):
        mesh_idx = elem_to_mesh[el]
        n_idx = builder.add_node(mesh_idx=mesh_idx, name=f"{el}_{i}", translation=pos_list[0][i])
        atom_nodes.append(n_idx)
        builder.gltf.scenes[0].nodes.append(n_idx)

    # animation time axis
    T = len(pos_list)
    times = (np.arange(T, dtype=np.float32) / float(args.fps)).astype(np.float32)

    # one animation per atom (ok for your SSE size)
    for i, node_idx in enumerate(atom_nodes):
        traj = np.stack([pos_list[t][i] for t in range(T)], axis=0).astype(np.float32)
        builder.add_animation_translation(node_idx=node_idx, times=times, translations=traj)

    builder.save_glb(args.out)
    print(f"[OK] Saved animated GLB: {args.out}")
    print(f"[INFO] Frames: {T}, atoms: {len(symbols)}, elems: {unique_elems}")
    print(f"[INFO] unwrap={args.unwrap}, center_mode={args.center_mode}, stride={args.stride}, fps={args.fps}, max_frames={args.max_frames}")

if __name__ == "__main__":
    main()
