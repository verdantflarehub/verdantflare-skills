import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/verdantflare-music/scripts"
sys.path.insert(0, str(SCRIPTS))
from validate_score_plan import validate


def plan():
    return {"schema_version": "background-score-v1", "duration_seconds": 10,
            "cues": [{"cue_id": "M1", "timeline_start": 0, "timeline_end": 4,
                      "source_start": 1, "source_end": 5, "source_duration_seconds": 6,
                      "source_sha256": "a" * 64, "vocal_review": "pending"},
                     {"cue_id": "M2", "timeline_start": 6, "timeline_end": 10,
                      "source_start": 0, "source_end": 4, "source_duration_seconds": 4,
                      "source_sha256": "b" * 64, "vocal_review": "pending"}],
            "silences": [{"start": 4, "end": 6, "reason": "Approved pause"}]}


class ScoreTests(unittest.TestCase):
    def test_complete_linear_timeline_does_not_claim_listening(self):
        result = validate(plan())
        self.assertTrue(result["technical_pass"])
        self.assertEqual(result["review_pending"], ["M1", "M2"])

    def test_source_shortage_cannot_be_hidden_by_target_duration(self):
        value = plan()
        value["cues"][1]["source_duration_seconds"] = 3.5
        self.assertIn("M2:source_out_of_bounds", validate(value)["errors"])

    def test_gaps_overlap_silence_conflicts_and_implicit_stretch_fail(self):
        variants = []
        gap = plan(); gap["silences"] = []; variants.append((gap, "uncovered_interval"))
        overlap = plan(); overlap["cues"][1]["timeline_start"] = 5; variants.append((overlap, "overlap_or_silence_conflict"))
        stretch = plan(); stretch["cues"][0]["source_end"] = 4; variants.append((stretch, "implicit_time_stretch_or_padding"))
        tail = plan(); tail["duration_seconds"] = 11; variants.append((tail, "uncovered_tail"))
        for value, message in variants:
            with self.subTest(message=message):
                result = validate(value)
                self.assertFalse(result["technical_pass"])
                self.assertTrue(any(message in error for error in result["errors"]))

    def test_false_nan_inf_and_invalid_ids_are_rejected(self):
        for invalid in (False, float("nan"), float("inf"), "6"):
            value = plan(); value["cues"][1]["timeline_start"] = invalid
            self.assertFalse(validate(value)["technical_pass"])
        value = plan(); value["cues"][1]["cue_id"] = "M1"
        self.assertIn("M1:duplicate_id", validate(value)["errors"])

    def test_bpm_is_actual_measurement_not_a_prompt(self):
        value = plan(); cue = value["cues"][0]; cue["required_bpm"] = 128
        self.assertIn("M1:bpm_measurement_required", validate(value)["errors"])
        cue.update(measured_bpm=101.33, bpm_tolerance=1)
        self.assertIn("M1:bpm_mismatch", validate(value)["errors"])

    def test_clear_without_evidence_remains_pending(self):
        value = plan(); value["cues"][0]["vocal_review"] = "clear"
        self.assertIn("M1", validate(value)["review_pending"])

    def test_cli_exit_codes_distinguish_pending_failure_and_reviewed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            value = plan()
            for state, expected in (("pending", 2), ("present", 1), ("clear", 0)):
                for cue in value["cues"]:
                    cue.update(vocal_review=state, vocal_review_evidence="review/decision.json")
                path.write_text(json.dumps(value))
                result = subprocess.run([sys.executable, str(SCRIPTS / "validate_score_plan.py"), str(path)], capture_output=True)
                self.assertEqual(result.returncode, expected, result.stdout)


if __name__ == "__main__":
    unittest.main()
