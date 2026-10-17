#!/usr/bin/env python3
"""Experimental same-text WORLD reference morph; run media processing on 5090 dev."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path

from prepare_voice_profile import inspect_reference, read_json, require


def verify_source(data, generation):
    require(isinstance(generation, dict), "expected Speech generation.json")
    request = generation.get("request", {})
    require(isinstance(request, dict), "invalid generation request")
    require(isinstance(request.get("text"), str) and request["text"].strip(), "missing generated text")
    require(request.get("language") == "zh-CN", "only verified zh-CN sources are supported")
    info = inspect_reference(data)
    outputs = generation.get("runtime_outputs", [])
    require(isinstance(outputs, list), "invalid runtime_outputs")
    matches = [item for item in outputs if isinstance(item, dict) and item.get("name") == "original.wav"]
    require(len(matches) == 1 and matches[0].get("sha256") == info["sha256"]
            and matches[0].get("size") == info["size"], "original.wav does not match generation provenance")
    return request, info


def verify_pair(base_data, donor_data, base_generation, donor_generation, weight):
    require(type(weight) in (int, float) and math.isfinite(weight) and 0 < weight < 1, "weight must be finite and strictly between 0 and 1")
    base_request, base_info = verify_source(base_data, base_generation)
    donor_request, donor_info = verify_source(donor_data, donor_generation)
    require(base_request["text"] == donor_request["text"], "sources must contain the same generated text")
    require(base_info["sample_rate_hz"] == donor_info["sample_rate_hz"], "source sample rates must match; no implicit resampling")
    require(base_info["sha256"] != donor_info["sha256"], "sources must be distinct")
    return base_info, donor_info


def features(spectrum, rate):
    import numpy as np
    from scipy.fft import dct

    hz = np.linspace(0, rate / 2, spectrum.shape[1])
    mel = lambda value: 2595 * np.log10(1 + value / 700)
    points = 700 * (10 ** (np.linspace(mel(60), mel(min(9000, rate / 2)), 66) / 2595) - 1)
    bank = np.stack([np.maximum(0, np.minimum((hz - left) / (center - left), (right - hz) / (right - center)))
                     for left, center, right in zip(points[:-2], points[1:-1], points[2:])])
    return dct(np.log(spectrum @ bank.T + 1e-12), type=2, norm="ortho", axis=1)[:, 1:15]


def alignment(base, donor):
    import numpy as np

    require(base.ndim == donor.ndim == 2 and base.shape[1] == donor.shape[1] and
            0 < len(base) <= 3100 and 0 < len(donor) <= 3100 and
            np.isfinite(base).all() and np.isfinite(donor).all(), "invalid or oversized alignment features")
    n, m = len(base), len(donor)
    scale = np.std(np.vstack([base, donor]), axis=0) + 1e-6
    base, donor = base / scale, donor / scale
    cost = np.full((n + 1, m + 1), np.inf)
    cost[0, 0] = 0
    back = np.zeros((n, m), np.uint8)
    for i in range(n):
        for j in range(max(0, int(i * m / n - .27 * m) - 20), min(m, int(i * m / n + .27 * m) + 21)):
            choices = [cost[i, j], cost[i, j + 1] + .3, cost[i + 1, j] + .3]
            step = int(np.argmin(choices))
            cost[i + 1, j + 1] = choices[step] + np.mean((base[i] - donor[j]) ** 2)
            back[i, j] = step
    require(np.isfinite(cost[n, m]), "acoustic alignment failed")
    i, j = n - 1, m - 1
    links = [[] for _ in range(n)]
    while i >= 0 and j >= 0:
        links[i].append(j)
        step = back[i, j]
        if step == 0:
            i, j = i - 1, j - 1
        elif step == 1:
            i -= 1
        else:
            j -= 1
    require(all(links), "incomplete acoustic alignment")
    mapping = np.array([np.mean(values) for values in links])
    require(np.all(np.diff(mapping) >= 0), "non-monotonic acoustic alignment")
    return mapping, float(cost[n, m] / (n + m))


def render(base_data, donor_data, rate, weight):
    import io
    import numpy as np
    import soundfile as sf
    import pyworld as pw
    from scipy.ndimage import gaussian_filter1d

    def analyze(data):
        signal, actual_rate = sf.read(io.BytesIO(data), dtype="float64")
        require(actual_rate == rate and signal.ndim == 1 and np.isfinite(signal).all(), "invalid decoded source")
        f0, times = pw.harvest(signal, rate, f0_floor=65, f0_ceil=450, frame_period=5)
        f0 = pw.stonemask(signal, f0, times, rate)
        return signal, f0, times, pw.cheaptrick(signal, f0, times, rate), pw.d4c(signal, f0, times, rate)

    source, f0, times, spectrum, aperiodicity = analyze(base_data)
    _, donor_f0, _, donor_spectrum, donor_aperiodicity = analyze(donor_data)
    mapping, cost = alignment(features(spectrum, rate), features(donor_spectrum, rate))
    positions = np.arange(len(donor_spectrum))
    mapped = lambda matrix: np.stack([np.interp(mapping, positions, matrix[:, column]) for column in range(matrix.shape[1])], axis=1)
    target, target_ap = mapped(donor_spectrum), mapped(donor_aperiodicity)
    donor_voiced = np.interp(mapping, positions, (donor_f0 > 0).astype(float))
    voiced = (f0 > 0) & (donor_voiced > .5)
    require(np.any(voiced), "no shared voiced frames; cannot create a morph")
    mask = gaussian_filter1d(voiced.astype(float), 1.2)
    target *= (spectrum.sum(axis=1) / (target.sum(axis=1) + 1e-15))[:, None]
    weights = (weight * mask)[:, None]
    blend = np.exp((1 - weights) * np.log(spectrum + 1e-15) + weights * np.log(target + 1e-15))
    blend *= (spectrum.sum(axis=1) / (blend.sum(axis=1) + 1e-15))[:, None]
    ap_blend = np.clip((1 - weights) * aperiodicity + weights * target_ap, 0, 1)
    synthesized = pw.synthesize(f0, np.ascontiguousarray(blend), np.ascontiguousarray(ap_blend), rate, 5.0)
    # WORLD's frame grid may extend past the original sample grid; retain the base length.
    synthesized = synthesized[:len(source)]
    require(len(synthesized) == len(source), "synthesis shorter than base source")
    sample_mask = np.interp(np.arange(len(source)) / rate, times, mask)
    output = synthesized * sample_mask + source * (1 - sample_mask)
    require(np.isfinite(output).all() and np.max(np.abs(output)) > 0, "invalid synthesized audio")
    # Attenuation only: this is headroom, not clipped-waveform restoration.
    gain = min(1.0, 10 ** (-3 / 20) / np.max(np.abs(output)))
    output *= gain
    return output, {"alignment_normalized_cost": cost, "shared_voiced_fraction": float(np.mean(voiced)),
                    "base_f0_used_for_resynthesis": True, "base_sample_count_retained": True,
                    "output_gain": float(gain), "headroom_ceiling_dbfs": -3}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "donor", "base-generation", "donor-generation", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--weight", required=True, type=float, help="Spectral interpolation weight, not identity similarity")
    args = parser.parse_args()
    try:
        require(not args.out.exists(), "output directory already exists")
        require(args.base.stat().st_size <= 2 << 20 and args.donor.stat().st_size <= 2 << 20, "source exceeds reference limits")
        base_data, donor_data = args.base.read_bytes(), args.donor.read_bytes()
        base_generation, donor_generation = read_json(args.base_generation), read_json(args.donor_generation)
        base_info, donor_info = verify_pair(base_data, donor_data, base_generation, donor_generation, args.weight)
        versions = {name: importlib.metadata.version(name) for name in ("numpy", "scipy", "soundfile", "pyworld")}
        output, analysis = render(base_data, donor_data, base_info["sample_rate_hz"], args.weight)
        import soundfile as sf
        args.out.mkdir(parents=True, exist_ok=False)
        target = args.out / "reference.wav"
        sf.write(target, output, base_info["sample_rate_hz"], subtype="PCM_24")
        info = inspect_reference(target.read_bytes())
        require(info["frames"] == base_info["frames"] and info["clipped_samples"] == 0, "rendered output validation failed")
        report = {"schema_version": 1, "status": "experimental_candidate", "listening_review": "pending",
                  "method": "WORLD_same_text_spectral_envelope_interpolation", "weight": args.weight,
                  "dependencies": versions, "base": base_info, "donor": donor_info,
                  "base_task_id": base_generation.get("task_id"), "donor_task_id": donor_generation.get("task_id"),
                  "text_sha256": hashlib.sha256(base_generation["request"]["text"].encode()).hexdigest(),
                  "analysis": analysis, "output": info,
                  "warnings": ["vocoder_artifacts_possible", "acoustic_alignment_not_phoneme_verified",
                               "background_music_not_removed", "cross_text_identity_unverified"] +
                              (["source_clipping_not_repaired"] if base_info["clipped_samples"] or donor_info["clipped_samples"] else [])}
        (args.out / "morph-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "listening_review": "pending", "output_sha256": info["sha256"]}))
    except (OSError, ValueError, ImportError, importlib.metadata.PackageNotFoundError) as error:
        print(json.dumps({"status": "failed", "error": str(error) if isinstance(error, ValueError) else type(error).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
