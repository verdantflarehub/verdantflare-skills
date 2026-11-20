"""Prepare one immutable Project script; does not run Blender or call MCP."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import export_set_package as exporter
import validate_set_package as validator


def build_script(plan, *, workspace_root="/workspace"):
    exporter.validate_plan(plan, validator)
    root = Path(__file__).parent
    validator_source = (root / "validate_set_package.py").read_bytes()
    provenance = json.loads((root / "source-provenance.json").read_text())
    exporter.need(hashlib.sha256(validator_source).hexdigest() == provenance["validator"]["sha256"], "VALIDATOR_SNAPSHOT_CHANGED")
    export_source = (root / "export_set_package.py").read_bytes()
    plan_json = json.dumps(plan, sort_keys=True, ensure_ascii=False, allow_nan=False)
    script = (
        "# Generated from the release-locked VF set-export tools.\n"
        "import json, types\n"
        "_v = types.ModuleType('vf_set_validator')\n"
        f"exec(compile({validator_source!r}, '<vf_set_validator>', 'exec'), _v.__dict__)\n"
        "_e = types.ModuleType('vf_set_export')\n"
        f"exec(compile({export_source!r}, '<vf_set_export>', 'exec'), _e.__dict__)\n"
        f"_result = _e.run(json.loads({plan_json!r}), _v, workspace_root={workspace_root!r})\n"
        "print(json.dumps(_result, sort_keys=True))\n"
    ).encode("utf-8")
    exporter.need(len(script) <= 4 * 1024 * 1024, "SCRIPT_EXCEEDS_WORKER_LIMIT")
    compile(script, "<prepared_set_export>", "exec")
    return script


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    exporter.need(not any(p.is_symlink() for p in (args.plan.absolute(), *args.plan.absolute().parents))
                  and args.plan.is_file() and args.plan.stat().st_size <= 1024 * 1024, "INVALID_PLAN_FILE")
    plan = validator._strict_json(args.plan.read_text(encoding="utf-8"))
    script = build_script(plan)
    exporter.need(not any(p.is_symlink() for p in (args.output.absolute(), *args.output.absolute().parents)), "LINKED_OUTPUT_PATH")
    with args.output.open("xb") as stream:
        stream.write(script)
    print(json.dumps({"status": "script_prepared", "size": len(script),
                      "sha256": hashlib.sha256(script).hexdigest(), "executed": False}))


if __name__ == "__main__":
    main()
