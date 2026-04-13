"""
build_scene.py
Paste into Blender's Text Editor (Scripting workspace) and click Run Script.
Tested against Blender 3.6 and 4.x.
"""

import bpy
import json
import math
import os
from mathutils import Vector, Matrix

CONFIG_PATH = "/Users/mattvenn/blender/layer_config.json"

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def load_config(path):
    with open(path) as f:
        return json.load(f)


def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in list(bpy.data.meshes):
        bpy.data.meshes.remove(block)
    for block in list(bpy.data.materials):
        bpy.data.materials.remove(block)
    for block in list(bpy.data.cameras):
        bpy.data.cameras.remove(block)
    for block in list(bpy.data.lights):
        bpy.data.lights.remove(block)


def import_stl(filepath):
    """Import one STL and return the new object. Handles Blender 3.x and 4.x."""
    before = set(o.name for o in bpy.data.objects)
    try:
        # Blender 4.0+
        bpy.ops.wm.stl_import(filepath=filepath)
    except AttributeError:
        # Blender 3.x
        bpy.ops.import_mesh.stl(filepath=filepath)
    after = set(o.name for o in bpy.data.objects)
    new_names = after - before
    if not new_names:
        print(f"  ERROR: nothing imported from {filepath}")
        return None
    return bpy.data.objects[new_names.pop()]


def apply_scale(obj):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


def bbox_center_xy(obj):
    """Return (cx, cy) of the object bounding box in world space."""
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs = [v.x for v in corners]
    ys = [v.y for v in corners]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def bbox_z_extent(obj):
    """Return (z_min, z_max) of obj in local space (after scale applied)."""
    zs = [Vector(c).z for c in obj.bound_box]
    return min(zs), max(zs)


# ─────────────────────────────────────────────
# Materials
# ─────────────────────────────────────────────

def make_material(layer_cfg):
    mat = bpy.data.materials.new(name=layer_cfg['name'])
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get('Principled BSDF')
    if bsdf is None:
        return mat

    r, g, b = layer_cfg['color']
    bsdf.inputs['Base Color'].default_value = (r, g, b, 1.0)
    bsdf.inputs['Metallic'].default_value = layer_cfg.get('metallic', 0.0)
    bsdf.inputs['Roughness'].default_value = layer_cfg.get('roughness', 0.5)

    t = layer_cfg.get('transmission', 0.0)
    if t > 0:
        for key in ('Transmission Weight', 'Transmission'):
            if key in bsdf.inputs:
                bsdf.inputs[key].default_value = t
                break

    return mat


# ─────────────────────────────────────────────
# Camera
# ─────────────────────────────────────────────

def setup_camera(cfg, focus_obj):
    cam_data = bpy.data.cameras.new("Camera")
    cam_obj = bpy.data.objects.new("Camera", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj

    dist  = cfg['distance']
    elev  = math.radians(cfg['elevation_degrees'])
    azim  = math.radians(cfg['azimuth_degrees'])

    cx = dist * math.cos(elev) * math.cos(azim)
    cy = dist * math.cos(elev) * math.sin(azim)
    cz = dist * math.sin(elev) + focus_obj.location.z

    cam_obj.location = (cx, cy, cz)

    # Point at focus object
    direction = focus_obj.location - cam_obj.location
    rot_quat  = direction.to_track_quat('-Z', 'Y')
    cam_obj.rotation_euler = rot_quat.to_euler()

    cam_data.lens = cfg['focal_length']
    cam_data.dof.use_dof       = True
    cam_data.dof.focus_object  = focus_obj
    cam_data.dof.aperture_fstop = cfg['dof_fstop']

    return cam_obj


# ─────────────────────────────────────────────
# Lighting
# ─────────────────────────────────────────────

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
        d.energy = energy
        d.size   = size
        obj.location       = loc
        obj.rotation_euler = tuple(math.radians(a) for a in rot_deg)
        return obj

    add_area("KeyLight",  light_cfg.get('key_energy',  20000), 4,
             (9, -7, chip_center_z + 10), (50, 0, 40))
    add_area("FillLight", light_cfg.get('fill_energy',  8000), 9,
             (-11, 9, chip_center_z + 7), (35, 0, -50))

    rim_d   = bpy.data.lights.new("RimLight", type='SPOT')
    rim_obj = bpy.data.objects.new("RimLight", rim_d)
    bpy.context.scene.collection.objects.link(rim_obj)
    rim_d.energy     = light_cfg.get('rim_energy', 4000)
    rim_d.spot_size  = math.radians(25)
    rim_d.spot_blend = 0.3
    rim_obj.location       = (-6, -9, chip_center_z - 3)
    rim_obj.rotation_euler = (math.radians(-55), 0, math.radians(-40))


# ─────────────────────────────────────────────
# Animation helpers
# ─────────────────────────────────────────────

def _kf_interp(interp_type):
    """Context manager: temporarily set keyframe interpolation preference."""
    import contextlib
    @contextlib.contextmanager
    def _ctx():
        prefs = bpy.context.preferences.edit
        prev = prefs.keyframe_new_interpolation_type
        prefs.keyframe_new_interpolation_type = interp_type
        try:
            yield
        finally:
            prefs.keyframe_new_interpolation_type = prev
    return _ctx()


def set_linear_rotation(obj, frame_start, frame_end, degrees):
    """Rotate obj around Z, linearly, over the given frame range."""
    with _kf_interp('LINEAR'):
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
    """
    Full loop animation:
      - Frame 1: assembled
      - Fly off upward (top layer first)
      - Hold gone for gap_frames
      - Drop back in (bottom layer first)
      - End assembled (loops back to frame 1)
    """
    fly_first   = anim_cfg['fly_off_first_frame']
    stagger     = anim_cfg['stagger_frames']
    dur         = anim_cfg['duration_frames']
    gap         = anim_cfg['gap_frames']
    height      = anim_cfg['drop_height']
    overshoot   = anim_cfg['overshoot']
    total       = anim_cfg['total_frames']
    easing_type = anim_cfg.get('easing_type', 'BEZIER')

    # Fly off: top layer (idx n-1) first, bottom (idx 0) last
    fly_start = fly_first + (n_active - 1 - layer_idx) * stagger
    fly_end   = fly_start + dur

    # Drop back in: bottom (idx 0) first, top (idx n-1) last
    drop_first = fly_first + (n_active - 1) * stagger + dur + gap
    drop_start = drop_first + layer_idx * stagger
    drop_end   = drop_start + dur

    gone_z = final_z + height
    land_z = final_z - overshoot

    obj.animation_data_clear()

    # Assembled at start, smooth hold until fly_start
    with _kf_interp(easing_type):
        obj.location.z = final_z
        obj.keyframe_insert(data_path="location", index=2, frame=1)
        obj.keyframe_insert(data_path="location", index=2, frame=fly_start)
        # Fly off
        obj.location.z = gone_z
        obj.keyframe_insert(data_path="location", index=2, frame=fly_end)

    # Hold gone (CONSTANT from fly_end+1 until drop_start keyframe)
    with _kf_interp('CONSTANT'):
        obj.location.z = gone_z
        obj.keyframe_insert(data_path="location", index=2, frame=fly_end + 1)

    # Drop back in
    with _kf_interp(easing_type):
        obj.location.z = gone_z
        obj.keyframe_insert(data_path="location", index=2, frame=drop_start)
        obj.location.z = land_z
        obj.keyframe_insert(data_path="location", index=2, frame=drop_end - 4)
        obj.location.z = final_z
        obj.keyframe_insert(data_path="location", index=2, frame=drop_end)
        # End assembled — loops cleanly to frame 1
        obj.keyframe_insert(data_path="location", index=2, frame=total)


# ─────────────────────────────────────────────
# Render settings
# ─────────────────────────────────────────────

def setup_render(scene, anim_cfg, motion_blur, cfg):
    scene.frame_start = 1
    scene.frame_end   = anim_cfg['total_frames']
    scene.render.fps  = anim_cfg['fps']
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.film_transparent = False

    engine = cfg.get('render_engine', 'CYCLES')
    if engine == 'CYCLES':
        scene.render.engine = 'CYCLES'
    else:
        # EEVEE: try Next (4.2+) then fall back to legacy
        for eng in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
            try:
                scene.render.engine = eng
                break
            except TypeError:
                continue
        eevee = getattr(scene, 'eevee', None)
        if eevee:
            for attr, val in [
                ('use_shadows',    True),
                ('use_gtao',       True),
                ('gtao_distance',  0.2),
                ('use_bloom',      True),
                ('bloom_threshold', 0.9),
            ]:
                if hasattr(eevee, attr):
                    setattr(eevee, attr, val)

    # Motion blur
    scene.render.use_motion_blur = motion_blur
    scene.render.motion_blur_shutter = 0.3

    # Output: PNG sequence (safer for long renders)
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = "/tmp/chip_render/frame_"


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    cfg       = load_config(CONFIG_PATH)
    layers    = cfg['layers']
    anim_cfg  = cfg['animation']
    cam_cfg   = cfg['camera']
    light_cfg = cfg.get('lighting', {})
    folder    = cfg['stl_folder']
    scale            = cfg['scale']
    layer_thickness  = cfg['layer_thickness']

    print("\n=== Building chip scene ===")
    clear_scene()

    # ── 1. Import & position layers ──────────────────────────
    layer_objects = []
    current_z = 0.0
    for i, lc in enumerate(layers):
        filepath = os.path.join(folder, lc['filename'])
        if not os.path.exists(filepath):
            print(f"  SKIP (not found): {lc['filename']}")
            layer_objects.append(None)
            continue

        print(f"  Importing {lc['name']} …")
        obj = import_stl(filepath)
        if obj is None:
            layer_objects.append(None)
            continue

        obj.name  = lc['name']
        obj.scale = (scale, scale, scale * layer_thickness)
        apply_scale(obj)

        z_min, z_max = bbox_z_extent(obj)
        obj.location.z = current_z - z_min
        current_z += (z_max - z_min)

        mat = make_material(lc)
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)

        layer_objects.append(obj)
        print(f"    → placed at Z = {obj.location.z:.3f}, height = {z_max - z_min:.4f}")

    valid_objects = [o for o in layer_objects if o is not None]
    if not valid_objects:
        print("ERROR: No objects imported — check STL folder path in config.")
        return

    # ── 2. Centre in XY using first (largest footprint) object ─
    cx, cy = bbox_center_xy(valid_objects[0])
    print(f"\nChip XY centre (world): ({cx:.3f}, {cy:.3f})")
    for obj in valid_objects:
        obj.location.x -= cx
        obj.location.y -= cy

    # ── 3. Parent Empty at world origin ──────────────────────
    bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0, 0, 0))
    rot_empty = bpy.context.active_object
    rot_empty.name = "ChipRotation"
    for obj in valid_objects:
        obj.parent = rot_empty
        obj.matrix_parent_inverse = rot_empty.matrix_world.inverted()

    # ── 4. Focus Empty at chip vertical centre ───────────────
    chip_center_z = current_z / 2
    bpy.ops.object.empty_add(type='SPHERE', location=(0, 0, chip_center_z), scale=(0.1, 0.1, 0.1))
    focus_empty = bpy.context.active_object
    focus_empty.name = "FocusPoint"
    focus_empty.hide_render = True
    focus_empty.hide_viewport = False

    # ── 5. Camera & lighting ──────────────────────────────────
    cam_obj = setup_camera(cam_cfg, focus_empty)
    setup_lighting(chip_center_z, light_cfg)
    print(f"\nCamera at {tuple(round(v,2) for v in cam_obj.location)}")
    print(f"Chip centre Z = {chip_center_z:.3f}")

    # ── 7. Rotation animation ─────────────────────────────────
    set_linear_rotation(rot_empty, 1, anim_cfg['total_frames'], anim_cfg['rotation_degrees'])

    # ── 8. Layer animations ───────────────────────────────────
    total_frames = anim_cfg['total_frames']

    # Apply visibility and collect active (visible) layers
    active = []
    for lc, obj in zip(layers, layer_objects):
        if obj is None:
            continue
        hidden = lc.get('hidden', False)
        obj.hide_render   = hidden
        obj.hide_viewport = hidden
        if not hidden:
            active.append((lc, obj))

    n_active = len(active)
    for layer_idx, (lc, obj) in enumerate(active):
        final_z = obj.location.z
        animate_layer(obj, final_z, layer_idx, n_active, anim_cfg)
        print(f"  {lc['name']:12s} layer_idx={layer_idx}, Z={final_z:.3f}")

    # ── 9. Render settings ────────────────────────────────────
    setup_render(bpy.context.scene, anim_cfg, cfg.get('motion_blur', False), cfg)

    # Park timeline at frame 1 so Viewport shows layers ascending
    bpy.context.scene.frame_set(1)

    # ── 10. Save a checkpoint .blend ─────────────────────────
    blend_path = os.path.join(os.path.dirname(CONFIG_PATH), "chip_scene.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"\nSaved: {blend_path}")

    print("\n=== Done ===")
    print("  • For fast iteration: edit layer_config.json, then run update_scene.py")
    print("    (no re-import needed — just updates materials/camera/lighting/anim)")
    print("  • Press Space to preview the animation.")
    print("  • Scrub to frame 240 to see the final assembled chip.")
    print("  • Switch to Cycles in Render Properties for final quality output.")


main()
