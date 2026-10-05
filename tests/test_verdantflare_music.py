import sys
import unittest
from pathlib import Path

import numpy as np


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "verdantflare-music" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from validate_duet import chord_pcs, validate_duet_template, validate_schedule, validate_score
from validate_performance_duet import validate as validate_performance


def valid_plan():
    return {
        "schema_version": "duet-score-v1",
        "duration_seconds": 4.0,
        "lines": [
            {"start_seconds": 0.0, "end_seconds": 1.0, "voice": "female", "text": "甲"},
            {"start_seconds": 1.0, "end_seconds": 2.0, "voice": "male", "text": "乙"},
            {"start_seconds": 2.0, "end_seconds": 3.0, "voice": "both", "text": "丙"},
            {"start_seconds": 3.0, "end_seconds": 4.0, "voice": "instrumental", "text": ""},
        ],
        "chords": [
            {"start_seconds": 0.0, "end_seconds": 4.0, "root": "A", "quality": "min"},
        ],
        "melody": {
            "female": [
                {"start_seconds": 0.0, "end_seconds": 1.0, "midi": 69, "lyric": "甲"},
                {"start_seconds": 2.0, "end_seconds": 3.0, "midi": 69, "lyric": "丙"},
            ],
            "male": [
                {"start_seconds": 1.0, "end_seconds": 2.0, "midi": 57, "lyric": "乙"},
                {"start_seconds": 2.0, "end_seconds": 3.0, "midi": 57, "lyric": "丙"},
            ],
        },
    }


def normalize_lines(plan):
    return [
        {"start": line["start_seconds"], "end": line["end_seconds"], "voice": line["voice"], "text": line["text"]}
        for line in plan["lines"]
    ]


class DuetPlanTests(unittest.TestCase):
    def test_minor_eleventh_does_not_accept_flat_sixth(self):
        self.assertEqual(chord_pcs("A", "min11"), {9, 11, 0, 2, 4, 7})
        plan = valid_plan()
        plan["chords"][0]["quality"] = "min11"
        plan["melody"]["female"][0]["midi"] = 65  # F4 is outside Am11.
        errors = []
        validate_score(plan, normalize_lines(plan), errors)
        self.assertTrue(any("no chord-tone support" in error for error in errors))

    def test_complete_score_passes_machine_plan_checks(self):
        plan = valid_plan()
        lines = normalize_lines(plan)
        errors = []
        validate_schedule(plan, lines, errors)
        validate_score(plan, lines, errors)
        self.assertEqual(errors, [])


    def test_missing_both_and_melody_contract_is_rejected(self):
        plan = valid_plan()
        plan["lines"] = plan["lines"][:2]
        plan["melody"].pop("male")
        lines = normalize_lines(plan)
        errors = []
        validate_schedule(plan, lines, errors)
        validate_score(plan, lines, errors)
        self.assertTrue(any("both" in error for error in errors))
        self.assertTrue(any("independent female and male melody" in error for error in errors))

    def test_call_response_template_rejects_early_and_unannotated_overlap(self):
        plan = valid_plan()
        plan["duet_template"] = "call_response_final_merge"
        plan["both_policy"] = {
            "max_voiced_ratio": 0.18,
            "first_both_min_ratio": 0.65,
            "allowed_modes": ["unison"],
        }
        lines = normalize_lines(plan)
        errors = []
        validate_duet_template(plan, lines, errors)
        self.assertTrue(any("before the" in error for error in errors))
        self.assertTrue(any("harmony_mode" in error for error in errors))

    def test_call_response_template_accepts_late_annotated_merge(self):
        plan = {
            "duration_seconds": 10.0,
            "duet_template": "call_response_final_merge",
            "both_policy": {
                "max_voiced_ratio": 0.18,
                "first_both_min_ratio": 0.65,
                "allowed_modes": ["third"],
            },
        }
        lines = [
            {"start": 0.0, "end": 4.0, "voice": "female", "text": "甲"},
            {"start": 4.0, "end": 7.0, "voice": "male", "text": "乙"},
            {"start": 7.0, "end": 8.0, "voice": "both", "text": "丙", "harmony_mode": "third"},
            {"start": 8.0, "end": 10.0, "voice": "instrumental", "text": ""},
        ]
        errors = []
        validate_duet_template(plan, lines, errors)
        self.assertEqual(errors, [])


class PerformanceDuetTests(unittest.TestCase):
    def test_full_mix_level_drop_fails_despite_correct_singer_windows(self):
        rate = 1000
        plan = {
            "schema_version": "duet-performance-v1",
            "duration_seconds": 4.0,
            "lines": [
                {"start_seconds": 0, "end_seconds": 1, "voice": "female", "text": "甲"},
                {"start_seconds": 1, "end_seconds": 2, "voice": "male", "text": "乙"},
                {"start_seconds": 2, "end_seconds": 3, "voice": "both", "text": "丙"},
            ],
        }
        phase = np.arange(4000) * 2 * np.pi * 100 / rate
        tone = (.1 * np.sin(phase)).astype("float32")
        female = np.zeros((4000, 2), dtype="float32")
        male = np.zeros_like(female)
        female[:1000] = tone[:1000, None]
        female[2000:3000] = tone[2000:3000, None]
        male[1000:2000] = tone[1000:2000, None]
        male[2000:3000] = tone[2000:3000, None]
        source = np.repeat(tone[:, None], 2, axis=1)
        preview = source * .99
        good = validate_performance(plan, female, male, preview, source, rate)
        self.assertTrue(good["qualified"], good["errors"])

        dropped = preview.copy()
        dropped[1300:1700] *= .2
        bad = validate_performance(plan, female, male, dropped, source, rate)
        self.assertFalse(bad["qualified"])
        self.assertTrue(bad["level_mismatches"])


if __name__ == "__main__":
    unittest.main()
