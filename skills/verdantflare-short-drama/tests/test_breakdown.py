import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from breakdown import anchor_candidate, local_endpoint, write_new_json


class BreakdownTests(unittest.TestCase):
    def test_local_endpoints_only(self):
        self.assertEqual(local_endpoint("http://127.0.0.1:11434/"), "http://127.0.0.1:11434")
        self.assertEqual(local_endpoint("http://localhost:11434"), "http://localhost:11434")
        for endpoint in ("https://127.0.0.1:11434", "http://example.com:11434", "http://127.0.0.1:11434/x"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                local_endpoint(endpoint)

    def test_unquoted_model_claim_is_rejected(self):
        source = "雨夜站台，阿宁拿着黑伞跑到候车室门口，试着拉门，门没开。"
        candidate = {
            "facts": [
                {"kind": "character", "value": "阿宁", "evidence": "阿宁拿着黑伞"},
                {"kind": "prop", "value": "红伞", "evidence": "阿宁拿着黑伞"},
            ],
            "beats": [{"action": "拉门", "evidence": "试着拉门，门没开"}],
            "inferences": [{"idea": "她在等朋友", "basis": "她独自等待朋友"}],
            "questions": ["她为什么来这里？"],
        }
        result = anchor_candidate(candidate, source)
        self.assertEqual(len(result["facts"]), 1)
        self.assertEqual(len(result["beats"]), 1)
        self.assertEqual(len(result["inferences"]), 0)
        self.assertEqual(len(result["rejected"]), 2)

    def test_beat_must_match_its_own_evidence(self):
        source = "她试着拉门。她停下，看向末班车。"
        candidate = {
            "facts": [],
            "beats": [{"action": "试着拉门", "evidence": "她停下，看向末班车。"}],
            "inferences": [], "questions": [],
        }
        result = anchor_candidate(candidate, source)
        self.assertEqual(result["beats"], [])
        self.assertEqual(result["rejected"][0]["reason"], "beat action does not occur in its evidence")

    def test_grounded_inference_still_needs_review(self):
        source = "她停下，看向末班车驶离的方向。"
        candidate = {
            "facts": [], "beats": [],
            "inferences": [{"idea": "她可能失望", "basis": "她停下，看向末班车驶离的方向。"}],
            "questions": [],
        }
        result = anchor_candidate(candidate, source)
        self.assertTrue(result["inferences"][0]["needs_review"])

    def test_malformed_fact_is_rejected_without_crashing(self):
        result = anchor_candidate({
            "facts": [{"kind": [], "value": "阿宁", "evidence": "阿宁"}],
            "beats": [], "inferences": [], "questions": [],
        }, "阿宁")
        self.assertEqual(result["facts"], [])
        self.assertEqual(result["rejected"][0]["reason"], "invalid fact fields")

    def test_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "breakdown.json"
            write_new_json(path, {"version": 1})
            with self.assertRaises(FileExistsError):
                write_new_json(path, {"version": 2})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"version": 1})


if __name__ == "__main__":
    unittest.main()
