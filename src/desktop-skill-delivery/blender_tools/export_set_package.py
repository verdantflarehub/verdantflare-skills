"""Bound a prepared, static Blender scene to a new Set Package.

Runs in a fresh Blender process through an embedded, fixed Project script.
It does not fetch inputs, save the source blend, bake unknown shaders, or
claim visual alignment. The caller supplies the reviewed validator module.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct
import tempfile
import uuid


def need(condition, code):
    if not condition:
        raise ValueError(code)


def text(value):
    return isinstance(value, str) and bool(value.strip()) and all(ord(c) >= 32 for c in value)


def relative_path(value):
    need(text(value) and "\\" not in value, "INVALID_RELATIVE_PATH")
    parts = value.split("/")
    need(all(p not in ("", ".", "..") and ":" not in p for p in parts), "INVALID_RELATIVE_PATH")
    return PurePosixPath(value)


def validate_plan(plan, validator):
    need(isinstance(plan, dict), "INVALID_EXPORT_PLAN")
    allowed = {"schema", "source", "package_id", "title", "objects", "cameras",
               "navigation", "lighting_presets", "default_lighting_preset",
               "default_camera_id", "poster_resolution", "object_groups", "kind", "lineage", "cast"}
    need(not set(plan) - allowed and plan.get("schema") == "vf.set-export/v1", "INVALID_EXPORT_PLAN")
    need(isinstance(plan.get("package_id"), str) and validator.PACKAGE_ID_RE.fullmatch(plan["package_id"]), "INVALID_PACKAGE_ID")
    need(text(plan.get("title")), "INVALID_TITLE")
    source = plan.get("source")
    need(isinstance(source, dict) and set(source) == {"asset_id", "sha256", "project_id", "revision_id", "project_path"}, "INVALID_SOURCE_BINDING")
    asset = relative_path(source["asset_id"])
    need(asset.parts[0] in ("project", "exports") and len(asset.parts) > 1 and asset.suffix == ".blend", "INVALID_SOURCE_BINDING")
    need(relative_path(source["project_path"]).suffix == ".blend", "INVALID_SOURCE_BINDING")
    need(isinstance(source["sha256"], str) and validator.SHA256_RE.fullmatch(source["sha256"])
         and source["sha256"] != "0" * 64, "INVALID_SOURCE_BINDING")
    for key in ("project_id", "revision_id"):
        need(isinstance(source[key], str), "INVALID_SOURCE_BINDING")
        try:
            uuid.UUID(source[key])
        except ValueError as exc:
            raise ValueError("INVALID_SOURCE_BINDING") from exc
    objects = plan.get("objects")
    need(isinstance(objects, list) and 0 < len(objects) <= validator.MAX_GLB_NODES
         and all(text(name) for name in objects) and len(set(objects)) == len(objects), "INVALID_OBJECT_SELECTION")
    cameras = plan.get("cameras")
    need(isinstance(cameras, list) and 0 < len(cameras) <= validator.MAX_PACKAGE_FILES - 3, "INVALID_CAMERAS")
    seen = set()
    for cam in cameras:
        need(isinstance(cam, dict) and set(cam) <= {"id", "object_name", "label", "category", "hide_objects"}, "INVALID_CAMERA")
        cid = cam.get("id")
        need(isinstance(cid, str) and validator.CAM_ID_RE.fullmatch(cid) and cid not in seen, "INVALID_CAMERA_ID")
        seen.add(cid)
        need(text(cam.get("object_name")) and text(cam.get("label")) and text(cam.get("category", "shot")), "INVALID_CAMERA")
        hidden = cam.get("hide_objects", [])
        need(isinstance(hidden, list) and all(text(name) for name in hidden)
             and len(set(hidden)) == len(hidden) and set(hidden) <= set(objects), "INVALID_CAMERA_HIDE_LIST")
    need(text(plan.get("default_camera_id")) and plan["default_camera_id"] in seen, "INVALID_DEFAULT_CAMERA")
    nav = plan.get("navigation")
    need(isinstance(nav, dict) and validator._is_vec3(nav.get("orbit_target_blender_m")), "INVALID_NAVIGATION")
    boxes = nav.get("walkable_boxes_blender_m")
    need(isinstance(boxes, list) and bool(boxes), "INVALID_NAVIGATION")
    for box in boxes:
        need(isinstance(box, dict) and validator._is_vec3(box.get("min")) and validator._is_vec3(box.get("max"))
             and all(a <= b for a, b in zip(box["min"], box["max"])), "INVALID_NAVIGATION")
    presets = plan.get("lighting_presets")
    need(isinstance(presets, list) and bool(presets)
         and all(isinstance(item, dict) and text(item.get("id")) for item in presets), "INVALID_LIGHTING")
    need(plan.get("default_lighting_preset") in [item["id"] for item in presets], "INVALID_LIGHTING")
    resolution = plan.get("poster_resolution", [640, 360])
    need(isinstance(resolution, list) and len(resolution) == 2
         and all(type(n) is int and 0 < n <= 8192 for n in resolution)
         and resolution[0] * 9 == resolution[1] * 16, "INVALID_POSTER_RESOLUTION")
    # Reject non-finite values even in optional metadata before embedding code.
    json.dumps(plan, allow_nan=False)
    layer_errors = []
    layers = {k: plan[k] for k in ("kind", "lineage", "cast", "object_groups") if k in plan}
    layers["schema_version"] = "1.1" if any(k in plan for k in ("kind", "lineage", "cast")) else "1.0"
    validator._validate_layers(layers, objects, lambda code, message: layer_errors.append(code))
    need(not layer_errors, "INVALID_LAYER_DECLARATION:" + ",".join(layer_errors))
    return plan


def bound_path(root, relative):
    path = root.joinpath(*relative_path(relative).parts)
    need(not any(p.is_symlink() for p in (path, *path.parents)), "LINKED_PATH")
    need(path.resolve().is_relative_to(root.resolve()), "PATH_OUTSIDE_WORKSPACE")
    return path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_prepared_materials(objects):
    # Inspect the connected graph only; unused editor nodes do not change export.
    supported = {"OUTPUT_MATERIAL", "BSDF_PRINCIPLED", "TEX_IMAGE", "NORMAL_MAP",
                 "UV_MAP", "TEX_COORD", "MAPPING", "RGB", "VALUE", "REROUTE"}
    for obj in objects:
        for slot in obj.material_slots:
            material = slot.material
            need(material is not None, "MATERIAL_REQUIRES_PREPARATION")
            if not material.use_nodes:
                continue
            outputs = [node for node in material.node_tree.nodes
                       if node.type == "OUTPUT_MATERIAL" and node.is_active_output]
            need(len(outputs) == 1, "MATERIAL_REQUIRES_PREPARATION")
            need(not outputs[0].inputs["Displacement"].is_linked and not outputs[0].inputs["Volume"].is_linked,
                 "MATERIAL_REQUIRES_PREPARATION")
            surface = outputs[0].inputs["Surface"]
            need(surface.is_linked and all(link.from_node.type == "BSDF_PRINCIPLED" for link in surface.links),
                 "MATERIAL_REQUIRES_PREPARATION")
            pending, seen = outputs[:], set()
            while pending:
                node = pending.pop()
                if node.as_pointer() in seen:
                    continue
                seen.add(node.as_pointer())
                need(node.type in supported, "MATERIAL_REQUIRES_PREPARATION")
                if node.type == "TEX_IMAGE":
                    need(node.image is not None and node.image.source in ("FILE", "GENERATED"), "MATERIAL_REQUIRES_PREPARATION")
                for socket in node.inputs:
                    for link in socket.links:
                        if link.from_node.type in ("TEX_COORD", "UV_MAP"):
                            need(link.from_socket.name == "UV" and node.type in ("MAPPING", "TEX_IMAGE")
                                 and socket.name == "Vector", "MATERIAL_REQUIRES_PREPARATION")
                        if link.from_node.type == "MAPPING":
                            need(node.type == "TEX_IMAGE" and socket.name == "Vector", "MATERIAL_REQUIRES_PREPARATION")
                    pending.extend(link.from_node for link in socket.links)


def camera_metadata(obj, item, resolution, validator):
    from mathutils import Vector
    data = obj.data
    need(data.type in ("PERSP", "ORTHO") and abs(data.shift_x) < 1e-9
         and abs(data.shift_y) < 1e-9, "CAMERA_PROJECTION_REQUIRES_PREPARATION")
    quat = obj.matrix_world.to_quaternion()
    forward = (quat @ Vector((0, 0, -1))).normalized()
    actual_up = (quat @ Vector((0, 1, 0))).normalized()
    world_up = Vector((0, 1, 0)) if abs(forward.z) > .999 else Vector((0, 0, 1))
    right = forward.cross(world_up).normalized()
    expected_up = right.cross(forward).normalized()
    need(actual_up.dot(expected_up) > .999999, "CAMERA_ROLL_REQUIRES_PREPARATION")
    location = obj.matrix_world.translation
    result = {"id": item["id"], "object_name": item["object_name"], "label": item["label"],
              "category": item.get("category", "shot"), "projection": data.type,
              "location_blender_m": list(location), "target_blender_m": list(location + forward * 10),
              "hide_objects": item.get("hide_objects", []),
              "reference_image": f"cameras/CAM_{item['id']}.png"}
    if data.type == "PERSP":
        result.update(lens_mm=data.lens, sensor_width_mm=data.sensor_width,
                      sensor_height_mm=data.sensor_height, sensor_fit=data.sensor_fit,
                      fov_y_deg=validator.compute_vertical_fov_deg(data.lens, data.sensor_width,
                          data.sensor_height, data.sensor_fit, *resolution))
    else:
        need(data.sensor_fit != "VERTICAL" and (data.sensor_fit != "AUTO" or resolution[0] >= resolution[1]),
             "CAMERA_PROJECTION_REQUIRES_PREPARATION")
        result["ortho_scale"] = data.ortho_scale
    return result


def restore_glb_names(path, name_map, validator):
    """Preserve original node identities without renaming live source objects."""
    validator.inspect_glb_header(path)
    raw = path.read_bytes()
    length, kind = struct.unpack_from("<I4s", raw, 12)
    need(kind == b"JSON", "INVALID_GLB")
    doc = validator._strict_json(raw[20:20 + length].decode("utf-8"))
    nodes = doc.get("nodes", [])
    need(len(nodes) == len(name_map) and {n.get("name") for n in nodes} == set(name_map), "UNEXPECTED_EXPORTED_NODES")
    for node in nodes:
        node["name"] = name_map[node["name"]]
    data = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    data += b" " * (-len(data) % 4)
    tail = raw[20 + length:]
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 20 + len(data) + len(tail))
                     + struct.pack("<I4s", len(data), b"JSON") + data + tail)


def run(plan, validator, workspace_root="/workspace"):
    """Execute inside a fresh headless Blender process, never an interactive scene."""
    validate_plan(plan, validator)
    import bpy
    from mathutils import Vector
    need(bpy.app.background, "BACKGROUND_PROCESS_REQUIRED")
    root = Path(workspace_root)
    need(root.is_absolute() and root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)), "INVALID_WORKSPACE")
    cwd = Path.cwd()
    need(cwd.parent == root / "exports" and not cwd.is_symlink(), "INVALID_OUTPUT_WORKSPACE")
    source = bound_path(root, plan["source"]["asset_id"])
    need(source.is_file() and sha256(source) == plan["source"]["sha256"], "SOURCE_HASH_MISMATCH")
    target = bound_path(root, f"exports/{cwd.name}/{plan['package_id']}")
    need(not target.exists(), "OUTPUT_ALREADY_EXISTS")
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    scene.frame_set(1)
    bpy.context.view_layer.update()
    need(scene.unit_settings.system == "METRIC" and scene.unit_settings.scale_length == 1.0, "METRIC_SOURCE_REQUIRED")
    need(not bpy.data.libraries and all(image.packed_file or image.source in ("GENERATED", "VIEWER")
         for image in bpy.data.images), "EXTERNAL_SOURCE_DEPENDENCIES")
    need(not scene.use_nodes and scene.render.pixel_aspect_x == scene.render.pixel_aspect_y, "RENDER_SETTINGS_REQUIRE_PREPARATION")
    objects = [scene.objects.get(name) for name in plan["objects"]]
    need(all(obj is not None and obj.type == "MESH" and not obj.hide_render for obj in objects), "SOURCE_OBJECT_MISSING_OR_HIDDEN")
    need(all(obj.instance_type == "NONE" and not obj.particle_systems
             and not any(mod.type == "NODES" for mod in obj.modifiers) for obj in objects), "GEOMETRY_REQUIRES_PREPARATION")
    cameras = [scene.objects.get(item["object_name"]) for item in plan["cameras"]]
    need(all(obj is not None and obj.type == "CAMERA" for obj in cameras), "SOURCE_CAMERA_MISSING")
    require_prepared_materials(objects)
    resolution = [max(1, int(value * scene.render.resolution_percentage / 100))
                  for value in (scene.render.resolution_x, scene.render.resolution_y)]
    metadata = [camera_metadata(obj, item, resolution, validator) for obj, item in zip(cameras, plan["cameras"])]
    with tempfile.TemporaryDirectory(prefix=".vf-set-", dir=cwd) as temporary:
        stage = Path(temporary) / plan["package_id"]
        stage.mkdir()
        (stage / "cameras").mkdir()
        scene.render.resolution_x, scene.render.resolution_y = resolution
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.render.use_file_extension = True
        selected = set(objects)

        def render(camera, item, path):
            hidden = set(item.get("hide_objects", []))
            for obj in scene.objects:
                if obj.type not in ("LIGHT", "CAMERA", "EMPTY"):
                    obj.hide_render = obj not in selected or obj.name in hidden
            scene.camera = camera
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)

        for camera, item, info in zip(cameras, plan["cameras"], metadata):
            render(camera, item, stage / info["reference_image"])
        index = [item["id"] for item in plan["cameras"]].index(plan["default_camera_id"])
        scene.render.resolution_x, scene.render.resolution_y = plan.get("poster_resolution", [640, 360])
        render(cameras[index], plan["cameras"][index], stage / "poster.png")
        scene.render.resolution_x, scene.render.resolution_y = resolution
        for obj in objects:
            obj.hide_render = False
        bpy.context.view_layer.update()
        graph = bpy.context.evaluated_depsgraph_get()
        collection = bpy.data.collections.new("VF_EXPORT_COPIES")
        scene.collection.children.link(collection)
        copies, names = [], {}
        for index, obj in enumerate(objects):
            mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(graph), preserve_all_data_layers=True, depsgraph=graph)
            # Preserve effective OBJECT-linked overrides as well as DATA materials.
            indices = [polygon.material_index for polygon in mesh.polygons]
            mesh.materials.clear()
            for slot in obj.material_slots:
                mesh.materials.append(slot.material)
            for polygon, material_index in zip(mesh.polygons, indices):
                polygon.material_index = material_index
            mesh.transform(obj.matrix_world)
            mesh.update()
            copy = bpy.data.objects.new(f"VF_EXPORT_{index:03}", mesh)
            collection.objects.link(copy)
            copies.append(copy)
            names[copy.name] = obj.name
        bpy.context.view_layer.update()
        bpy.ops.object.select_all(action="DESELECT")
        for obj in copies:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = copies[0]
        bpy.ops.export_scene.gltf(filepath=str(stage / "scene.glb"), export_format="GLB",
                                use_selection=True, export_yup=True, export_apply=True,
                                export_materials="EXPORT", export_texcoords=True,
                                export_normals=True, export_cameras=False, export_lights=False,
                                export_animations=False)
        restore_glb_names(stage / "scene.glb", names, validator)
        corners = [obj.matrix_world @ Vector(corner) for obj in copies for corner in obj.bound_box]
        lower = [min(p[i] for p in corners) for i in range(3)]
        upper = [max(p[i] for p in corners) for i in range(3)]
        files = []
        outputs = [("scene.glb", "model", "model/gltf-binary"), ("poster.png", "poster", "image/png")]
        outputs += [(item["reference_image"], "camera_ref", "image/png") for item in metadata]
        for path, role, mime in outputs:
            file = stage / path
            files.append({"path": path, "role": role, "mime": mime, "size": file.stat().st_size, "sha256": sha256(file)})
        manifest = {"schema_version": "1.1" if any(k in plan for k in ("kind", "lineage", "cast")) else "1.0",
                    "package_id": plan["package_id"], "title": plan["title"],
                    "source": {"project_id": plan["source"]["project_id"], "revision_id": plan["source"]["revision_id"],
                               "blend_path": plan["source"]["project_path"], "sha256": plan["source"]["sha256"],
                               "blender_version": bpy.app.version_string, "render_engine": scene.render.engine},
                    "units": {"system": "METRIC", "scale_length": 1.0,
                              "blender_axes": {"right": "+X", "forward": "+Y", "up": "+Z"},
                              "gltf_axes": {"right": "+X", "up": "+Y", "forward": "-Z"}},
                    "room_bounds": {"size_m": [upper[i] - lower[i] for i in range(3)], "bbox_blender_m": {"min": lower, "max": upper}},
                    "navigation": plan["navigation"], "lighting_presets": plan["lighting_presets"],
                    "render_defaults": {"resolution": resolution, "aspect_ratio": resolution[0] / resolution[1],
                                        "fps": scene.render.fps / scene.render.fps_base, "frame_range": [1, 1],
                                        "default_camera_id": plan["default_camera_id"],
                                        "default_lighting_preset": plan["default_lighting_preset"]},
                    "cameras": metadata, "files": files}
        for key in ("kind", "lineage", "cast", "object_groups"):
            if key in plan:
                manifest[key] = plan[key]
        with (stage / "set.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
        report = validator.validate_set_package(stage)
        need(report["ok"], "EXPORTED_PACKAGE_INVALID:" + ",".join(item["code"] for item in report["errors"]))
        need(sha256(source) == plan["source"]["sha256"], "SOURCE_CHANGED_DURING_EXPORT")
        need(not target.exists(), "OUTPUT_ALREADY_EXISTS")
        stage.rename(target)
        return {"package_id": plan["package_id"], "source_sha256": plan["source"]["sha256"],
                "manifest_sha256": sha256(target / "set.json"), "file_count": report["file_count"],
                "visual_alignment_verified": False}
