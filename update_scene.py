"""
update_scene.py
Run this after import_layers.py has been run once and the .blend saved.
Re-applies materials, z-positions, camera, lighting, and animation
from layer_config.json WITHOUT re-importing any STL geometry.

Paste into Blender Text Editor and click Run Script (or Alt+P).
"""

import bpy
import json
import math
import os
from mathutils import Vector

CONFIG_PATH = "/Users/mattvenn/blender/layer_config.json"

# ─────────────────────────────────────────────
# Helpers (same as build_scene.py)
# ─────────────────────────────────────────────

def load_config(path):
    with open(path) as f:
        return json.load(f)


def make_material(layer_cfg):
    name = layer_cfg['name']
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        r, g, b = layer_cfg['color']
        bsdf.inputs['Base Color'].default_value = (r, g, b, 1.0)
        bsdf.inputs['Metallic'].default_value   = layer_cfg.get('metallic', 0.0)
        bsdf.inputs['Roughness'].default_value  = layer_cfg.get('roughness', 0.5)
        t = layer_cfg.get('transmission', 0.0)
        if t > 0:
            for key in ('Transmission Weight', 'Transmission'):
                if key in bsdf.inputs:
                    bsdf.inputs[key].default_value = t
                    break
    return mat


import contextlib

@contextlib.contextmanager
def kf_interp(interp_type):
    prefs = bpy.context.preferences.edit
    prev  = prefs.keyframe_new_interpolation_type
    prefs.keyframe_new_interpolation_type = interp_type
    try:
        yield
    finally:
        prefs.keyframe_new_interpolation_type = prev


def set_linear_rotation(obj, frame_start, frame_end, degrees):
    obj.animation_data_clear()
    with kf_interp('LINEAR'):
        obj.rotation_euler = (0, 0, 0)
        obj.keyframe_insert(data_path="rotation_euler", index=2, frame=frame_start)
        obj.rotation_euler = (0, 0, math.radians(degrees))
        obj.keyframe_insert(data_path="rotation_euler", index=2, frame=frame_end)


def animate_drop(obj, final_z, drop_start, drop_duration, drop_height, overshoot, total_frames):
    # Clear only location Z animation
    obj.animation_data_clear()
    start_z  = final_z + drop_height
    land_z   = final_z - overshoot
    drop_end = drop_start + drop_duration

    with kf_interp('CONSTANT'):
        obj.location.z = start_z
        obj.keyframe_insert(data_path="location", index=2, frame=1)
        obj.keyframe_insert(data_path="location", index=2, frame=drop_start)

    with kf_interp('BEZIER'):
        obj.location.z = land_z
        obj.keyframe_insert(data_path="location", index=2, frame=drop_end - 4)
        obj.location.z = final_z
        obj.keyframe_insert(data_path="location", index=2, frame=drop_end)
        obj.keyframe_insert(data_path="location", index=2, frame=total_frames)


def clear_lights_and_camera():
    for obj in list(bpy.data.objects):
        if obj.type in ('LIGHT', 'CAMERA'):
            bpy.data.objects.remove(obj, do_unlink=True)


def setup_camera(cfg, focus_obj):
    cam_data = bpy.data.cameras.new("Camera")
    cam_obj  = bpy.data.objects.new("Camera", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj

    if 'location' in cfg and 'rotation_euler' in cfg:
        # Use exact transform captured from viewport
        cam_obj.location       = cfg['location']
        cam_obj.rotation_euler = cfg['rotation_euler']
    else:
        dist = cfg['distance']
        elev = math.radians(cfg['elevation_degrees'])
        azim = math.radians(cfg['azimuth_degrees'])
        cam_obj.location = (
            dist * math.cos(elev) * math.cos(azim),
            dist * math.cos(elev) * math.sin(azim),
            dist * math.sin(elev) + focus_obj.location.z,
        )
        direction = focus_obj.location - cam_obj.location
        cam_obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()

    cam_data.lens               = cfg['focal_length']
    cam_data.dof.use_dof        = True
    cam_data.dof.focus_object   = focus_obj
    cam_data.dof.aperture_fstop = cfg['dof_fstop']
    return cam_obj


def setup_lighting(chip_center_z):
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get('Background')
    if bg:
        bg.inputs['Color'].default_value    = (0.05, 0.06, 0.10, 1.0)
        bg.inputs['Strength'].default_value = 1.5

    def add_area(name, energy, size, loc, rot_deg):
        d   = bpy.data.lights.new(name, type='AREA')
        obj = bpy.data.objects.new(name, d)
        bpy.context.scene.collection.objects.link(obj)
        d.energy           = energy
        d.size             = size
        obj.location       = loc
        obj.rotation_euler = tuple(math.radians(a) for a in rot_deg)

    add_area("KeyLight", 6000, 4,
             (9, -7, chip_center_z + 10), (50, 0, 40))
    add_area("FillLight", 2000, 9,
             (-11, 9, chip_center_z + 7), (35, 0, -50))

    rim_d   = bpy.data.lights.new("RimLight", type='SPOT')
    rim_obj = bpy.data.objects.new("RimLight", rim_d)
    bpy.context.scene.collection.objects.link(rim_obj)
    rim_d.energy         = 4000
    rim_d.spot_size      = math.radians(25)
    rim_d.spot_blend     = 0.3
    rim_obj.location     = (-6, -9, chip_center_z - 3)
    rim_obj.rotation_euler = (math.radians(-55), 0, math.radians(-40))


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    cfg       = load_config(CONFIG_PATH)
    layers    = cfg['layers']
    anim_cfg  = cfg['animation']
    z_spacing = cfg['layer_z_spacing']

    # ── Materials & Z positions ───────────────────────────────
    missing = []
    for i, lc in enumerate(layers):
        obj = bpy.data.objects.get(lc['name'])
        if obj is None:
            missing.append(lc['name'])
            continue

        # Z position
        obj.location.z = i * z_spacing

        # Material
        mat = make_material(lc)
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)

    if missing:
        print(f"WARNING: objects not found (run import_layers.py first): {missing}")

    # ── Camera & lighting ─────────────────────────────────────
    chip_center_z = ((len(layers) - 1) * z_spacing) / 2

    focus_empty = bpy.data.objects.get("FocusPoint")
    if focus_empty:
        focus_empty.location.z = chip_center_z
    else:
        bpy.ops.object.empty_add(type='SPHERE',
                                 location=(0, 0, chip_center_z),
                                 scale=(0.1, 0.1, 0.1))
        focus_empty = bpy.context.active_object
        focus_empty.name        = "FocusPoint"
        focus_empty.hide_render = True

    clear_lights_and_camera()
    setup_camera(cfg['camera'], focus_empty)
    setup_lighting(chip_center_z)

    # ── Animation ─────────────────────────────────────────────
    rot_empty = bpy.data.objects.get("ChipRotation")
    if rot_empty is None:
        print("ERROR: 'ChipRotation' empty not found — run import_layers.py first.")
        return

    set_linear_rotation(rot_empty, 1, anim_cfg['total_frames'],
                        anim_cfg['rotation_degrees'])

    n            = len(layers)
    first_drop   = anim_cfg['first_drop_frame']
    last_drop    = anim_cfg['last_drop_start_frame']
    duration     = anim_cfg['drop_duration_frames']
    drop_height  = anim_cfg['drop_height']
    overshoot    = anim_cfg['overshoot']
    total_frames = anim_cfg['total_frames']

    for i, lc in enumerate(layers):
        obj = bpy.data.objects.get(lc['name'])
        if obj is None:
            continue
        t = i / max(n - 1, 1)
        drop_start = int(first_drop + t * (last_drop - first_drop))
        animate_drop(obj, obj.location.z, drop_start, duration,
                     drop_height, overshoot, total_frames)

    bpy.context.scene.frame_set(1)
    print("update_scene.py done — materials, camera, lighting, and animation updated.")


main()
