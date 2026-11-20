"""Synthetic Blender fixture only. Run headless with -- <existing workspace>."""
import sys
from pathlib import Path
import bpy
from mathutils import Vector


root = Path(sys.argv[sys.argv.index("--") + 1])
assert bpy.app.background and root.is_dir()
project = root / "project"
project.mkdir(exist_ok=False)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = 1.0
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 4
scene.render.resolution_x = 160
scene.render.resolution_y = 90
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
scene.view_settings.exposure = 0
scene.world.color = (.15, .15, .15)


def cube(name, location, scale, color):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    mat = bpy.data.materials.new(name + "_material")
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Roughness"].default_value = .65
    obj.data.materials.append(mat)
    return obj


floor = cube("SET_floor", (0, 0, -.1), (6, 4, .2), (.65, .65, .65))
block = cube("SET_block", (0, 0, .65), (1.2, .7, 1.3), (.1, .4, .7))
block.rotation_euler.z = .4
cube("ACTOR_unselected", (0, -1.5, .4), (1, .5, .8), (.9, .1, .1))
for name, location, projection in (("Camera", (5, -7, 4), "PERSP"), ("TopCamera", (0, 0, 8), "ORTHO")):
    data = bpy.data.cameras.new(name)
    data.type = projection
    data.lens = 42
    data.ortho_scale = 7
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.location = location
    if projection == "PERSP":
        obj.rotation_euler = (Vector((0, 0, .3)) - obj.location).to_track_quat("-Z", "Y").to_euler()
    else:
        obj.rotation_euler = (0, 0, 0)
scene.camera = scene.objects["Camera"]
light = bpy.data.lights.new("Key", "AREA")
light.energy = 800
light.shape = "DISK"
light.size = 5
obj = bpy.data.objects.new("Key", light)
scene.collection.objects.link(obj)
obj.location = (2, -3, 7)
obj.rotation_euler = (Vector((0, 0, 0)) - obj.location).to_track_quat("-Z", "Y").to_euler()
bpy.ops.wm.save_as_mainfile(filepath=str(project / "prepared.blend"))
scene.objects["Camera"].data.shift_x = .1
bpy.ops.wm.save_as_mainfile(filepath=str(project / "shifted.blend"))
scene.objects["Camera"].data.shift_x = 0
scene.objects["TopCamera"].data.sensor_fit = "VERTICAL"
bpy.ops.wm.save_as_mainfile(filepath=str(project / "vertical-ortho.blend"))
scene.objects["TopCamera"].data.sensor_fit = "AUTO"
block.material_slots[0].link = "OBJECT"
override = bpy.data.materials.new("Object_override")
override.use_nodes = True
override.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = (.7, .2, .1, 1)
block.material_slots[0].material = override
bpy.ops.wm.save_as_mainfile(filepath=str(project / "object-material.blend"))
object_noise = override.node_tree.nodes.new("ShaderNodeTexNoise")
override.node_tree.links.new(object_noise.outputs["Color"], override.node_tree.nodes.get("Principled BSDF").inputs["Base Color"])
bpy.ops.wm.save_as_mainfile(filepath=str(project / "object-noise.blend"))
block.material_slots[0].link = "DATA"
modifier = block.modifiers.new("UnsupportedGeometry", "NODES")
bpy.ops.wm.save_as_mainfile(filepath=str(project / "geometry-nodes.blend"))
block.modifiers.remove(modifier)
mat = floor.data.materials[0]
coordinates = mat.node_tree.nodes.new("ShaderNodeTexCoord")
mat.node_tree.links.new(coordinates.outputs["Object"], mat.node_tree.nodes.get("Principled BSDF").inputs["Base Color"])
bpy.ops.wm.save_as_mainfile(filepath=str(project / "object-coordinates.blend"))
mat.node_tree.nodes.remove(coordinates)
value = mat.node_tree.nodes.new("ShaderNodeValue")
mat.node_tree.links.new(value.outputs["Value"], mat.node_tree.nodes.get("Material Output").inputs["Displacement"])
bpy.ops.wm.save_as_mainfile(filepath=str(project / "displacement.blend"))
mat.node_tree.nodes.remove(value)
texture = mat.node_tree.nodes.new("ShaderNodeTexImage")
mat.node_tree.links.new(texture.outputs["Color"], mat.node_tree.nodes.get("Principled BSDF").inputs["Base Color"])
bpy.ops.wm.save_as_mainfile(filepath=str(project / "missing-texture.blend"))
mat.node_tree.nodes.remove(texture)
noise = mat.node_tree.nodes.new("ShaderNodeTexNoise")
mat.node_tree.links.new(noise.outputs["Color"], mat.node_tree.nodes.get("Principled BSDF").inputs["Base Color"])
bpy.ops.wm.save_as_mainfile(filepath=str(project / "procedural.blend"))
