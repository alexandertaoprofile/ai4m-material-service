from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    import numpy as np
    from pymatgen.core import Lattice, Structure
    from tools import render_new_material_assets
    from tools import structure_to_glb
    PYMATGEN_AVAILABLE = True
except ModuleNotFoundError:
    PYMATGEN_AVAILABLE = False



@unittest.skipUnless(PYMATGEN_AVAILABLE, "Crystal rendering dependencies are installed in the MatterGen environment")
class LocalCoordinationVisualisationTest(unittest.TestCase):
    def test_alloy_elements_have_stable_non_grey_colours_in_2d_and_glb_assets(self) -> None:
        expected = {"Ti", "Cr", "Mo", "Nb", "Ta", "W", "Zr", "Hf"}
        two_dimensional = {element: render_new_material_assets.color(element) for element in expected}
        three_dimensional = {element: structure_to_glb.element_rgb(element) for element in expected}
        self.assertTrue(all(value != "#AAB7C4" for value in two_dimensional.values()))
        self.assertEqual(len(set(two_dimensional.values())), len(expected))
        self.assertTrue(all(value != (160, 160, 160) for value in three_dimensional.values()))
        self.assertEqual(len(set(three_dimensional.values())), len(expected))
        self.assertEqual(
            two_dimensional,
            {
                element: "#{:02X}{:02X}{:02X}".format(*rgb)
                for element, rgb in three_dimensional.items()
            },
        )
        self.assertEqual(render_new_material_assets.SURFACE, "#06182F")
        self.assertEqual(render_new_material_assets.PANEL, "#0B2848")

    def test_glb_uses_the_same_structure_spec_as_the_rotation_gif(self) -> None:
        with patch.object(structure_to_glb, "export_glb_mpstyle", return_value={"ok": True}) as export:
            output = render_new_material_assets.try_export_glb(
                Structure(Lattice.cubic(3), ["Fe"], [[0, 0, 0]]),
                "/tmp/candidate_structure.glb",
            )

        self.assertEqual(output, "/tmp/candidate_structure.glb")
        _structure, _path = export.call_args.args
        kwargs = export.call_args.kwargs
        self.assertEqual(kwargs["supercell"], (2, 2, 2))
        self.assertTrue(kwargs["draw_bonds"])
        self.assertFalse(kwargs["draw_periodic_boundary_bonds"])
        self.assertEqual(kwargs["poly_mode"], "auto")
        self.assertEqual(render_new_material_assets.STRUCTURE_LINE, "#C7D6E4")

    def test_only_recognised_local_motifs_get_connections(self) -> None:
        structure = Structure(
            Lattice.cubic(10),
            ["P", "S", "S", "S", "S", "Fe"],
            [[0.5, 0.5, 0.5], [0.6, 0.5, 0.5], [0.4, 0.5, 0.5],
             [0.5, 0.6, 0.5], [0.5, 0.4, 0.5], [0.1, 0.1, 0.1]],
        )

        class FakeCrystalNN:
            def get_nn_info(self, _structure, index):
                if index == 0:
                    return [
                        {"site_index": neighbour, "image": np.array([0, 0, 0])}
                        for neighbour in (1, 2, 3, 4)
                    ]
                if index == 5:
                    return [{"site_index": 1, "image": np.array([0, 0, 0])}]
                return []

        with patch.object(structure_to_glb, "CrystalNN", FakeCrystalNN):
            connections = structure_to_glb.get_local_coordination_connections(
                structure, {"P", "Fe"}, {4, 6}
            )

        self.assertEqual([(first, second) for first, second, _image in connections],
                         [(0, 1), (0, 2), (0, 3), (0, 4)])

    def test_periodic_edges_are_not_shown_in_local_view(self) -> None:
        structure = Structure(Lattice.cubic(10), ["P", "S", "S", "S", "S"],
                              [[0.5, 0.5, 0.5], [0.6, 0.5, 0.5], [0.4, 0.5, 0.5],
                               [0.5, 0.6, 0.5], [0.5, 0.4, 0.5]])

        class FakeCrystalNN:
            def get_nn_info(self, _structure, _index):
                return [
                    {"site_index": neighbour, "image": np.array([0, 0, 0])}
                    for neighbour in (1, 2, 3)
                ] + [{"site_index": 4, "image": np.array([1, 0, 0])}]

        with patch.object(structure_to_glb, "CrystalNN", FakeCrystalNN):
            connections = structure_to_glb.get_local_coordination_connections(structure, {"P"}, {4})

        self.assertEqual(connections, [])
