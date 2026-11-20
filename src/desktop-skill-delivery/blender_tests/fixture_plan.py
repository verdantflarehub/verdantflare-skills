"""Non-production identities and small geometry for isolated exporter tests."""


def make_plan(source_hash):
    return {
        "schema": "vf.set-export/v1",
        "source": {"asset_id": "project/prepared.blend", "sha256": source_hash,
                   "project_id": "11111111-1111-4111-8111-111111111111",
                   "revision_id": "22222222-2222-4222-8222-222222222222",
                   "project_path": "blender/prepared.blend"},
        "package_id": "synthetic-room", "title": "Synthetic geometry fixture",
        "objects": ["SET_floor", "SET_block"],
        "cameras": [{"id": "C1", "object_name": "Camera", "label": "Perspective"},
                    {"id": "N1", "object_name": "TopCamera", "label": "Top", "category": "overview"}],
        "navigation": {"orbit_target_blender_m": [0, 0, .5],
                       "walkable_boxes_blender_m": [{"min": [-2, -1, 0], "max": [2, 1, 2]}]},
        "lighting_presets": [{"id": "neutral", "label": "Neutral", "exposure_ev": 0}],
        "default_lighting_preset": "neutral", "default_camera_id": "C1",
        "poster_resolution": [320, 180], "kind": "world_empty",
    }
