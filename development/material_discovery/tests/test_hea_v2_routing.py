from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.material_workflow.generation import build_mattergen_command
from src.material_workflow.schemas import GenerationConstraint


class HeaV2RoutingTest(unittest.TestCase):
    def test_validated_cantor_system_uses_v2_checkpoint_and_element_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model_dir = Path(temporary)
            (model_dir / "checkpoints").mkdir()
            (model_dir / "config.yaml").write_text("test", encoding="utf-8")
            (model_dir / "checkpoints" / "epoch=18-loss_val=0.47.ckpt").write_text("test", encoding="utf-8")
            constraints = GenerationConstraint(
                taskid="cantor-v2",
                allowed_elements=["Ni", "Mn", "Fe", "Cr", "Co"],
                target_properties={"energy_above_hull": 0.05},
            )
            with patch.dict(os.environ, {
                "MATTERGEN_HEA_V2_MODEL_DIR": str(model_dir),
                "MATTERGEN_HEA_V2_SYSTEMS": "Co-Cr-Fe-Mn-Ni",
                "MATTERGEN_HEA_V2_ENABLED": "true",
            }, clear=False):
                command = build_mattergen_command(constraints, Path(temporary) / "output", 2)

        rendered = "\n".join(command)
        self.assertIn(f"--model_path={model_dir}", command)
        self.assertIn("--checkpoint_epoch=18", command)
        self.assertNotIn("--pretrained-name=chemical_system_energy_above_hull", command)
        self.assertIn('--properties_to_condition_on={"chemical_system":["Ni","Mn","Fe","Cr","Co"],"energy_above_hull":0.05}', command)
        self.assertIn("--diffusion_guidance_factor=1.0", command)
        self.assertNotIn("mattergen_fast_sampling", rendered)

    def test_other_system_stays_on_official_conditional_model(self) -> None:
        constraints = GenerationConstraint(
            taskid="other-alloy",
            allowed_elements=["Nb", "Mo", "Ta", "W"],
            target_properties={"energy_above_hull": 0.05},
        )
        with patch.dict(os.environ, {"MATTERGEN_HEA_V2_SYSTEMS": "Co-Cr-Fe-Mn-Ni"}, clear=False):
            command = build_mattergen_command(constraints, Path("/tmp/output"), 1)
        self.assertIn("--pretrained-name=chemical_system_energy_above_hull", command)
        self.assertIn('--properties_to_condition_on={"chemical_system":["Nb","Mo","Ta","W"],"energy_above_hull":0.05}', command)


if __name__ == "__main__":
    unittest.main()
