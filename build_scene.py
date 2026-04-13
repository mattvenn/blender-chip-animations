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


def animate_drop(obj, final_z, drop_start, drop_duration, drop_height, overshoot, total_frames):
    """
    Animate obj.location.z:
      - stays high (CONSTANT) until drop_start
      - falls with BEZIER easing, slight overshoot, then settles at final_z
    """
    start_z  = final_z + drop_height
    land_z   = final_z - overshoot
    drop_end = drop_start + drop_duration

    # Hidden phase — no interpolation between these two frames
    with _kf_interp('CONSTANT'):
        obj.location.z = start_z
        obj.keyframe_insert(data_path="location", index=2, frame=1)
        obj.keyframe_insert(data_path="location", index=2, frame=drop_start)

    # Drop + settle — smooth Bezier (AUTO_CLAMPED handles by default)
    with _kf_interp('BEZIER'):
        obj.location.z = land_z
        obj.keyframe_insert(data_path="location", index=2, frame=drop_end - 4)
        obj.location.z = final_z
        obj.keyframe_insert(data_path="location", index=2, frame=drop_end)
        obj.keyframe_insert(data_path="location", index=2, frame=total_frames)


# ─────────────────────────────────────────────
# Render settings
# ─────────────────────────────────────────────

def setup_render(scene, anim_cfg):
    scene.frame_start = 1
    scene.frame_end   = anim_cfg['total_frames']
    scene.render.fps  = anim_cfg['fps']
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.film_transparent = False

    # Try EEVEE Next (Blender 4.2+) then fall back to legacy EEVEE
    for engine in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue

    # EEVEE quality tweaks (attribute names differ across versions)
    eevee = getattr(scene, 'eevee', None)
    if eevee:
        for attr, val in [
            ('use_shadows',   True),
            ('use_gtao',      True),
            ('gtao_distance', 0.2),
            ('use_bloom',     True),   # removed in 4.2, handled gracefully
            ('bloom_threshold', 0.9),
        ]:
            if hasattr(eevee, attr):
                setattr(eevee, attr, val)

    # Motion blur
    scene.render.use_motion_blur = True
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
    scale     = cfg['scale']
    z_spacing = cfg['layer_z_spacing']

    print("\n=== Building chip scene ===")
    clear_scene()

    # ── 1. Import & position layers ──────────────────────────
    layer_objects = []
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
        obj.scale = (scale, scale, scale)
        apply_scale(obj)
        obj.location.z = i * z_spacing

        mat = make_material(lc)
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)

        layer_objects.append(obj)
        print(f"    → placed at Z = {obj.location.z:.3f}")

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
    chip_center_z = ((len(layers) - 1) * z_spacing) / 2
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

    # ── 6. Rotation animation ─────────────────────────────────
    set_linear_rotation(rot_empty, 1, anim_cfg['total_frames'], anim_cfg['rotation_degrees'])

    # ── 7. Layer drop animations ──────────────────────────────
    n = len(layers)
    first_drop   = anim_cfg['first_drop_frame']
    last_drop    = anim_cfg['last_drop_start_frame']
    duration     = anim_cfg['drop_duration_frames']
    drop_height  = anim_cfg['drop_height']
    overshoot    = anim_cfg['overshoot']
    total_frames = anim_cfg['total_frames']

    for i, (lc, obj) in enumerate(zip(layers, layer_objects)):
        if obj is None:
            continue

        if i == 0:
            # Substrate is already in place — pin it at final Z for all frames
            final_z = obj.location.z
            with _kf_interp('CONSTANT'):
                obj.location.z = final_z
                obj.keyframe_insert(data_path="location", index=2, frame=1)
                obj.keyframe_insert(data_path="location", index=2, frame=total_frames)
            print(f"  {lc['name']:12s} pinned at Z={final_z:.3f} (no drop)")
            continue

        t = i / max(n - 1, 1)
        drop_start = int(first_drop + t * (last_drop - first_drop))
        final_z    = obj.location.z   # local Z relative to parent

        animate_drop(obj, final_z, drop_start, duration,
                     drop_height, overshoot, total_frames)
        print(f"  {lc['name']:12s} drops at frame {drop_start:3d}, settles at Z={final_z:.3f}")

    # ── 8. Render settings ────────────────────────────────────
    setup_render(bpy.context.scene, anim_cfg)

    # Park timeline at frame 1 so Viewport shows layers ascending
    bpy.context.scene.frame_set(1)

    # ── 9. Save a checkpoint .blend ──────────────────────────
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
