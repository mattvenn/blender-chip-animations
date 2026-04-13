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


def bbox_z_extent(obj):
    """Return (z_min, z_max) of obj in local space (after scale applied)."""
    zs = [Vector(c).z for c in obj.bound_box]
    return min(zs), max(zs)

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
    # LINEAR extrapolation ensures constant rate through the loop point
    if obj.animation_data and obj.animation_data.action:
        action = obj.animation_data.action
        try:
            fcurves = action.fcurves  # Blender < 4.4
        except AttributeError:
            fcurves = [fc for layer in getattr(action, 'layers', [])
                          for strip in layer.strips
                          for cb in strip.channelbags
                          for fc in cb.fcurves]
        for fc in fcurves:
            if fc.data_path == "rotation_euler" and fc.array_index == 2:
                fc.extrapolation = 'LINEAR'


def animate_layer(obj, final_z, layer_idx, n_active, anim_cfg):
    fly_first   = anim_cfg['fly_off_first_frame']
    stagger     = anim_cfg['stagger_frames']
    dur         = anim_cfg['duration_frames']
    gap         = anim_cfg['gap_frames']
    height      = anim_cfg['drop_height']
    overshoot   = anim_cfg['overshoot']
    total       = anim_cfg['total_frames']
    easing_type = anim_cfg.get('easing_type', 'BEZIER')

    fly_start  = fly_first + (n_active - 1 - layer_idx) * stagger
    fly_end    = fly_start + dur

    drop_first = fly_first + (n_active - 1) * stagger + dur + gap
    drop_start = drop_first + layer_idx * stagger
    drop_end   = drop_start + dur

    gone_z = final_z + height
    land_z = final_z - overshoot

    obj.animation_data_clear()

    with kf_interp(easing_type):
        obj.location.z = final_z
        obj.keyframe_insert(data_path="location", index=2, frame=1)
        obj.keyframe_insert(data_path="location", index=2, frame=fly_start)
        obj.location.z = gone_z
        obj.keyframe_insert(data_path="location", index=2, frame=fly_end)

    with kf_interp('CONSTANT'):
        obj.location.z = gone_z
        obj.keyframe_insert(data_path="location", index=2, frame=fly_end + 1)

    with kf_interp(easing_type):
        obj.location.z = gone_z
        obj.keyframe_insert(data_path="location", index=2, frame=drop_start)
        obj.location.z = land_z
        obj.keyframe_insert(data_path="location", index=2, frame=drop_end - 4)
        obj.location.z = final_z
        obj.keyframe_insert(data_path="location", index=2, frame=drop_end)
        obj.keyframe_insert(data_path="location", index=2, frame=total)


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

    cam_data.lens     = cfg['focal_length']
    fstop = cfg.get('dof_fstop', 0)
    if fstop > 0:
        cam_data.dof.use_dof        = True
        cam_data.dof.focus_object   = focus_obj
        cam_data.dof.aperture_fstop = fstop
    else:
        cam_data.dof.use_dof = False
    return cam_obj


def setup_lighting(chip_center_z, light_cfg):
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get('Background')
    if bg:
        bg.inputs['Color'].default_value    = (0.05, 0.06, 0.10, 1.0)
        bg.inputs['Strength'].default_value = light_cfg.get('world_strength', 1.5)

    def add_area(name, energy, size, loc, rot_deg):
        d   = bpy.data.lights.new(name, type='AREA')
        obj = bpy.data.objects.new(name, d)
        bpy.context.scene.collection.objects.link(obj)
        d.energy           = energy
        d.size             = size
        obj.location       = loc
        obj.rotation_euler = tuple(math.radians(a) for a in rot_deg)

    add_area("KeyLight",  light_cfg.get('key_energy',  20000), 4,
             (9, -7, chip_center_z + 10), (50, 0, 40))
    add_area("FillLight", light_cfg.get('fill_energy',  8000), 9,
             (-11, 9, chip_center_z + 7), (35, 0, -50))

    rim_d   = bpy.data.lights.new("RimLight", type='SPOT')
    rim_obj = bpy.data.objects.new("RimLight", rim_d)
    bpy.context.scene.collection.objects.link(rim_obj)
    rim_d.energy         = light_cfg.get('rim_energy', 4000)
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
    light_cfg = cfg.get('lighting', {})

    # ── Materials & Z positions ───────────────────────────────
    missing = []
    current_z = 0.0
    for lc in layers:
        obj = bpy.data.objects.get(lc['name'])
        if obj is None:
            missing.append(lc['name'])
            continue

        # Stack flush on top of previous layer
        z_min, z_max = bbox_z_extent(obj)
        obj.location.z = current_z - z_min
        current_z += (z_max - z_min)

        # Material
        mat = make_material(lc)
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)

    if missing:
        print(f"WARNING: objects not found (run build_scene.py first): {missing}")

    # ── Camera & lighting ─────────────────────────────────────
    chip_center_z = current_z / 2

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
    setup_lighting(chip_center_z, light_cfg)

    # ── Animation ─────────────────────────────────────────────
    rot_empty = bpy.data.objects.get("ChipRotation")
    if rot_empty is None:
        print("ERROR: 'ChipRotation' empty not found — run import_layers.py first.")
        return

    set_linear_rotation(rot_empty, 1, anim_cfg['total_frames'],
                        anim_cfg['rotation_degrees'])

    # Apply visibility and collect active (visible) layers
    active = []
    for lc in layers:
        obj = bpy.data.objects.get(lc['name'])
        if obj is None:
            continue
        hidden = lc.get('hidden', False)
        obj.hide_render   = hidden
        obj.hide_viewport = hidden
        if not hidden:
            active.append((lc, obj))

    n_active = len(active)
    for layer_idx, (lc, obj) in enumerate(active):
        animate_layer(obj, obj.location.z, layer_idx, n_active, anim_cfg)

    # Sync frame range, fps, and motion blur from config
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end   = anim_cfg['total_frames']
    scene.render.fps  = anim_cfg['fps']
    scene.render.use_motion_blur = cfg.get('motion_blur', False)
    engine = cfg.get('render_engine', 'CYCLES')
    if engine == 'CYCLES':
        scene.render.engine = 'CYCLES'
    else:
        for eng in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
            try:
                scene.render.engine = eng
                break
            except TypeError:
                continue

    bpy.context.scene.frame_set(1)
    print("update_scene.py done — materials, camera, lighting, and animation updated.")


main()
