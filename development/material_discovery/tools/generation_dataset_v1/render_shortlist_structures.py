#!/usr/bin/env python3
"""Render truthful static previews for the two DFT-priority shortlist structures.

The images are visualisations of MatterSim-relaxed candidate CIFs.  They are
not a claim of experimentally observed structure, bonding, or phase purity.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pymatgen.core import Structure


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.render_new_material_assets import render_structure_png  # noqa: E402


ROOT = Path("/data/se42/hea_surrogate/analysis/shortlist_property_screen_v1")
OUTPUT = ROOT / "candidate_briefs" / "assets"
CANDIDATES = (
    (
        ROOT / "relaxed_cif" / "aerospace_alloy-000415.relaxed.cif",
        "Mn3CrFe4Co2Ni2",
        "hea_mn3crfe4co2ni2_relaxed_structure.png",
    ),
    (
        ROOT / "relaxed_cif" / "chip_packaging-000165.relaxed.cif",
        "AlSi3N3O3",
        "sialon_alsi3n3o3_relaxed_structure.png",
    ),
)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for cif_path, formula, filename in CANDIDATES:
        if not cif_path.is_file():
            raise FileNotFoundError(cif_path)
        structure = Structure.from_file(cif_path)
        output = OUTPUT / filename
        render_structure_png(structure, output, formula, relaxed=True)
        print(f"wrote {output}")


if __name__ == "__main__":
    main()
