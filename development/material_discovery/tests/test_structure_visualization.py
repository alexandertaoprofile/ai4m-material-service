from __future__ import annotations

import unittest
from pathlib import Path
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
    def comparison_source(self, *options):
        candidates, validations, ranked = [], [], []
        for row in options:
            identifier = row["id"]
            candidate = {"candidate_id": identifier, "formula_pretty": "CdCu2SnTe4", "metadata": {"element_counts": {"Cd": 1, "Cu": 2, "Sn": 1, "Te": 4}}}
            validation = {"candidate_id": identifier, "is_valid": True, "energy_above_hull": row.get("hull", 0.0),
                "metadata": {"composition_ok": True, "mattersim": {"relaxation_converged": row.get("converged", True)}, "screening": {"state": row.get("state", "passed")}}}
            candidates.append(candidate); validations.append(validation)
            ranked.append({"candidate": candidate, "validation": validation})
        return {"generation": {"candidates": candidates, "metadata": {"mode": "dng"}}, "validations": validations,
            "ranked_candidates": ranked, "constraints": {"target_properties": {"energy_above_hull": 0.05}}}

    def draw_comparison(self, source):
        plt = render_new_material_assets.plt
        with patch.object(plt, "close"), patch("matplotlib.figure.Figure.savefig"):
            render_new_material_assets.render_omatg_comparison(source, Path("unused.png"))
            figure = plt.gcf()
            figure.canvas.draw()
        self.addCleanup(plt.close, figure)
        return figure

    def test_comparison_zero_has_a_visible_point_and_missing_hull_no_fake_bar(self):
        source = self.comparison_source({"id": "zero"}, {"id": "pending", "hull": None, "state": "incomplete"})
        figure = self.draw_comparison(source)
        axis = figure.axes[-1]
        self.assertEqual(len(axis.collections), 1)
        self.assertEqual(list(axis.collections[0].get_offsets()[0]), [0.0, 0.0])
        self.assertEqual(len(axis.patches), 2)  # green range and the true zero-width bar
        self.assertTrue(any(text.get_text() == "没有稳定性数值\n稳定性结果未记录" for text in axis.texts))
        self.assertTrue(any("0.00000\n可继续比较" == text.get_text() for text in axis.texts))

    def test_comparison_keeps_incomplete_and_other_failed_conditions_distinct(self):
        source = self.comparison_source({"id": "other-condition", "state": "failed"}, {"id": "missing-property", "state": "incomplete"})
        figure = self.draw_comparison(source)
        captions = [text.get_text() for text in figure.axes[-1].texts]
        self.assertIn("0.00000\n其他设定条件未通过", captions)
        self.assertIn("0.00000\n部分性能尚无法判断", captions)
        self.assertTrue(any("暂未得到" in text.get_text() for text in figure.texts))

    def test_chart_labels_use_shared_scheme_names_instead_of_internal_ids(self):
        figure = self.draw_comparison(self.comparison_source({"id": "dng-004"}, {"id": "dng-009"}))
        labels = [text.get_text() for text in figure.axes[-1].get_yticklabels()]
        self.assertIn("JC-E3B0C44298-01", labels[0])
        self.assertIn("JC-E3B0C44298-02", labels[1])
        self.assertTrue(all("dng-" not in label for label in labels))

    def test_composition_figure_names_elements_and_keeps_multirow_labels_in_bounds(self):
        from matplotlib.text import Text
        plt = render_new_material_assets.plt
        structure = Structure(Lattice.cubic(20), ["H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne"],
            [[index * 0.05, 0.5, 0.5] for index in range(10)])
        with patch.object(plt, "close"), patch("matplotlib.figure.Figure.savefig"):
            render_new_material_assets.render_design_brief(Path("unused.png"), structure, {"target_properties": {"energy_above_hull": 0.05}})
            figure = plt.gcf()
            figure.canvas.draw()
        self.addCleanup(plt.close, figure)
        labels = [text.get_text() for text in figure.texts]
        self.assertIn("氢", labels)
        self.assertIn("氖", labels)
        self.assertIn("材料组成与评价条件", labels)
        renderer = figure.canvas.get_renderer()
        for text in figure.findobj(match=Text):
            if text.get_visible() and text.get_text():
                with self.subTest(caption=text.get_text()):
                    bounds = text.get_window_extent(renderer)
                    self.assertGreaterEqual(bounds.x0, figure.bbox.x0)
                    self.assertGreaterEqual(bounds.y0, figure.bbox.y0)
                    self.assertLessEqual(bounds.x1, figure.bbox.x1)
                    self.assertLessEqual(bounds.y1, figure.bbox.y1)

    def test_comparison_handles_no_candidates_or_no_converged_structure(self):
        for source in (self.comparison_source(), self.comparison_source({"id": "pending", "hull": None, "converged": False, "state": "incomplete"})):
            with self.subTest(source=source):
                figure = self.draw_comparison(source)
                self.assertEqual(len(figure.axes[-1].patches), 0)
                self.assertTrue(any("没有收敛结构" in text.get_text() for text in figure.axes[-1].texts))

    def test_comparison_ten_rows_and_customer_captions_fit_inside_the_image(self):
        from matplotlib.text import Text
        source = self.comparison_source(*[{"id": f"dng-{index:03d}"} for index in range(1, 11)])
        figure = self.draw_comparison(source)
        renderer = figure.canvas.get_renderer()
        for text in figure.findobj(match=Text):
            if not text.get_visible() or not text.get_text():
                continue
            with self.subTest(caption=text.get_text()):
                bounds = text.get_window_extent(renderer)
                self.assertGreaterEqual(bounds.x0, figure.bbox.x0 - 2)
                self.assertGreaterEqual(bounds.y0, figure.bbox.y0 - 2)
                self.assertLessEqual(bounds.x1, figure.bbox.x1 + 2)
                self.assertLessEqual(bounds.y1, figure.bbox.y1 + 2)

    def test_zero_hull_and_threshold_captions_stay_inside_the_panel(self) -> None:
        plt = render_new_material_assets.plt
        with patch.object(plt, "close"), patch("matplotlib.figure.Figure.savefig"):
            render_new_material_assets.render_scorecard(Path("unused.png"), "CdCu2SnTe4", {"energy_above_hull": 0.0, "formation_energy_per_atom": -0.45}, {"target_properties": {"energy_above_hull": 0.0}})
            figure = plt.gcf()
            figure.canvas.draw()
            axis = figure.axes[0]
            captions = [text for text in axis.texts if text.get_text().startswith(("稳定性差值", "复核参考线"))]
            self.assertEqual(len(captions), 2)
            for text in captions:
                extent = text.get_window_extent(figure.canvas.get_renderer())
                self.assertGreaterEqual(extent.x0, axis.bbox.x0)
                self.assertLessEqual(extent.x1, axis.bbox.x1)
        plt.close(figure)

    def test_scorecard_uses_ev_per_atom_for_all_thermodynamic_values(self) -> None:
        self.assertEqual(
            render_new_material_assets.format_energy_ev_per_atom(0.1303),
            "0.1303 eV/atom",
        )
        self.assertEqual(render_new_material_assets.format_energy_ev_per_atom(None), "待计算")

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
