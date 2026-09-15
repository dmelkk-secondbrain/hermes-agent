#!/usr/bin/env python3
import argparse
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("t041_custody", ROOT / "tools/t041_custody.py")
custody = importlib.util.module_from_spec(spec)
spec.loader.exec_module(custody)


def ok(stdout=""):
    return {"argv": ["fixture"], "exit": 0, "stdout": stdout, "stderr": ""}


class CustodyObservationTests(unittest.TestCase):
    def base(self):
        cg = {"pid": 10, "read": ok("0::/user.slice/test.scope"), "value": "0::/user.slice/test.scope"}
        gateway = ok("MainPID=22\nControlGroup=/system.slice/hermes.service")
        return {
            "elapsed_seconds": 119.5,
            "process_ceiling_seconds": 120,
            "probe": {"cgroup": cg},
            "gateway": gateway,
            "source_import": {"head": ok("h"), "tree": ok("t"), "candidate_check": ok()},
            "samples": [{"probe": cg, "gateway": gateway}],
        }

    def test_every_sampled_command_status_is_required(self):
        for path in (("samples", 0, "probe", "read"), ("samples", 0, "gateway"),
                     ("gateway",), ("source_import", "head"), ("source_import", "tree")):
            data = self.base()
            target = data
            for key in path[:-1]:
                target = target[key]
            target[path[-1]]["exit"] = 7
            self.assertFalse(custody.evaluate_observation(data)[0], path)

    def test_every_probe_cgroup_must_remain_outside_gateway(self):
        data = self.base()
        data["samples"][0]["probe"]["value"] = "0::/system.slice/hermes.service"
        self.assertFalse(custody.evaluate_observation(data)[0])

    def test_elapsed_time_above_ceiling_is_rejected(self):
        data = self.base()
        data["elapsed_seconds"] = 120.001
        self.assertFalse(custody.evaluate_observation(data)[0])

    def test_command_timeout_is_clamped_to_absolute_deadline(self):
        seen = {}
        def fake_run(*args, **kwargs):
            seen["timeout"] = kwargs["timeout"]
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(custody.subprocess, "run", side_effect=fake_run):
            custody.run(["true"], deadline=time.monotonic() + 0.04, env={"HOME": "/isolated"})
        self.assertGreater(seen["timeout"], 0)
        self.assertLessEqual(seen["timeout"], 0.04)

    def test_real_capture_passes_isolated_home_to_every_subprocess_and_removes_it(self):
        homes = []
        def fake_run(argv, deadline=None, env=None):
            homes.append(env["HOME"])
            if argv[0] == "cat":
                return ok("0::/user.slice/test.scope")
            if argv[0] == "systemctl":
                return ok("MainPID=22\nControlGroup=/system.slice/hermes.service")
            return ok("fixture")
        with tempfile.TemporaryDirectory() as td, mock.patch.object(custody, "run", side_effect=fake_run), \
                mock.patch.object(custody, "OBSERVATION_SECONDS", 0.03), \
                mock.patch.object(custody, "INTERVAL_SECONDS", 0.01), \
                mock.patch.object(custody, "PROCESS_CEILING_SECONDS", 0.2):
            output = Path(td) / "capture.json"
            rc = custody.observe(argparse.Namespace(output=str(output), source=td, candidate="c"))
            data = json.loads(output.read_text())
            self.assertEqual(rc, 0, data.get("errors"))
            self.assertTrue(homes)
            self.assertEqual(set(homes), {data["temporary_home"]})
            self.assertFalse(Path(data["temporary_home"]).exists())


if __name__ == "__main__":
    unittest.main()
