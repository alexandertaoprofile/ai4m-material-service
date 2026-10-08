from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from private_shadow_dispatcher import Channel, PrivateShadowDispatcher
from shadow_channel_configs import model_shadow_channels


class PrivateShadowDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.result = {"model_domain": "chip_glass_thermomechanical_family_v1", "all_candidates": [{"candidate_id": "CGS-001"}]}

    def channel(self):
        return Channel(
            name="glass_v0", model_domain="chip_glass_thermomechanical_family_v1",
            enabled_env="TEST_GLASS_SHADOW_ENABLED",
            builder=lambda _: ({"requests": [{"request_id": "glass-1"}]}, [{"request_id": "glass-1"}]),
            command=lambda request, response: ["fake-python", str(request), str(response)],
            merger=lambda mapping, response: {
                "ok": True, "display_allowed": False, "production_release_authorized": False,
                "comparisons": [{"request_id": mapping[0]["request_id"], "status": response["results"][0]["status"],
                                 "display_allowed": False, "production_release_authorized": False}],
            },
        )

    @staticmethod
    def fake_success(command, **kwargs):
        response = Path(command[-1])
        response.write_text(json.dumps({
            "ok": True, "display_allowed": False, "production_release_authorized": False,
            "model": {"version": "test-only"},
            "results": [{"request_id": "glass-1", "status": "predicted_diagnostic",
                         "display_allowed": False, "production_release_authorized": False}],
        }), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_default_off_does_not_launch(self):
        dispatcher = PrivateShadowDispatcher(self.root, [self.channel()], process_runner=self.fake_success)
        with patch.dict(os.environ, {"TEST_GLASS_SHADOW_ENABLED": "0"}):
            self.assertIsNone(dispatcher.submit("glass_v0", "task-1", self.result))
        self.assertEqual(list(self.root.glob("**/*.json")), [])

    def test_private_success_never_mutates_formal_result(self):
        original = deepcopy(self.result)
        dispatcher = PrivateShadowDispatcher(self.root, [self.channel()], process_runner=self.fake_success)
        with patch.dict(os.environ, {"TEST_GLASS_SHADOW_ENABLED": "1"}):
            future = dispatcher.submit("glass_v0", "task-1", self.result)
            record = future.result(timeout=5)
        self.assertEqual(record["status"], "completed")
        self.assertFalse(record["public_effect"])
        self.assertFalse(record["formal_result_mutated"])
        self.assertEqual(self.result, original)
        paths = list((self.root / "task-1" / "shadow" / "glass_v0").glob("*/manifest.json"))
        self.assertEqual(len(paths), 1)
        self.assertTrue((paths[0].parent / "comparison.json").is_file())
        self.assertFalse((self.root / "task-1" / "presentation").exists())

    def test_timeout_is_private_and_nonblocking(self):
        def timeout(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        dispatcher = PrivateShadowDispatcher(self.root, [self.channel()], process_runner=timeout)
        record = dispatcher.run_sync("glass_v0", "task-2", self.result)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error_type"], "TimeoutExpired")
        self.assertFalse(record["public_effect"])

    def test_queue_full_records_private_notice(self):
        dispatcher = PrivateShadowDispatcher(self.root, [self.channel()], process_runner=self.fake_success)
        for _ in range(dispatcher.channels["glass_v0"].queue_size):
            dispatcher.slots["glass_v0"].acquire()
        try:
            with patch.dict(os.environ, {"TEST_GLASS_SHADOW_ENABLED": "1"}):
                self.assertIsNone(dispatcher.submit("glass_v0", "task-queue", self.result))
        finally:
            for _ in range(dispatcher.channels["glass_v0"].queue_size):
                dispatcher.slots["glass_v0"].release()
        notices = list((self.root / "task-queue" / "shadow" / "glass_v0").glob("notice-*.json"))
        self.assertEqual(len(notices), 1)
        self.assertEqual(json.loads(notices[0].read_text(encoding="utf-8"))["status"], "queue_full")

    def test_taskid_cannot_escape_results_root(self):
        dispatcher = PrivateShadowDispatcher(self.root, [self.channel()], process_runner=self.fake_success)
        for bad in ("..", ".", "../other", "task/other"):
            with self.assertRaises(ValueError):
                dispatcher.run_sync("glass_v0", bad, self.result)

    def test_channel_commands_are_versioned_and_separate(self):
        channels = model_shadow_channels(
            alloy_python=Path("/env/alloy/python"), alloy_runtime=Path("/private/alloy_v10"),
            alloy_items=lambda _: [], glass_python=Path("/env/glass/python"),
            glass_runtime=Path("/private/glass_v0"), glass_checkpoint=Path("/models/glass.pt"),
            glass_code=Path("/models/code"), glass_builder=lambda _: ({"requests": []}, []),
            glass_merger=lambda _, response: response,
        )
        self.assertEqual({c.name for c in channels}, {"alloy_v10", "alloy_v10_stainless", "alloy_v10_nickel", "glass_v1"})
        alloy = channels[0].command(Path("/tmp/request.json"), Path("/tmp/response.json"))
        glass = channels[-1].command(Path("/tmp/request.json"), Path("/tmp/response.json"))
        self.assertIn("--service-comparison", alloy)
        self.assertIn("--expected-sha256", glass)
        self.assertNotIn("--service-comparison", glass)


if __name__ == "__main__":
    unittest.main()
