import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate_plan import validate


def valid_plan():
    return {
        "schema_version": 1,
        "project_ref": "demo-film",
        "kind": "film",
        "duration_ms": 10000,
        "aspect_ratio": "16:9",
        "source": {"type": "script", "reference": "script-v1"},
        "assets": {
            "characters": [{"id": "C01", "description": "Adult passenger"}],
            "looks": [{"id": "L01", "character_id": "C01", "description": "Gray coat"}],
            "locations": [{"id": "LOC01", "description": "Rainy station"}],
            "props": [{"id": "P01", "description": "Black umbrella"}],
        },
        "shots": [
            {
                "id": "S01", "start_ms": 0, "end_ms": 4000,
                "purpose": "Establish the station", "action": "Passenger enters",
                "continuity_group": "B01",
                "start_state": "Platform empty", "end_state": "Passenger by the door",
                "audio": "Rain and footsteps", "location_id": "LOC01",
                "character_ids": ["C01"], "look_ids": ["L01"], "prop_ids": ["P01"],
                "camera": {"framing": "Wide", "position": "Across platform", "movement": "static"},
            },
            {
                "id": "S02", "start_ms": 4000, "end_ms": 10000,
                "purpose": "Reveal the locked door", "action": "Passenger tries handle",
                "continuity_group": "B01",
                "start_state": "Passenger by the door", "end_state": "Hand releases handle",
                "audio": "Handle click", "location_id": "LOC01",
                "character_ids": ["C01"], "look_ids": ["L01"], "prop_ids": ["P01"],
                "camera": {"framing": "Medium", "position": "Door side", "movement": "slow push in"},
            },
        ],
        "generation_units": [{
            "id": "GU01", "start_ms": 0, "end_ms": 10000,
            "shot_ids": ["S01", "S02"], "form": "internal_multi_shot", "model": "h3",
        }],
    }


class PlanValidationTests(unittest.TestCase):
    def test_valid_h3_handoff(self):
        self.assertEqual(validate(valid_plan()), [])

    def test_timeline_gap_and_wrong_look(self):
        plan = valid_plan()
        plan["shots"][1]["start_ms"] = 4500
        plan["assets"]["looks"][0]["character_id"] = "C02"
        errors = validate(plan)
        self.assertTrue(any("previous end_ms" in error for error in errors))
        self.assertTrue(any("references no character" in error for error in errors))

    def test_unit_cannot_repeat_or_exceed_h3_shots(self):
        plan = valid_plan()
        plan["generation_units"][0]["shot_ids"] = ["S01", "S02", "S02", "S02"]
        errors = validate(plan)
        self.assertTrue(any("2-3 Shots" in error for error in errors))
        self.assertTrue(any("cover every Shot once" in error for error in errors))

    def test_unit_cannot_cross_continuity_group(self):
        plan = valid_plan()
        plan["shots"][1]["continuity_group"] = "B02"
        self.assertTrue(any("continuity groups" in error for error in validate(plan)))

    def test_canonical_h3_model_uses_same_limits(self):
        plan = valid_plan()
        plan["generation_units"][0]["model"] = "minimax-h3-ref2va"
        plan["generation_units"][0]["form"] = "continuous_single_shot"
        self.assertTrue(any("must contain one Shot" in error for error in validate(plan)))

    def test_advertisement_requires_evidence_and_cta(self):
        plan = valid_plan()
        plan["kind"] = "advertisement"
        self.assertTrue(any("advertisement brief" in error for error in validate(plan)))
        plan["brief"] = {
            "benefit": "Easy to carry", "evidence": "Fits the shown bag",
            "cta": "View details", "prohibited_claims": [],
        }
        self.assertEqual(validate(plan), [])

    def test_malformed_fields_report_errors(self):
        plan = copy.deepcopy(valid_plan())
        plan["shots"][0]["location_id"] = []
        plan["shots"][0]["character_ids"] = 4
        plan["generation_units"][0]["id"] = []
        errors = validate(plan)
        self.assertTrue(any("location_id" in error for error in errors))
        self.assertTrue(any("character_ids" in error for error in errors))
        self.assertTrue(any("generation_units[0].id" in error for error in errors))

    def test_command_line_handoff(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "validate_plan.py"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text(json.dumps(valid_plan()), encoding="utf-8")
            result = subprocess.run([sys.executable, str(script), str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            plan = valid_plan()
            plan["generation_units"][0]["end_ms"] = 3000
            path.write_text(json.dumps(plan), encoding="utf-8")
            result = subprocess.run([sys.executable, str(script), str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("ERROR:", result.stdout)


if __name__ == "__main__":
    unittest.main()
