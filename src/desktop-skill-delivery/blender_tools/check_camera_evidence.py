"""Measure supplied camera captures; native capture origin is verified separately.

Contract profile: frame 1, 1344x768, <=0.05 degree FOV difference,
<=8 pixel anchor drift, >=0.92 symmetric tolerant edge IoU at 3 pixels.
This tool does not generate, move, resize or align either input image.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import struct
from pathlib import Path, PurePosixPath

import validate_set_package as validator

RESOLUTION = (1344, 768)
EDGE_THRESHOLD = 32
EDGE_RADIUS = 3


def need(condition, code):
    if not condition:
        raise ValueError(code)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def read_image(root, item, *, mask=False):
    from PIL import Image
    import io
    need(isinstance(item, dict) and isinstance(item.get("path"), str), "INVALID_IMAGE_REFERENCE")
    name = item["path"]
    path = PurePosixPath(name)
    need(not path.is_absolute() and "\\" not in name
         and all(part not in ("", ".", "..") and ":" not in part for part in name.split("/")), "INVALID_IMAGE_PATH")
    disk = root.joinpath(*path.parts)
    need(not any(p.is_symlink() for p in (disk.absolute(), *disk.absolute().parents))
         and disk.is_file() and disk.stat().st_size <= 32 * 1024 * 1024, "INVALID_IMAGE_FILE")
    raw = disk.read_bytes()
    need(sha256(raw) == item.get("sha256"), "IMAGE_HASH_MISMATCH")
    # Inspect the fixed PNG header before Pillow can emit a decompression warning.
    need(len(raw) >= 33 and raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[8:16] == b"\0\0\0\rIHDR"
         and struct.unpack(">II", raw[16:24]) == RESOLUTION, "IMAGE_FORMAT_OR_SIZE_MISMATCH")
    with Image.open(io.BytesIO(raw)) as image:
        need(image.format == "PNG" and image.size == RESOLUTION and not getattr(image, "is_animated", False)
             and image.getexif().get(274, 1) == 1, "IMAGE_FORMAT_OR_SIZE_MISMATCH")
        image.load()
        if mask:
            need(image.mode == "L" and sum(image.histogram()[1:255]) == 0, "INVALID_STRUCTURE_MASK")
            return image.copy()
        need(image.mode in ("RGB", "RGBA"), "INVALID_CAPTURE_MODE")
        if image.mode == "RGBA":
            need(image.getchannel("A").getextrema() == (255, 255), "TRANSPARENT_CAPTURE")
        return image.convert("RGB")


def sobel_edges(image):
    from PIL import ImageChops, ImageDraw, ImageFilter
    gray = image.convert("L")
    x = (-1, 0, 1, -2, 0, 2, -1, 0, 1)
    y = (-1, -2, -1, 0, 0, 0, 1, 2, 1)
    def absolute_kernel(kernel):
        positive = gray.filter(ImageFilter.Kernel((3, 3), kernel, scale=1))
        negative = gray.filter(ImageFilter.Kernel((3, 3), tuple(-v for v in kernel), scale=1))
        return ImageChops.lighter(positive, negative)
    magnitude = ImageChops.add(absolute_kernel(x), absolute_kernel(y), scale=2)
    result = magnitude.point(lambda value: 255 if value >= EDGE_THRESHOLD else 0)
    draw = ImageDraw.Draw(result)
    draw.rectangle((0, 0, result.width - 1, result.height - 1), outline=0, width=1)
    return result


def compare_edges(first, second, mask=None):
    from PIL import ImageChops, ImageFilter
    need(first.size == second.size, "CAPTURE_DIMENSIONS_DIFFER")
    difference = ImageChops.difference(first.convert("RGB"), second.convert("RGB"))
    need(difference.getbbox() is not None,
         "DUPLICATE_CAPTURE_PIXELS")
    channel_max = ImageChops.lighter(ImageChops.lighter(*difference.split()[:2]), difference.split()[2])
    meaningful = channel_max.point(lambda v: 255 if v >= 2 else 0).histogram()[255]
    total_pixels = first.width * first.height
    need(meaningful >= max(2, math.ceil(total_pixels * .0001)), "NEAR_DUPLICATE_CAPTURE_PIXELS")
    left, right = sobel_edges(first), sobel_edges(second)
    unmasked_left, unmasked_right = left.histogram()[255], right.histogram()[255]
    if mask is not None:
        need(mask.size == first.size, "MASK_DIMENSIONS_DIFFER")
        need(mask.histogram()[255] >= math.ceil(total_pixels * .01), "INSUFFICIENT_MASK_AREA")
        left, right = ImageChops.multiply(left, mask), ImageChops.multiply(right, mask)
    count_left, count_right = left.histogram()[255], right.histogram()[255]
    minimum_edges = max(16, math.ceil(total_pixels * .0001))
    need(count_left >= minimum_edges and count_right >= minimum_edges, "INSUFFICIENT_STRUCTURE")
    if mask is not None:
        need(count_left >= unmasked_left * .25 and count_right >= unmasked_right * .25,
             "INSUFFICIENT_MASK_STRUCTURE")
    wide_left = left.filter(ImageFilter.MaxFilter(2 * EDGE_RADIUS + 1))
    wide_right = right.filter(ImageFilter.MaxFilter(2 * EDGE_RADIUS + 1))
    matched_left = ImageChops.multiply(left, wide_right).histogram()[255]
    matched_right = ImageChops.multiply(right, wide_left).histogram()[255]
    intersection = (matched_left + matched_right) / 2.0
    union = count_left + count_right - intersection
    return {"edge_iou": intersection / union, "reference_edges": count_left,
            "web_edges": count_right, "matched_reference_edges": matched_left,
            "matched_web_edges": matched_right, "tolerance_px": EDGE_RADIUS,
            "sobel_l1_half_threshold": EDGE_THRESHOLD,
            "meaningfully_different_pixels": meaningful, "minimum_structure_edges": minimum_edges,
            "unmasked_reference_edges": unmasked_left, "unmasked_web_edges": unmasked_right,
            "mask_pixels": mask.histogram()[255] if mask else first.width * first.height}


def anchors(value):
    need(isinstance(value, list) and bool(value), "MISSING_OBSERVED_ANCHORS")
    result = {}
    for item in value:
        need(isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
             and item["id"] not in result, "INVALID_ANCHOR_ID")
        pixel = item.get("pixel")
        need(isinstance(pixel, list) and len(pixel) == 2
             and all(validator._finite_number(v) for v in pixel)
             and 0 <= pixel[0] < RESOLUTION[0] and 0 <= pixel[1] < RESOLUTION[1], "INVALID_ANCHOR_PIXEL")
        result[item["id"]] = pixel
    return result


def measure(package, evidence_file):
    package_report = validator.validate_set_package(package)
    need(package_report["ok"], "INVALID_SET_PACKAGE")
    need(not any(p.is_symlink() for p in (evidence_file.absolute(), *evidence_file.absolute().parents))
         and evidence_file.is_file() and evidence_file.stat().st_size <= 1024 * 1024, "INVALID_EVIDENCE_FILE")
    evidence_bytes = evidence_file.read_bytes()
    evidence = validator._strict_json(evidence_bytes.decode("utf-8"))
    need(isinstance(evidence, dict) and evidence.get("schema") == "vf.camera-evidence/v1", "INVALID_EVIDENCE_SCHEMA")
    manifest_bytes = (package / "set.json").read_bytes()
    manifest = validator._strict_json(manifest_bytes.decode("utf-8"))
    need(evidence.get("manifest_sha256") == sha256(manifest_bytes)
         and evidence.get("scene_sha256") == sha256((package / "scene.glb").read_bytes()), "PACKAGE_BINDING_MISMATCH")
    need(manifest["render_defaults"]["resolution"] == list(RESOLUTION), "CONTRACT_RESOLUTION_REQUIRED")
    expected = {item["id"]: item for item in manifest["cameras"]}
    rows = evidence.get("cameras")
    need(isinstance(rows, list) and len(rows) == len(expected), "CAMERA_COVERAGE_MISMATCH")
    seen, capture_refs, results = set(), set(), []
    for row in rows:
        need(isinstance(row, dict) and isinstance(row.get("camera_id"), str)
             and row["camera_id"] in expected and row["camera_id"] not in seen, "INVALID_EVIDENCE_CAMERA")
        cid = row["camera_id"]
        seen.add(cid)
        need(type(row.get("frame")) is int and row["frame"] == 1
             and row.get("resolution") == list(RESOLUTION), "CAPTURE_FRAME_OR_RESOLUTION_MISMATCH")
        original, web = row.get("blender"), row.get("web")
        need(isinstance(original, dict) and isinstance(web, dict), "MISSING_CAPTURE")
        need(all(isinstance(item.get("capture_ref"), str) and item["capture_ref"].strip() for item in (original, web))
             and original["capture_ref"] != web["capture_ref"], "DISTINCT_CAPTURE_REFERENCES_REQUIRED")
        for item in (original, web):
            need(item["capture_ref"] not in capture_refs, "REUSED_CAPTURE_REFERENCE")
            capture_refs.add(item["capture_ref"])
            need(isinstance(item.get("image"), dict), "INVALID_IMAGE_REFERENCE")
        camera = expected[cid]
        reference = camera.get("reference_image")
        need(isinstance(reference, str) and original["image"].get("sha256") == sha256((package / reference).read_bytes()), "REFERENCE_IMAGE_NOT_FROM_PACKAGE")
        first = read_image(evidence_file.parent, original.get("image"))
        second = read_image(evidence_file.parent, web.get("image"))
        mask = None
        if "structure_mask" in row:
            item = row["structure_mask"]
            need(isinstance(item, dict) and isinstance(item.get("rationale"), str) and item["rationale"].strip()
                 and item.get("baseline_manifest_sha256") == evidence["manifest_sha256"], "UNBOUND_STRUCTURE_MASK")
            mask = read_image(evidence_file.parent, item, mask=True)
        left, right = anchors(original.get("anchors")), anchors(web.get("anchors"))
        required = row.get("anchor_ids")
        need(isinstance(required, list) and bool(required) and all(isinstance(v, str) and v for v in required)
             and len(set(required)) == len(required) and set(required) == set(left) == set(right), "ANCHOR_COVERAGE_MISMATCH")
        drift = max(math.dist(left[name], right[name]) for name in required)
        if camera["projection"] == "PERSP":
            theoretical = camera.get("fov_y_deg")
            if theoretical is None:
                theoretical = validator.compute_vertical_fov_deg(camera["lens_mm"],
                    camera.get("sensor_width_mm", 36), camera.get("sensor_height_mm", 24),
                    camera.get("sensor_fit", "AUTO"), *RESOLUTION)
            values = [original.get("fov_y_deg"), web.get("fov_y_deg"), theoretical]
            need(all(validator._finite_number(v) and 0 < v < 180 for v in values), "INVALID_OBSERVED_FOV")
            fov_error = max(abs(values[i] - values[j]) for i, j in ((0, 1), (0, 2), (1, 2)))
            optics_pass = fov_error <= .05
            ortho_error = None
        else:
            values = [original.get("ortho_width_m"), web.get("ortho_width_m"), camera["ortho_scale"]]
            need(all(validator._finite_number(v) and v > 0 for v in values), "INVALID_OBSERVED_ORTHO_WIDTH")
            ortho_error = max(abs(values[i] - values[j]) for i, j in ((0, 1), (0, 2), (1, 2)))
            optics_pass = all(math.isclose(values[i], values[j], rel_tol=1e-6, abs_tol=1e-8) for i, j in ((0, 1), (0, 2), (1, 2)))
            fov_error = None
        edges = compare_edges(first, second, mask)
        results.append({"camera_id": cid, "thresholds_passed": optics_pass and drift <= 8 and edges["edge_iou"] >= .92,
                        "fov_error_deg": fov_error, "ortho_width_error_m": ortho_error,
                        "max_anchor_drift_px": drift, "anchor_ids": required, "edges": edges,
                        "blender_capture_ref": original["capture_ref"], "web_capture_ref": web["capture_ref"],
                        "structure_mask_requires_independent_review": mask is not None})
    return {"schema": "vf.camera-measurement/v1", "scope": "supplied_capture_measurements",
            "thresholds_passed": all(item["thresholds_passed"] for item in results),
            "manifest_sha256": evidence["manifest_sha256"], "scene_sha256": evidence["scene_sha256"],
            "evidence_sha256": sha256(evidence_bytes),
            "native_capture_origin_verified": False, "desktop_or_project_acceptance": False, "cameras": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = measure(args.package_dir, args.evidence)
    need(not any(p.is_symlink() for p in (args.output.absolute(), *args.output.absolute().parents)), "LINKED_OUTPUT_PATH")
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"thresholds_passed": report["thresholds_passed"], "native_capture_origin_verified": False}))
    return 0 if report["thresholds_passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RecursionError) as exc:
        # Do not print absolute local paths from filesystem exceptions.
        code = str(exc) if type(exc) is ValueError and str(exc).isupper() and " " not in str(exc) else "INVALID_CAMERA_EVIDENCE"
        print(json.dumps({"error": code, "thresholds_passed": False}), file=sys.stderr)
        raise SystemExit(2)
