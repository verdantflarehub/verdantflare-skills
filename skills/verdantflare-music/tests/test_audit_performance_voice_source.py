"""Behavioral checks for the duet voice-source coverage gate."""

import importlib.util
from pathlib import Path
import unittest

import numpy as np


SCRIPT = (Path(__file__).resolve().parents[1] / "scripts" /
          "audit_performance_voice_source.py")
SPEC = importlib.util.spec_from_file_location("audit_performance_voice_source", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class VoiceSourceAuditTest(unittest.TestCase):
    def setUp(self):
        self.rate = 8000
        self.plan = {
            "schema_version": "duet-performance-v1",
            "duration_seconds": 3.,
            "lines": [
                {"start_seconds": 0., "end_seconds": 1., "voice": "female"},
                {"start_seconds": 1., "end_seconds": 2., "voice": "male"},
                {"start_seconds": 2., "end_seconds": 3., "voice": "both"},
            ],
        }
        samples = np.arange(3 * self.rate)
        vocal = (.12 * np.sin(2 * np.pi * 220 * samples / self.rate)).astype("float32")
        self.source = np.column_stack((vocal, vocal))
        self.backing = np.zeros_like(self.source)

    def check(self, candidate, dry=None):
        return AUDIT.audit(self.plan, self.source, self.backing,
                           candidate, dry, self.rate, "male")

    def test_detects_male_gap_but_ignores_female_window(self):
        candidate = self.source.copy()
        candidate[round(1.3 * self.rate):round(1.7 * self.rate)] = 0
        candidate[round(.2 * self.rate):round(.6 * self.rate)] = 0
        report = self.check(candidate)
        self.assertFalse(report["coverage_passed"])
        events = report["candidate"]["coverage_gaps"]
        self.assertEqual(len(events), 1)
        self.assertGreaterEqual(events[0]["start_seconds"], 1.2)
        self.assertLessEqual(events[0]["end_seconds"], 1.8)

    def test_healthy_candidate_passes_even_when_dry_source_has_gap(self):
        dry = self.source.copy()
        dry[round(1.3 * self.rate):round(1.7 * self.rate)] = 0
        candidate = self.source[:-round(.009 * self.rate)]
        report = self.check(candidate, dry)
        self.assertTrue(report["coverage_passed"])
        self.assertEqual(report["candidate"]["coverage_gaps"], [])
        self.assertEqual(len(report["dry_source"]["coverage_gaps"]), 1)

    def test_rejects_material_timeline_mismatch(self):
        with self.assertRaisesRegex(ValueError, "durations differ"):
            self.check(self.source[:-round(.05 * self.rate)])


if __name__ == "__main__":
    unittest.main()
