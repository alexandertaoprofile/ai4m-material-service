"""Run in OMatG's scientific environment; exercises CPU-to-GPU anchor transfer."""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

source = Path(os.getenv("OMATG_SOURCE_DIR", "/data/third_party/OMatG"))
available = source.is_dir() and importlib.util.find_spec("torch") is not None


@unittest.skipUnless(available, "Requires isolated OMatG scientific environment")
class WorkerAnchorTests(unittest.TestCase):
    def check_conditioning(self, device):
        import torch
        sys.path.insert(0, str(source))
        from omg.sampler.independent_sampler import IndependentSampler
        from omg.si.discrete_flow_matching_mask import DiscreteFlowMatchingMask
        from tools.run_omatg_generation import install_species_mask
        templates = [{0: 26, 3: 24}, {2: 26, 7: 28}]
        initial = SimpleNamespace(species=torch.zeros(16, dtype=torch.long), batch=torch.tensor([0]*8 + [1]*8))
        def integrate(_self, model_function, values, time, step, batch):
            logits = model_function(time, values)[0]
            self.assertEqual(int(torch.isfinite(logits).sum().item()), 16*3)
            # Simulate remasking; the wrapper must restore anchors afterwards.
            return torch.zeros_like(values)
        with patch.object(IndependentSampler, "sample_p_0", lambda _self, _x: initial), patch.object(DiscreteFlowMatchingMask, "integrate", integrate):
            install_species_mask(["Fe", "Cr", "Ni"], templates)
            sample = IndependentSampler.sample_p_0(None, None)
            self.assertEqual(sample.species[[0, 3, 10, 15]].tolist(), [26, 24, 26, 28])
            current = sample.species.to(device)
            batch = sample.batch.to(device)
            result = DiscreteFlowMatchingMask.integrate(None, lambda _t, _x: (torch.zeros(16, 100, device=device), None), current,
                torch.tensor(0.2, device=device), torch.tensor(0.01, device=device), batch)
            self.assertEqual(result[[0, 3, 10, 15]].tolist(), [26, 24, 26, 28])
            self.assertEqual(result.device, current.device)
            self.assertEqual(int((result != 0).sum().item()), 4)

    def test_partial_species_anchors_and_mask_on_cpu(self):
        self.check_conditioning("cpu")

    def test_sampler_cpu_then_integrator_cuda(self):
        import torch
        if not torch.cuda.is_available():
            self.skipTest("CUDA not available")
        self.check_conditioning("cuda")


if __name__ == "__main__":
    unittest.main()
