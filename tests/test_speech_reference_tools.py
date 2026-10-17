import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import wave

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/verdantflare-speech/scripts"
sys.path.insert(0, str(SCRIPTS))
from prepare_voice_profile import inspect_reference, prepare, read_json
from morph_voice_reference import verify_pair


def wav(seconds=1, channels=1, width=2, sample=2000, rate=16000):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setparams((channels, width, rate, 0, "NONE", "NONE"))
        # Alternating finite samples; no real recording or synthesis dependency.
        output.writeframes((sample.to_bytes(width, "little", signed=True) + (-sample).to_bytes(width, "little", signed=True)) * (round(seconds * rate) * channels // 2))
    return stream.getvalue()


def capabilities():
    return {"engine": "indextts2", "configured_model_manifest_sha256": "a" * 64, "ready": True,
            "reference_seconds": {"min": 1, "max": 15}, "reference_max_bytes": 2 << 20}


def ref():
    return dict(zip(("store_id", "artifact_id", "version_id"),
                    [f"00000000-0000-7000-8000-{i:012d}" for i in range(1, 4)]))


def generation(data, text="同一句话。"):
    return {"request": {"text": text, "language": "zh-CN"},
            "runtime_outputs": [{"name": "original.wav", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}]}


class ReferenceTests(unittest.TestCase):
    def test_candidate_profile_has_only_contract_fields_and_actual_digest(self):
        data = wav()
        profile, intake = prepare(data, ref(), capabilities(), "voice-b", "旁白B")
        self.assertEqual(profile["reference"]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(set(profile), {"schema_version", "voice_id", "label", "engine", "model_manifest_sha256", "reference", "candidate_state", "listening_review"})
        self.assertEqual((profile["candidate_state"], profile["listening_review"]), ("candidate", "pending"))
        self.assertFalse(intake["remote_membership_verified"])
        self.assertIsNone(intake["transcript"])

    def test_pcm24_extensible_is_supported(self):
        data = wav(width=3)
        pcm = data[44:]
        fmt = struct.pack("<HHIIHHHHI", 0xfffe, 1, 16000, 48000, 3, 24, 22, 24, 4) + bytes.fromhex("0100000000001000800000aa00389b71")
        chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm
        extensible = b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks
        self.assertEqual(inspect_reference(extensible)["frames"], 16000)

    def test_rejects_silence_stereo_short_long_and_truncated_sources(self):
        for data in (wav(sample=0), wav(channels=2), wav(seconds=.9), wav(seconds=15.1), wav()[:-1], b"not audio"):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                inspect_reference(data)

    def test_rejects_truncated_data_even_if_riff_length_is_patched(self):
        data = bytearray(wav()[:-10])
        struct.pack_into("<I", data, 4, len(data) - 8)
        with self.assertRaisesRegex(ValueError, "truncated WAV chunk"):
            inspect_reference(bytes(data))

    def test_rejects_wrong_ids_and_missing_model_digest(self):
        for voice, reference, caps in (("../other", ref(), capabilities()), ("ok", {"artifact_id": "fake"}, capabilities()), ("ok", ref(), {})):
            with self.assertRaises(ValueError):
                prepare(wav(), reference, caps, voice, "test")

    def test_current_limits_and_clipping_are_not_hidden(self):
        caps = capabilities()
        caps["reference_seconds"]["min"] = 2
        with self.assertRaises(ValueError):
            prepare(wav(), ref(), caps, "test", "test")
        _, intake = prepare(wav(sample=32767), ref(), capabilities(), "test", "test")
        self.assertGreater(intake["reference"]["clipped_samples"], 0)
        self.assertIn("reference_contains_full_scale_samples", intake["warnings"])

    def test_cli_refuses_overwrite_without_changing_prior_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reference.wav").write_bytes(wav())
            (root / "ref.json").write_text(json.dumps(ref()))
            (root / "caps.json").write_text(json.dumps(capabilities()))
            command = [sys.executable, str(SCRIPTS / "prepare_voice_profile.py"), "--reference", str(root / "reference.wav"), "--content-ref", str(root / "ref.json"), "--capabilities", str(root / "caps.json"), "--voice-id", "test", "--label", "Test", "--out", str(root / "out")]
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            before = (root / "out/profile.json").read_bytes()
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 1)
            self.assertEqual((root / "out/profile.json").read_bytes(), before)

    def test_duplicate_json_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text('{"voice_id":"a","voice_id":"b"}')
            with self.assertRaisesRegex(ValueError, "duplicate"):
                read_json(path)


class MorphInputTests(unittest.TestCase):
    def test_same_text_provenance_accepts_distinct_voices(self):
        base, donor = wav(), wav(sample=1000)
        a, b = verify_pair(base, donor, generation(base), generation(donor), .35)
        self.assertNotEqual(a["sha256"], b["sha256"])

    def test_mismatch_text_hash_rate_and_weight_fail_before_rendering(self):
        base, donor = wav(), wav(sample=1000)
        cases = [(donor, generation(donor, "不同文本。"), .35),
                 (donor, generation(base), .35),
                 (wav(rate=22050), generation(wav(rate=22050)), .35),
                 (donor, generation(donor), float("nan")),
                 (donor, generation(donor), 0)]
        for audio, proof, weight in cases:
            with self.subTest(weight=weight), self.assertRaises(ValueError):
                verify_pair(base, audio, generation(base), proof, weight)


if __name__ == "__main__":
    unittest.main()
