import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from install_environment import ROOT, commands, normalize, pins
from prepare_runtime import settings


class EnvironmentPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = json.loads((ROOT / "env/current/installed-20261008.json").read_text())

    def test_web_dependencies_and_deployed_compatibility(self):
        dependencies = pins("web", self.snapshot)
        self.assertIn("numpy==1.26.4", dependencies)
        self.assertIn("python-dotenv==1.0.0", dependencies)
        self.assertIn("colorama==0.4.6", dependencies)
        plan = commands("web", Path("/data/test-env/web"), self.snapshot)
        self.assertIn("apply_environment_compatibility.py", " ".join(plan[-1]))

    def test_alignn_dependencies_respect_model_package_contract(self):
        dependencies = pins("alignn", self.snapshot)
        for pin in ("numpy==1.26.4", "pyparsing==2.4.7", "matplotlib==3.8.4"):
            self.assertIn(pin, dependencies)
        plan = commands("alignn", Path("/data/test-env/alignn"), self.snapshot)
        self.assertIn("torch==2.2.1", plan[1])
        self.assertIn("dgl==1.1.1", plan[2])

    def test_generation_and_relaxation_use_separate_builds(self):
        for name, expected in (("numeric", "torch==2.2.1"), ("omatg", "torch==2.8.0")):
            self.assertIn(expected, commands(name, Path("/data/test-env") / name, self.snapshot)[1])
        numeric = commands("numeric", Path("/data/test-env/numeric"), self.snapshot)
        for pin in ("torch==2.2.1", "torchaudio==2.2.1", "torchvision==0.17.1"):
            self.assertTrue(any(pin in command for command in numeric[1:-1]))
            self.assertIn(pin, numeric[-1])
        omatg = pins("omatg", self.snapshot)
        self.assertIn("jsonargparse[signatures]==4.41.0", omatg)
        self.assertIn("docstring-parser==0.18.0", omatg)
        self.assertIn("typeshed-client==2.13.0", omatg)
        self.assertIn("warp-lang==1.17.0", omatg)

    def test_all_runtime_workers_have_local_configured_paths(self):
        config = settings(Path("/data/test-resources"), Path("/data/test-env"))
        self.assertEqual(config["1111"]["MATERIAL_LEGACY_PYTHON"], "/data/test-env/numeric/bin/python")
        self.assertEqual(config["1111"]["MATERIAL_RESEARCH_PYTHON"], "/data/test-env/recipe/bin/python")
        self.assertEqual(config["1105"]["MATURE_MATERIAL_CATALOG_ROOT"], "/data/test-resources/data/1105/catalog")
        self.assertEqual(config["1115"]["ALIGNN_SITE_PACKAGES"], "")

    def test_selected_dependencies_match_independently_validated_versions(self):
        validated = json.loads((ROOT / "env/validated/installed-20261008.json").read_text())
        for environment in ("web", "numeric", "recipe", "omatg", "alignn"):
            observed = {normalize(row["name"]): row["version"] for row in validated[environment]["packages"]}
            self.assertTrue(validated[environment]["dependency_check_ok"])
            for pin in pins(environment, self.snapshot):
                name, version = pin.split("==")
                self.assertEqual(observed[normalize(name.split("[")[0])], version, pin)


if __name__ == "__main__":
    unittest.main()
