"""Check complementary singer lifting and source-mix replacement invariants."""

import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np
from scipy.signal import resample_poly


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LIFT = load("lift_duet_singer_mask")
REPLACE = load("replace_singer_in_mix")


class SingerMixReplacementTest(unittest.TestCase):
    def test_lift_is_complementary_and_tracks_source_rate(self):
        rate = 32000
        time = np.arange(rate) / rate
        target = .11 * np.sin(2 * np.pi * 220 * time)
        other = .08 * np.sin(2 * np.pi * 630 * time)
        source = np.column_stack((target + other, .9 * target + 1.1 * other)).astype("float32")
        target_low = resample_poly(target, 1, 2).astype("float32")
        other_low = resample_poly(other, 1, 2).astype("float32")
        first, second = LIFT.lift(source, rate, target_low, other_low, 16000)
        self.assertEqual(first.shape, source.shape)
        np.testing.assert_allclose(first + second, source, atol=2e-5)
        self.assertLess(np.sqrt(np.mean((first[:, 0] - target) ** 2)), .04)

    def test_lift_rejects_misaligned_estimate(self):
        source = np.zeros((32000, 2), dtype="float32")
        with self.assertRaisesRegex(ValueError, "more than 30 ms"):
            LIFT.lift(source, 32000, np.zeros(15000), np.zeros(16000), 16000)

    def test_replacement_preserves_other_singer_and_untouched_audio(self):
        rate = 8000
        time = np.arange(3 * rate) / rate
        target = .05 * np.sin(2 * np.pi * 170 * time)
        other = .06 * np.sin(2 * np.pi * 350 * time)
        backing = .03 * np.sin(2 * np.pi * 80 * time)
        old = np.column_stack((target, target)).astype("float32")
        original = np.column_stack((target + other + backing,) * 2).astype("float32")
        new = (.05 * np.sin(2 * np.pi * 190 * time)).astype("float32")[:, None]
        plan = {"duration_seconds": 3., "male_phrases": [
            {"start": 1., "end": 2., "source": "separated", "max_gain": 1.15}]}
        phrases = REPLACE.phrases_from_plan(plan, "male", len(original), rate)
        mixed, report = REPLACE.assemble(original, old, new, rate, phrases)
        np.testing.assert_array_equal(mixed[:rate], original[:rate])
        np.testing.assert_array_equal(mixed[2 * rate:], original[2 * rate:])
        self.assertEqual(report["untouched_max_difference"], 0.)
        mid = int(1.5 * rate)
        expected = original[mid] - old[mid] + new[mid, 0] * np.array([.95, 1.])
        np.testing.assert_allclose(mixed[mid], expected, atol=.002)
        self.assertEqual(len(mixed), len(original))

    def test_rejects_overlap_and_misaligned_tracks(self):
        plan = {"duration_seconds": 3., "female_phrases": [
            {"start": 1., "end": 2.}, {"start": 1.9, "end": 2.5}]}
        with self.assertRaisesRegex(ValueError, "overlap"):
            REPLACE.phrases_from_plan(plan, "female", 24000, 8000)
        valid = REPLACE.phrases_from_plan(
            {"duration_seconds": 3., "female_phrases": [{"start": 1., "end": 2.}]},
            "female", 24000, 8000)
        with self.assertRaisesRegex(ValueError, "align exactly"):
            REPLACE.assemble(np.zeros((24000, 2)), np.zeros((24000, 2)),
                             np.zeros((23999, 1)), 8000, valid)


if __name__ == "__main__":
    unittest.main()
