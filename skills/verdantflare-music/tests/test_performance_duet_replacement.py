"""Check timed singer replacement and distinguish preview tracks from remix stems."""

import importlib.util
from pathlib import Path
import unittest

import numpy as np


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPLACE = load("replace_performance_duet_voice")
VALIDATE = load("validate_performance_duet")
PREPARE = load("prepare_duet_replacement_backing")


class PerformanceDuetReplacementTest(unittest.TestCase):
    def setUp(self):
        self.rate = 8000
        self.plan = {
            "schema_version": "duet-performance-v1",
            "duration_seconds": 3.,
            "lines": [
                {"start_seconds": 0., "end_seconds": 1., "voice": "female", "text": "你听"},
                {"start_seconds": 1., "end_seconds": 2., "voice": "male", "text": "我答"},
                {"start_seconds": 2., "end_seconds": 3., "voice": "both", "text": "同唱"},
            ],
        }
        time = np.arange(3 * self.rate) / self.rate
        stereo = lambda x: np.column_stack((x, x)).astype("float32")
        self.backing = stereo(.03 * np.sin(2 * np.pi * 90 * time))
        self.female = stereo(.06 * np.sin(2 * np.pi * 300 * time))
        self.female[self.rate:2 * self.rate] = 0
        self.male = stereo(.07 * np.sin(2 * np.pi * 180 * time))
        self.male[:self.rate] = 0
        self.baseline = self.backing + self.female + self.male
        self.converted = stereo(.06 * np.sin(2 * np.pi * 220 * time))

    def test_replaces_only_target_windows_and_preserves_duet(self):
        result = REPLACE.assemble(self.plan, self.baseline, self.backing,
                                  self.female, self.converted, "male", self.rate)
        np.testing.assert_array_equal(result["Preview"][:self.rate],
                                      self.baseline[:self.rate])
        self.assertEqual(float(np.max(np.abs(result["Male"][:self.rate]))), 0.)
        self.assertGreater(float(np.max(np.abs(result["Male"][self.rate:]))), .01)
        self.assertGreater(float(np.max(np.abs(result["Female"][2 * self.rate:]))), .01)
        report = VALIDATE.validate(self.plan, result["Female"], result["Male"],
                                   result["Preview"], self.baseline, self.rate,
                                   result["Instrumental"])
        self.assertTrue(report["qualified"], report["errors"])
        self.assertEqual(report["role_counts"], {"female": 1, "male": 1, "both": 1})

    def test_rejects_short_phrase_before_envelope_processing(self):
        self.plan["lines"][1]["end_seconds"] = 1.1
        with self.assertRaisesRegex(ValueError, "invalid times"):
            REPLACE.assemble(self.plan, self.baseline, self.backing,
                             self.female, self.converted, "male", self.rate)

    def test_suppresses_converted_sound_when_source_is_silent(self):
        reference = np.zeros((self.rate, 2), dtype="float32")
        candidate = np.full((self.rate, 1), .01, dtype="float32")
        gain = REPLACE.match_vocal(reference, candidate, round(.01 * self.rate))
        self.assertLess(float(np.max(gain)), .2)

    def test_detects_nonreconstructing_role_tracks(self):
        report = VALIDATE.mix_reconstruction(self.baseline, self.backing,
                                             self.female, self.male)
        self.assertTrue(report["passed"])
        incomplete = self.male.copy()
        incomplete[self.rate:2 * self.rate] = 0
        report = VALIDATE.mix_reconstruction(self.baseline, self.backing,
                                             self.female, incomplete)
        self.assertFalse(report["passed"])
        self.assertGreater(report["residual_relative_db"], -35)

    def test_rejects_self_comparison_as_level_reference(self):
        report = VALIDATE.validate(self.plan, self.female, self.male,
                                   self.baseline, self.baseline, self.rate,
                                   self.backing)
        self.assertFalse(report["qualified"])
        self.assertTrue(report["stems_reconstruct_preview"])
        self.assertIn("source and preview are identical; use the prior approved mix",
                      report["errors"])

    def test_restores_approved_backing_automation_to_new_separator(self):
        time = np.arange(4 * self.rate) / self.rate
        source = np.column_stack((.08 * np.sin(2 * np.pi * 91 * time),
                                  .07 * np.sin(2 * np.pi * 173 * time))).astype("float32")
        automation = .82 + .06 * np.sin(2 * np.pi * .2 * time)
        approved = source * automation[:, None]
        replacement = source * 1.12
        prepared, report = PREPARE.prepare(source, approved, replacement, self.rate)
        np.testing.assert_allclose(prepared, approved, atol=.001)
        self.assertAlmostEqual(report["replacement_scale"], 1 / 1.12, places=4)
        self.assertLess(report["approved_gain_reconstruction_db"], -35)
        self.assertTrue(report["human_review_required"])

    def test_rejects_nonmatching_approved_backing(self):
        source = np.tile(self.backing[:self.rate], (3, 1))
        independent = np.column_stack((.05 * np.sin(2 * np.pi * 301 *
                                                    np.arange(len(source)) / self.rate),) * 2)
        with self.assertRaisesRegex(ValueError, "not a level-automated source"):
            PREPARE.prepare(source, source + independent, source, self.rate)


if __name__ == "__main__":
    unittest.main()
