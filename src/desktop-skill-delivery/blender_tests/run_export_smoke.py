"""Run isolated synthetic export checks with an explicitly supplied Blender binary."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import struct
import time

TOOLS = Path(__file__).resolve().parents[1] / "blender_tools"
sys.path.insert(0, str(TOOLS))
import prepare_set_export as prepare
import validate_set_package as validator
from fixture_plan import make_plan


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    base = args.output.resolve()
    workspace = base / "workspace"
    workspace.mkdir()
    (workspace / "exports").mkdir()
    resources = base / "user-resources"
    resources.mkdir()
    env = dict(os.environ)
    env.update(BLENDER_USER_RESOURCES=str(resources),
               BLENDER_USER_CONFIG=str(resources / "config"),
               BLENDER_USER_SCRIPTS=str(resources / "scripts"),
               BLENDER_USER_DATAFILES=str(resources / "datafiles"),
               BLENDER_USER_EXTENSIONS=str(resources / "extensions"))
    rows = []

    def execute(label, script, cwd, extra=()):
        focus = shutil.which("lsappinfo")
        before = subprocess.check_output([focus, "front"], text=True).strip() if focus else None
        started = time.monotonic()
        result = subprocess.run([str(args.blender.resolve()), "--background", "--factory-startup", "--disable-autoexec",
                                 "--threads", "2", "--python-exit-code", "1", "--python", str(script), *extra],
                                cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
        after = subprocess.check_output([focus, "front"], text=True).strip() if focus else None
        raw = result.stdout + result.stderr
        # Logs retain relative task locations without disclosing user directories.
        repository = Path(__file__).resolve().parents[4]
        (base / (label + ".log")).write_text(raw.replace(str(repository), "%WORKSPACE%"))
        rows.append({"case": label, "exit_code": result.returncode, "duration_seconds": round(time.monotonic() - started, 3),
                     "frontmost_observed": focus is not None, "frontmost_unchanged": before == after if focus else None})
        assert not focus or before == after, "FOCUS_CHANGE_OBSERVED"
        return result, raw

    fixture = Path(__file__).with_name("fixture_scene.py").resolve()
    result, raw = execute("fixture", fixture, workspace, ("--", str(workspace)))
    assert result.returncode == 0, "FIXTURE_FAILED: see fixture.log"
    originals = {p.name: digest(p) for p in (workspace / "project").glob("*.blend")}
    plan = make_plan(originals["prepared.blend"])
    output = workspace / "exports" / "valid"
    output.mkdir()
    script = base / "prepared-export.py"
    script.write_bytes(prepare.build_script(plan, workspace_root=str(workspace)))
    assert script.read_bytes() == prepare.build_script(plan, workspace_root=str(workspace))
    result, raw = execute("valid", script, output)
    assert result.returncode == 0, "EXPORT_FAILED: see valid.log"
    package = output / plan["package_id"]
    report = validator.validate_set_package(package)
    assert report["ok"], report["errors"]
    assert set(report["glb"]["node_names"]) == set(plan["objects"])
    manifest = json.loads((package / "set.json").read_text())
    assert manifest["render_defaults"]["resolution"] == [160, 90]
    assert [cam["projection"] for cam in manifest["cameras"]] == ["PERSP", "ORTHO"]
    bounds = manifest["room_bounds"]["bbox_blender_m"]
    assert all(abs(a - b) < 1e-5 for a, b in zip(bounds["min"], [-3, -2, -.2]))
    assert all(abs(a - b) < 1e-5 for a, b in zip(bounds["max"], [3, 2, 1.3]))
    before_files = {p.relative_to(package).as_posix(): digest(p) for p in package.rglob("*") if p.is_file()}
    result, raw = execute("existing-output", script, output)
    assert result.returncode != 0 and "OUTPUT_ALREADY_EXISTS" in raw
    assert before_files == {p.relative_to(package).as_posix(): digest(p) for p in package.rglob("*") if p.is_file()}
    for label, source_name, expected in (("procedural", "procedural.blend", "MATERIAL_REQUIRES_PREPARATION"),
                                         ("object-noise", "object-noise.blend", "MATERIAL_REQUIRES_PREPARATION"),
                                         ("object-coordinates", "object-coordinates.blend", "MATERIAL_REQUIRES_PREPARATION"),
                                         ("vertical-ortho", "vertical-ortho.blend", "CAMERA_PROJECTION_REQUIRES_PREPARATION"),
                                         ("displacement", "displacement.blend", "MATERIAL_REQUIRES_PREPARATION"),
                                         ("missing-texture", "missing-texture.blend", "MATERIAL_REQUIRES_PREPARATION"),
                                         ("geometry-nodes", "geometry-nodes.blend", "GEOMETRY_REQUIRES_PREPARATION"),
                                         ("shifted", "shifted.blend", "CAMERA_PROJECTION_REQUIRES_PREPARATION"),
                                         ("changed-source", "prepared.blend", "SOURCE_HASH_MISMATCH")):
        changed = copy.deepcopy(plan)
        changed["source"].update(asset_id="project/" + source_name, project_path="blender/" + source_name,
                                 sha256=originals[source_name] if label != "changed-source" else "f" * 64)
        destination = workspace / "exports" / label
        destination.mkdir()
        candidate = base / (label + ".py")
        candidate.write_bytes(prepare.build_script(changed, workspace_root=str(workspace)))
        result, raw = execute(label, candidate, destination)
        assert result.returncode != 0 and expected in raw, label + ": expected rejection absent"
        assert not list(destination.iterdir()), label + ": failure wrote partial output before preflight"
    # Exercise post-render validation failure: only our temporary stage is removed.
    invalid = copy.deepcopy(plan)
    invalid['lighting_presets'][0]['id'] = 'missing'
    invalid['default_lighting_preset'] = 'missing'
    invalid['navigation']['walkable_boxes_blender_m'] = [{'min': [0,0,0], 'max': [1,1,1]}]
    # A late validator rejection injected into the generated script exercises cleanup
    # without falsely claiming an actual scene defect.
    late = base / 'late-validation.py'
    script_bytes = prepare.build_script(invalid, workspace_root=str(workspace))
    marker = b'_result = _e.run('
    assert script_bytes.count(marker) == 1
    script_bytes = script_bytes.replace(marker, b'_v.validate_set_package = lambda path: {"ok": False, "errors": [{"code": "INJECTED_TEST_FAILURE"}]}\n' + marker)
    late.write_bytes(script_bytes)
    destination = workspace / 'exports' / 'late-validation'
    destination.mkdir()
    result, raw = execute('late-validation', late, destination)
    assert result.returncode != 0 and 'INJECTED_TEST_FAILURE' in raw
    assert not list(destination.iterdir()), 'STAGING_NOT_CLEANED'
    # Prepared object-level material must survive into the actual GLB.
    override = copy.deepcopy(plan)
    override['source'].update(asset_id='project/object-material.blend', project_path='blender/object-material.blend', sha256=originals['object-material.blend'])
    override_script = base / 'object-material.py'
    override_script.write_bytes(prepare.build_script(override, workspace_root=str(workspace)))
    destination = workspace / 'exports' / 'object-material'
    destination.mkdir()
    result, raw = execute('object-material', override_script, destination)
    assert result.returncode == 0, 'OBJECT_MATERIAL_EXPORT_FAILED'
    glb = (destination / plan['package_id'] / 'scene.glb').read_bytes()
    size = struct.unpack_from('<I',glb,12)[0]
    gltf = json.loads(glb[20:20+size])
    node = next(n for n in gltf['nodes'] if n['name']=='SET_block')
    for primitive in gltf['meshes'][node['mesh']]['primitives']:
        material = gltf['materials'][primitive['material']]
        assert material['name'] == 'Object_override'
        assert all(abs(a-b)<1e-5 for a,b in zip(material['pbrMetallicRoughness']['baseColorFactor'], [.7,.2,.1,1]))
    assert originals == {p.name: digest(p) for p in (workspace / "project").glob("*.blend")}
    receipt = {"cases": rows, "passed": True, "source_unchanged": True,
               "synthetic_project_identities": True, "package_report": report,
               "package_files": before_files, "tool_files": {p.name: digest(p) for p in TOOLS.iterdir() if p.is_file()},
               "blender_sha256": digest(args.blender), "actual_instance_or_web_acceptance": False,
               "visual_alignment_verified": False, "windows_execution": sys.platform == "win32"}
    (base / "smoke-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"cases": len(rows), "passed": True, "source_unchanged": True,
                      "node_names": report["glb"]["node_names"], "visual_alignment_verified": False}))


if __name__ == "__main__":
    main()
