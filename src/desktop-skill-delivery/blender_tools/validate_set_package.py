#!/usr/bin/env python3
"""Validator and math utilities for Studio Set Package v1.0/v1.1 (set-package-v1).

Validates a set package directory against docs/design/contracts/set-package-v1.md:
- set.json schema_version in ("1.0", "1.1") and structural constraints
- Blender Z-up -> glTF/Three.js Y-up coordinate transform & vertical FOV formula
- Forbidden file types (.blend, .blend1, .py) and symlink rejection
- File count (<= 32), scene.glb size (<= 25 MiB), total package size (<= 64 MiB)
- Exact file size and SHA-256 verification for every file in files[]
- glTF 2.0 binary header & JSON chunk inspection (node count <= 180)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import re
import struct
import sys
import uuid
from typing import Any

MAX_PACKAGE_FILES = 32
MAX_PACKAGE_BYTES = 64 * 1024 * 1024  # 64 MiB
MAX_GLB_BYTES = 25 * 1024 * 1024      # 25 MiB
MAX_GLB_NODES = 180

PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}$")
CAM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_SUFFIXES = {".blend", ".blend1", ".py", ".pyc", ".sh", ".bat", ".ps1"}
ALLOWED_SUFFIXES = {".json", ".glb", ".gltf", ".ktx2", ".png", ".jpg", ".jpeg", ".webp", ".bin"}


def _finite_number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _strict_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def finite(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        return value
    def constant(raw):
        raise ValueError("non-finite JSON number")
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_float=finite, parse_constant=constant)
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds parser limit") from exc


def blender_to_gltf_point(pt: list[float] | tuple[float, float, float]) -> tuple[float, float, float]:
    """Convert Blender Z-up (x_b, y_b, z_b) to glTF/Three.js Y-up (x_b, z_b, -y_b)."""
    if not isinstance(pt, (list, tuple)) or len(pt) != 3 or not all(_finite_number(v) for v in pt):
        raise ValueError("Point must have 3 coordinates")
    return (float(pt[0]), float(pt[2]), -float(pt[1]))


def compute_vertical_fov_deg(
    lens_mm: float,
    sensor_width_mm: float = 36.0,
    sensor_height_mm: float = 24.0,
    sensor_fit: str = "AUTO",
    res_x: int = 1344,
    res_y: int = 768,
) -> float:
    """Compute Three.js PerspectiveCamera vertical FOV in degrees per Contract Section 4.2."""
    if not all(_finite_number(v) and v > 0 for v in (lens_mm, sensor_width_mm, sensor_height_mm, res_x, res_y)):
        raise ValueError("Camera lens, sensor, and resolution must be positive")
    if type(res_x) is not int or type(res_y) is not int:
        raise ValueError("Resolution must use positive integers")
    if sensor_fit not in ("AUTO", "VERTICAL", "HORIZONTAL"):
        raise ValueError("Invalid sensor fit")
    # Evaluate h_eff / (2 * lens) in log space to avoid intermediate
    # overflow (including inf/inf) with otherwise finite numeric inputs.
    if sensor_fit == "VERTICAL" or (sensor_fit == "AUTO" and res_x < res_y):
        log_ratio = math.log(sensor_height_mm) - math.log(2.0) - math.log(lens_mm)
    else:
        log_ratio = (math.log(sensor_width_mm) - math.log(2.0) - math.log(lens_mm)
                     + math.log(res_y) - math.log(res_x))
    if log_ratio > 0:
        fov = 180.0 - math.degrees(2.0 * math.atan(math.exp(-log_ratio)))
    else:
        fov = math.degrees(2.0 * math.atan(math.exp(log_ratio)))
    if not math.isfinite(fov) or not 0.0 < fov < 180.0:
        raise ValueError("Camera FOV cannot be represented in (0, 180) degrees")
    return fov


def inspect_glb_header(glb_path: pathlib.Path) -> dict[str, Any]:
    """Parse GLB 12-byte header and JSON chunk0 to verify glTF 2.0 validity and node count."""
    glb_path = pathlib.Path(glb_path)
    if (any(p.is_symlink() for p in (glb_path.absolute(), *glb_path.absolute().parents))
            or not glb_path.is_file() or glb_path.stat().st_size > MAX_GLB_BYTES):
        raise ValueError("INVALID_GLB_HEADER: linked, non-regular or oversized GLB")
    data = glb_path.read_bytes()
    if len(data) < 20:
        raise ValueError("INVALID_GLB_HEADER: file shorter than 20 bytes")
    magic, version, total_len = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("INVALID_GLB_HEADER: magic is not 'glTF'")
    if version != 2:
        raise ValueError(f"INVALID_GLB_HEADER: unsupported glTF version {version}")
    if total_len != len(data):
        raise ValueError(f"INVALID_GLB_HEADER: declared length {total_len} != actual {len(data)}")
    chunk0_len, chunk0_type = struct.unpack_from("<I4s", data, 12)
    if chunk0_type != b"JSON":
        raise ValueError("INVALID_GLB_HEADER: first chunk is not JSON")
    if 20 + chunk0_len > len(data):
        raise ValueError("INVALID_GLB_HEADER: JSON chunk exceeds file size")
    gltf_json = _strict_json(data[20 : 20 + chunk0_len].decode("utf-8"))
    if not isinstance(gltf_json, dict):
        raise ValueError("INVALID_GLB_HEADER: JSON chunk must be an object")
    asset = gltf_json.get("asset")
    if not isinstance(asset, dict) or asset.get("version") != "2.0":
        raise ValueError("INVALID_GLB_HEADER: asset.version must be 2.0")
    for field in ("nodes", "meshes", "materials", "textures", "images"):
        value = gltf_json.get(field, [])
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError("INVALID_GLB_HEADER: invalid array of objects")
    nodes = gltf_json.get("nodes") or []
    if any("name" in node and not isinstance(node["name"], str) for node in nodes):
        raise ValueError("INVALID_GLB_HEADER: node name must be text")
    meshes = gltf_json.get("meshes") or []
    materials = gltf_json.get("materials") or []
    textures = gltf_json.get("textures") or []
    images = gltf_json.get("images") or []
    return {
        "version": version,
        "byte_length": len(data),
        "node_count": len(nodes),
        "mesh_count": len(meshes),
        "material_count": len(materials),
        "texture_count": len(textures),
        "image_count": len(images),
        "node_names": [node.get("name", "") for node in nodes],
    }


def _is_vec3(val: Any) -> bool:
    return isinstance(val, list) and len(val) == 3 and all(_finite_number(x) for x in val)


def _validate_layers(manifest: dict, node_names: list[str], err) -> None:
    """Validate v1.1 declarations only; identities still require service ACL checks."""
    groups = manifest.get("object_groups", [])
    if (not isinstance(groups, list)
            or any(not isinstance(group, dict) or not isinstance(group.get("prefix"), str)
                   for group in groups)):
        err("INVALID_OBJECT_GROUPS", "object_groups must contain objects with text prefixes")
        groups = []
    fields = ("kind", "lineage", "cast")
    if manifest.get("schema_version") == "1.0":
        if any(key in manifest for key in fields):
            err("SCHEMA_VERSION_MISMATCH", "Layer fields require schema_version 1.1")
        return
    if manifest.get("schema_version") != "1.1":
        return
    kind = manifest.get("kind", "setup")
    if kind not in ("world_empty", "project_state", "setup"):
        err("INVALID_SET_KIND", "kind must be world_empty, project_state or setup")
        return
    if kind != "setup" and ("cast" in manifest
                            or any(group["prefix"].startswith("ACTOR_") for group in groups)
                            or any(name.startswith("ACTOR_") for name in node_names)):
        err("EMPTY_SET_HAS_ACTORS", "Empty sets cannot contain cast, actor groups or actor nodes")
    lineage = manifest.get("lineage", {})
    if not isinstance(lineage, dict):
        err("INVALID_LINEAGE", "lineage must be an object")
        return
    allowed = {"world_asset"} if kind == "world_empty" else {"world_asset", "project_scene"}
    if kind == "setup":
        allowed.add("setup")
        if "lineage" in manifest and not {"project_scene", "setup"}.issubset(lineage):
            err("INVALID_LINEAGE", "setup requires project_scene and setup lineage")
    if not set(lineage).issubset(allowed):
        err("INVALID_LINEAGE", "lineage sections do not match kind")
    fields_by_section = {
        "world_asset": ("asset_id", "version_id", "label"),
        "project_scene": ("project_id", "revision_id", "material_key", "state_key", "label"),
        "setup": ("setup_key", "label"),
    }
    for section, value in lineage.items():
        required = fields_by_section.get(section)
        if required is None:
            continue
        if not isinstance(value, dict) or any(not isinstance(value.get(key), str) or not value[key].strip() for key in required):
            err("INVALID_LINEAGE", "Lineage section is missing text identity or display fields")
            continue
        for key in required:
            if key.endswith("_id"):
                try:
                    uuid.UUID(value[key])
                except ValueError:
                    err("INVALID_LINEAGE", "Lineage ID must be a UUID")
    cast = manifest.get("cast", [])
    if not isinstance(cast, list):
        err("INVALID_CAST", "cast must be an array")
        return
    seen = set()
    for member in cast:
        if not isinstance(member, dict):
            err("INVALID_CAST", "cast entry must be an object")
            continue
        prefix = member.get("node_prefix")
        if not isinstance(prefix, str) or not re.fullmatch(r"ACTOR_[A-Za-z0-9_]{1,60}", prefix):
            err("INVALID_CAST", "Invalid actor prefix")
            continue
        if prefix in seen:
            err("INVALID_CAST", "Duplicate actor prefix")
        seen.add(prefix)
        if not any(name.startswith(prefix) for name in node_names):
            err("CAST_NODE_MISSING", "Actor prefix has no node in scene.glb")
        if any(not isinstance(member.get(key), str) or not member[key].strip()
               for key in ("character_key", "label", "wardrobe_label")):
            err("INVALID_CAST", "Cast identity and display fields must be non-empty text")


def validate_set_package(pkg_dir: pathlib.Path) -> dict[str, Any]:
    """Validate a Set Package v1.0/v1.1 directory and return a structured report."""
    errors: list[dict[str, str]] = []
    warnings: list[str] = []

    def err(code: str, msg: str) -> None:
        errors.append({"code": code, "message": msg})

    pkg_dir = pathlib.Path(pkg_dir)
    if any(p.is_symlink() for p in (pkg_dir.absolute(), *pkg_dir.absolute().parents)):
        err("SYMLINK_FORBIDDEN", "Package root or ancestor is a symlink")
        return {"ok": False, "errors": errors, "warnings": warnings}
    if not pkg_dir.exists() or not pkg_dir.is_dir():
        err("PACKAGE_DIR_NOT_FOUND", f"Directory does not exist: {pkg_dir}")
        return {"ok": False, "errors": errors, "warnings": warnings}

    # Enumerate all files in package directory
    actual_files: dict[str, pathlib.Path] = {}
    total_bytes = 0
    for p in sorted(pkg_dir.rglob("*")):
        if p.is_symlink():
            err("SYMLINK_FORBIDDEN", f"Symlink not allowed in set package: {p.relative_to(pkg_dir)}")
            continue
        if p.is_file():
            rel = p.relative_to(pkg_dir).as_posix()
            suffix = p.suffix.lower()
            if suffix in FORBIDDEN_SUFFIXES:
                err("FORBIDDEN_FILE_TYPE", f"Forbidden source/script file in package: {rel}")
            elif suffix not in ALLOWED_SUFFIXES:
                err("FORBIDDEN_FILE_TYPE", f"Disallowed file extension '{suffix}' in package: {rel}")
            actual_files[rel] = p
            total_bytes += p.stat().st_size
            if rel == "scene.glb" and p.stat().st_size > MAX_GLB_BYTES:
                err("GLB_SIZE_EXCEEDED", "scene.glb exceeds the GLB size limit")
        elif not p.is_dir():
            err("NON_REGULAR_FILE", "Package contains a non-regular filesystem node")

    if len(actual_files) > MAX_PACKAGE_FILES:
        err("FILE_COUNT_EXCEEDED", f"Package has {len(actual_files)} files (limit {MAX_PACKAGE_FILES})")

    if total_bytes > MAX_PACKAGE_BYTES:
        err("PACKAGE_SIZE_EXCEEDED", f"Total package size {total_bytes} bytes exceeds {MAX_PACKAGE_BYTES}")

    if any(e["code"] in ("SYMLINK_FORBIDDEN", "NON_REGULAR_FILE", "FILE_COUNT_EXCEEDED", "PACKAGE_SIZE_EXCEEDED", "GLB_SIZE_EXCEEDED") for e in errors):
        return {"ok": False, "errors": errors, "warnings": warnings}

    set_json_path = pkg_dir / "set.json"
    if not set_json_path.is_file():
        err("MANIFEST_MISSING", "Required set.json manifest not found")
        return {"ok": False, "errors": errors, "warnings": warnings}

    try:
        if set_json_path.stat().st_size > 1024 * 1024:
            raise ValueError("manifest exceeds 1 MiB")
        manifest = _strict_json(set_json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        err("MANIFEST_INVALID_JSON", f"Failed to parse set.json: {exc}")
        return {"ok": False, "errors": errors, "warnings": warnings}

    objects = ("units", "room_bounds", "navigation", "render_defaults")
    lists = ("cameras", "lighting_presets", "files")
    if (not isinstance(manifest, dict)
            or any(not isinstance(manifest.get(k), dict) for k in objects)
            or any(not isinstance(manifest.get(k), list) or not all(isinstance(item, dict) for item in manifest[k]) for k in lists)
            or any(not isinstance(item.get("id"), str) for item in manifest["lighting_presets"])):
        err("INVALID_MANIFEST_SHAPE", "Manifest contains invalid object or list fields")
        return {"ok": False, "errors": errors, "warnings": warnings}

    if manifest.get("schema_version") not in ("1.0", "1.1"):
        err("UNSUPPORTED_SCHEMA_VERSION", "Expected schema_version 1.0 or 1.1")

    pkg_id = manifest.get("package_id", "")
    if not isinstance(pkg_id, str) or not PACKAGE_ID_RE.fullmatch(pkg_id):
        err("INVALID_PACKAGE_ID", f"Invalid package_id: {pkg_id!r}")
    elif pkg_id != pkg_dir.name:
        err("PACKAGE_ID_DIRECTORY_MISMATCH", "package_id must match the package directory name")

    if not isinstance(manifest.get("title"), str) or not manifest["title"].strip():
        err("INVALID_TITLE", "title must be a non-empty string")

    # Units & room_bounds
    units = manifest.get("units") or {}
    if units.get("system") != "METRIC" or (not _finite_number(units.get("scale_length")) or units["scale_length"] != 1.0):
        err("INVALID_UNITS", "units.system must be 'METRIC' and scale_length must be 1.0")

    room_bounds = manifest.get("room_bounds") or {}
    size_m = room_bounds.get("size_m")
    if not _is_vec3(size_m) or any(x <= 0 for x in size_m):
        err("INVALID_ROOM_BOUNDS", "room_bounds.size_m must be 3 positive numbers")

    # Navigation & walkable boxes
    nav = manifest.get("navigation") or {}
    if not _is_vec3(nav.get("orbit_target_blender_m")):
        err("INVALID_NAVIGATION", "navigation.orbit_target_blender_m must be a [x, y, z] array")
    wboxes = nav.get("walkable_boxes_blender_m")
    if not isinstance(wboxes, list) or len(wboxes) < 1:
        err("INVALID_WALKABLE_BOXES", "navigation.walkable_boxes_blender_m must contain at least 1 AABB")
    else:
        for idx, wb in enumerate(wboxes):
            if not isinstance(wb, dict):
                err("INVALID_WALKABLE_BOXES", "Walkable box must be an object")
                continue
            bmin, bmax = wb.get("min"), wb.get("max")
            if not _is_vec3(bmin) or not _is_vec3(bmax) or any(bmin[i] > bmax[i] for i in range(3)):
                err("INVALID_WALKABLE_BOXES", f"walkable_boxes_blender_m[{idx}] has invalid min/max")

    # Render defaults
    rdef = manifest.get("render_defaults") or {}
    res = rdef.get("resolution")
    if not isinstance(res, list) or len(res) != 2 or not all(type(x) is int and _finite_number(x) and x > 0 for x in res):
        err("INVALID_RENDER_DEFAULTS", "render_defaults.resolution must be [width, height] positive integers")
        res = [1344, 768]

    # Cameras
    cameras = manifest.get("cameras")
    seen_cam_ids: set[str] = set()
    if not isinstance(cameras, list) or len(cameras) < 1:
        err("INVALID_CAMERAS", "cameras must be a non-empty array")
    else:
        for idx, cam in enumerate(cameras):
            cid = cam.get("id", "")
            if not isinstance(cid, str) or not CAM_ID_RE.fullmatch(cid):
                err("INVALID_CAMERA_ID", f"cameras[{idx}].id {cid!r} is invalid")
            elif cid in seen_cam_ids:
                err("DUPLICATE_CAMERA_ID", f"Duplicate camera id {cid!r}")
            if isinstance(cid, str):
                seen_cam_ids.add(cid)

            proj = cam.get("projection")
            if proj not in ("PERSP", "ORTHO"):
                err("INVALID_CAMERA_PROJECTION", f"Camera {cid}: projection must be PERSP or ORTHO")
            if not _is_vec3(cam.get("location_blender_m")) or not _is_vec3(cam.get("target_blender_m")):
                err("INVALID_CAMERA_TRANSFORM", f"Camera {cid}: location_blender_m and target_blender_m required")
            if proj == "PERSP":
                lens = cam.get("lens_mm", 0)
                if not _finite_number(lens) or lens <= 0:
                    err("INVALID_CAMERA_LENS", f"Camera {cid}: lens_mm must be positive")
                elif (not all(_finite_number(cam.get(k, default)) and cam.get(k, default) > 0
                              for k, default in (("sensor_width_mm", 36.0), ("sensor_height_mm", 24.0)))
                      or cam.get("sensor_fit", "AUTO") not in ("AUTO", "VERTICAL", "HORIZONTAL")
                      or ("fov_y_deg" in cam and (not _finite_number(cam["fov_y_deg"]) or not 0 < cam["fov_y_deg"] < 180))):
                    err("INVALID_CAMERA_OPTICS", "Camera optics must use positive finite numeric values and a valid sensor fit")
                else:
                    try:
                        fov_calc = compute_vertical_fov_deg(
                            lens_mm=float(lens),
                            sensor_width_mm=float(cam.get("sensor_width_mm", 36.0)),
                            sensor_height_mm=float(cam.get("sensor_height_mm", 24.0)),
                            sensor_fit=str(cam.get("sensor_fit", "AUTO")),
                            res_x=res[0],
                            res_y=res[1],
                        )
                    except ValueError:
                        err("INVALID_CAMERA_OPTICS", "Camera FOV cannot be represented")
                        continue
                    if "fov_y_deg" in cam and abs(float(cam["fov_y_deg"]) - fov_calc) > 0.05:
                        err(
                            "CAMERA_FOV_MISMATCH",
                            f"Camera {cid}: declared fov_y_deg {cam['fov_y_deg']} differs from formula {fov_calc:.4f}",
                        )
            elif proj == "ORTHO":
                oscale = cam.get("ortho_scale", 0)
                if not _finite_number(oscale) or oscale <= 0:
                    err("INVALID_ORTHO_SCALE", f"Camera {cid}: ortho_scale must be positive for ORTHO camera")

        def_cam = rdef.get("default_camera_id")
        if not isinstance(def_cam, str) or def_cam not in seen_cam_ids:
            err("INVALID_DEFAULT_CAMERA", f"default_camera_id {def_cam!r} not in cameras")

    # Lighting presets
    presets = manifest.get("lighting_presets")
    if not isinstance(presets, list) or len(presets) < 1:
        err("INVALID_LIGHTING_PRESETS", "lighting_presets must be a non-empty array")
    else:
        preset_ids = {p.get("id") for p in presets if isinstance(p, dict)}
        if not isinstance(rdef.get("default_lighting_preset"), str) or rdef["default_lighting_preset"] not in preset_ids:
            err("INVALID_DEFAULT_LIGHTING", "default_lighting_preset not found in lighting_presets")

    # Files manifest verification
    decl_files = manifest.get("files")
    declared_paths: set[str] = set()
    has_glb = False
    has_poster = False
    has_cam_ref = False
    glb_info: dict[str, Any] | None = None

    if not isinstance(decl_files, list) or len(decl_files) < 1:
        err("INVALID_FILES_LIST", "files must be a non-empty array")
    else:
        for fentry in decl_files:
            if not isinstance(fentry.get("mime"), str) or not fentry["mime"].strip():
                err("INVALID_FILE_MIME", "Every file requires a non-empty MIME type")
            if "role" in fentry and (not isinstance(fentry["role"], str) or not fentry["role"].strip()):
                err("INVALID_FILE_ROLE", "File role must be non-empty text when present")
            fpath = fentry.get("path", "")
            if not isinstance(fpath, str) or not fpath or fpath.startswith("/") or ".." in fpath:
                err("INVALID_FILE_PATH", f"Invalid file path in files[]: {fpath!r}")
                continue
            if fpath in declared_paths:
                err("DUPLICATE_FILE_PATH", "Duplicate file path in files[]")
                continue
            declared_paths.add(fpath)
            if fpath == "scene.glb":
                has_glb = True
            if fpath == "poster.png":
                has_poster = True
            if (re.fullmatch(r"cameras/CAM_[A-Za-z0-9_-]{1,32}\.png", fpath)
                    and fpath[len("cameras/CAM_"):-4] in seen_cam_ids):
                has_cam_ref = True

            disk_p = pkg_dir / fpath
            if fpath not in actual_files or disk_p.is_symlink() or not disk_p.is_file():
                err("FILE_MISSING", f"Declared file missing on disk: {fpath}")
                continue

            actual_size = disk_p.stat().st_size
            actual_sha = hashlib.sha256(disk_p.read_bytes()).hexdigest()
            decl_size = fentry.get("size")
            decl_sha = fentry.get("sha256", "")

            if type(decl_size) is not int or decl_size <= 0 or decl_size != actual_size:
                err("PACKAGE_SIZE_MISMATCH", f"File {fpath}: declared size {decl_size} != actual {actual_size}")
            if not isinstance(decl_sha, str) or not SHA256_RE.fullmatch(decl_sha) or decl_sha != actual_sha:
                err("PACKAGE_HASH_MISMATCH", f"File {fpath}: declared sha256 {decl_sha!r} != actual {actual_sha}")

            if fpath == "scene.glb":
                if actual_size > MAX_GLB_BYTES:
                    err("GLB_SIZE_EXCEEDED", f"scene.glb size {actual_size} exceeds {MAX_GLB_BYTES}")
                try:
                    glb_info = inspect_glb_header(disk_p)
                    if glb_info["node_count"] > MAX_GLB_NODES:
                        err(
                            "GLB_NODE_COUNT_EXCEEDED",
                            f"scene.glb has {glb_info['node_count']} nodes (limit {MAX_GLB_NODES})",
                        )
                except ValueError as exc:
                    err("INVALID_GLB_HEADER", str(exc))

    for cam in cameras:
        if "reference_image" in cam:
            ref = cam["reference_image"]
            if (not isinstance(ref, str) or ref not in declared_paths
                    or ref != f"cameras/CAM_{cam.get('id')}.png"):
                err("INVALID_CAMERA_REFERENCE", "Camera reference must name its declared cameras/CAM_<id>.png file")

    _validate_layers(manifest, glb_info["node_names"] if glb_info else [], err)

    if not has_glb:
        err("MISSING_SCENE_GLB", "Required scene.glb is missing from files[]")
    if not has_poster:
        err("MISSING_POSTER_PNG", "Required poster.png is missing from files[]")
    if not has_cam_ref:
        err("MISSING_CAMERA_REF", "At least one cameras/CAM_<id>.png reference still is required")

    # Check for undeclared files on disk (excluding set.json)
    for rel_path in actual_files:
        if rel_path == "set.json":
            continue
        if rel_path not in declared_paths:
            err("UNDECLARED_FILE", f"File on disk is not listed in set.json files[]: {rel_path}")

    return {
        "ok": len(errors) == 0,
        "package_id": pkg_id,
        "file_count": len(actual_files),
        "total_bytes": total_bytes,
        "camera_count": len(seen_cam_ids),
        "glb": glb_info,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Set Package v1.0/v1.1 directory.")
    parser.add_argument("package_dir", type=pathlib.Path, help="Path to the set package directory containing set.json")
    args = parser.parse_args(argv)
    report = validate_set_package(args.package_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
