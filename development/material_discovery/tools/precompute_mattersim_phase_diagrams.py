#!/usr/bin/env python3
"""Warm persistent MP2020 convex-hull caches for common discovery systems.

Run this inside the MatterGen environment.  It performs no candidate
generation or relaxation; it only builds the reference convex hull once so
online tasks can reuse it.
"""

from __future__ import annotations

import argparse

from run_mattersim_evaluation import (
    _canonical_chemical_system,
    _load_cached_mp2020_reference,
    _load_cached_phase_diagram,
    _patch_mattergen_lmdb_loader,
)


COMMON_SYSTEMS = (
    "Co-Cr-Fe-Mn-Ni",  # current high-entropy alloy discovery route
    "Nb-Mo-Ta-W",      # refractory high-entropy alloy example
    "Li-P-S",          # sulfide solid-electrolyte default
    "Li-P-S-Cl",       # halide-containing sulfide electrolyte example
    "Li-La-Zr-O",      # garnet solid-electrolyte default
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--system",
        action="append",
        default=[],
        help="Chemical system to warm; repeat. Defaults to the registered common systems.",
    )
    args = parser.parse_args()
    systems = args.system or list(COMMON_SYSTEMS)
    _patch_mattergen_lmdb_loader()
    reference = _load_cached_mp2020_reference()
    for system in systems:
        canonical = _canonical_chemical_system(system)
        phase_diagram = _load_cached_phase_diagram(reference, canonical)
        print(f"ready {canonical}: {len(phase_diagram.all_entries)} reference entries")


if __name__ == "__main__":
    main()
