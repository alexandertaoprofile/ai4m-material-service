"""Periodic image and unsafe-cell regressions, no GPU required."""
from unittest import TestCase
import importlib.util

try:
    from pymatgen.core import Lattice, Structure
    from tools.crystal_checks import check_structure
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

import unittest


@unittest.skipUnless(AVAILABLE, "Requires the scientific pymatgen environment")
class OMatGGeometryTest(TestCase):
    def test_identical_sites_are_rejected(self):
        result = check_structure(Structure(Lattice.cubic(5), ["Li", "O"], [[0, 0, 0], [0, 0, 0]]))
        self.assertFalse(result["is_valid"])
        self.assertEqual(result["metadata"]["min_interatomic_distance"], 0)

    def test_short_periodic_self_image_is_rejected(self):
        result = check_structure(Structure(Lattice.cubic(1), ["Li"], [[0, 0, 0]]))
        self.assertFalse(result["is_valid"])
        self.assertTrue(any(pair["sites"] == [0, 0] for pair in result["metadata"]["close_pair_violations"]))

    def test_collapsed_cell_is_rejected_before_neighbor_enumeration(self):
        with self.assertRaisesRegex(ValueError, "安全范围"):
            check_structure(Structure(Lattice.from_parameters(0.001, 100, 100, 90, 90, 90), ["Li"], [[0, 0, 0]]))

    def test_ordinary_crystal_records_symmetry_and_counts(self):
        result = check_structure(Structure(Lattice.cubic(5.64), ["Na", "Cl"], [[0, 0, 0], [.5, .5, .5]]))
        self.assertTrue(result["is_valid"])
        self.assertEqual(result["metadata"]["element_counts"], {"Na": 1, "Cl": 1})
        self.assertFalse(result["metadata"]["non_centrosymmetric"])
