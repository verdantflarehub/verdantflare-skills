import copy
from pathlib import Path
import sys
import unittest
import json
import subprocess
import tempfile

TOOLS = Path(__file__).resolve().parents[1] / "blender_tools"
sys.path.insert(0, str(TOOLS))
import prepare_set_export as prepare
import export_set_package as exporter
import validate_set_package as validator
from fixture_plan import make_plan


class ExportPlanTests(unittest.TestCase):
    def test_script_is_repeatable_and_compiles_without_importing_bpy(self):
        plan = make_plan("1" * 64)
        before = copy.deepcopy(plan)
        first = prepare.build_script(plan)
        self.assertEqual(first, prepare.build_script(plan))
        compile(first, "<test-export>", "exec")
        self.assertNotIn("bpy", sys.modules)
        self.assertEqual(plan, before)

    def test_unknown_fields_and_unbound_sources_reject(self):
        for mutation in (lambda p: p.update(arbitrary_script="bad"),
                         lambda p: p["source"].update(asset_id="../../outside.blend"),
                         lambda p: p["source"].update(asset_id="/outside.blend"),
                         lambda p: p["source"].update(sha256="0" * 64),
                         lambda p: p["source"].update(asset_id="project/../outside.blend")):
            plan = make_plan("1" * 64)
            mutation(plan)
            with self.assertRaises(ValueError):
                prepare.build_script(plan)

    def test_ambiguous_objects_camera_ids_and_hidden_objects_reject(self):
        for mutation in (lambda p: p["objects"].append("SET_floor"),
                         lambda p: p["cameras"].append(copy.deepcopy(p["cameras"][0])),
                         lambda p: p["cameras"][0].update(hide_objects=["not-selected"]),
                         lambda p: p.update(default_camera_id=[])):
            plan = make_plan("1" * 64)
            mutation(plan)
            with self.assertRaises(ValueError):
                prepare.build_script(plan)

    def test_empty_set_actor_selection_and_nonfinite_metadata_reject(self):
        plan = make_plan("1" * 64)
        plan["objects"].append("ACTOR_person")
        with self.assertRaises(ValueError):
            exporter.validate_plan(plan, validator)
        plan = make_plan("1" * 64)
        plan["navigation"]["orbit_target_blender_m"][0] = float("nan")
        with self.assertRaises(ValueError):
            prepare.build_script(plan)

    def test_poster_has_true_sixteen_by_nine_pixels(self):
        plan = make_plan("1" * 64)
        plan["poster_resolution"] = [320, 200]
        with self.assertRaises(ValueError):
            prepare.build_script(plan)

    def test_cli_rejects_linked_plan_before_creating_output(self):
        root = Path(__file__).resolve().parents[4] / '.tmp' / 'blender-plan-tests'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            base = Path(temporary)
            source = base / 'source.json'
            source.write_text(json.dumps(make_plan('1' * 64)))
            linked = base / 'linked.json'
            linked.symlink_to(source)
            output = base / 'prepared.py'
            result = subprocess.run([sys.executable, str(TOOLS / 'prepare_set_export.py'), str(linked),
                                     '--output', str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('INVALID_PLAN_FILE', result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
