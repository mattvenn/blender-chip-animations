"""
update_scene.py
Run after build_scene.py has been run once and the .blend saved.
Re-applies materials, z-positions, camera, lighting, and animation
from layer_config.json WITHOUT re-importing any STL geometry.

Paste into Blender Text Editor and click Run Script (or Alt+P).
"""

import bpy
import json
import math
import os
import contextlib
from mathutils import Vector


def bbox_z_extent(obj):
    zs = [Vector(c).z for c in obj.bound_box]
    return min(zs), max(zs)


def compute_chip_bounds(objects):
    """Return (x_min, x_max, y_min, y_max) of all objects in world space."""
    xs, ys = [], []
    for obj in objects:
        for c in obj.bound_box:
            v = obj.matrix_world @ Vector(c)
            xs.append(v.x)
            ys.append(v.y)
    return min(xs), max(xs), min(ys), max(ys)


# Derive config path from the open .blend file's location.
if not bpy.data.filepath:
    raise RuntimeError(
        "No .blend file is open/saved. Open your animation's chip_scene.blend first."
    )
CONFIG_PATH = os.path.join(os.path.dirname(bpy.data.filepath), "layer_config.json")

# ─────────────────────────────────────────────
# Helpers
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
    if obj.animation_data and obj.animation_data.action:
        action = obj.animation_data.action
        try:
            fcurves = action.fcurves
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


def _add_via_driver(obj, data_path, index, expression, below_obj, above_obj):
    """Add a scripted driver to obj[data_path][index] with variables bz/az."""
    fc  = obj.driver_add(data_path, index)
    drv = fc.driver
    drv.type       = 'SCRIPTED'
    drv.expression = expression

    for var_name, target_obj in (("bz", below_obj), ("az", above_obj)):
        v = drv.variables.new()
        v.name = var_name
        v.type = 'TRANSFORMS'
        v.targets[0].id             = target_obj
        v.targets[0].transform_type  = 'LOC_Z'
        v.targets[0].transform_space = 'LOCAL_SPACE'


def animate_drift_loop_layers(layer_stack, anim_cfg):
    """
    Non-via layers: 3-keyframe BEZIER loop mirrored about chip_center_z.
    Via layers: Blender drivers that read neighbouring layer positions every
    frame and elastically scale/position the via to always span the exact gap.
    Collapses to zero when neighbours cross.  Frame 1 == frame total.
    """
    easing = anim_cfg.get('easing_type', 'BEZIER')
    total  = anim_cfg['total_frames']
    mid    = total // 2

    chip_z_bottom = layer_stack[0]['z_init'] + layer_stack[0]['z_min_local']
    chip_z_top    = (layer_stack[-1]['z_init']
                     + layer_stack[-1]['z_min_local']
                     + layer_stack[-1]['height'])
    chip_center_z = (chip_z_bottom + chip_z_top) / 2

    for i, entry in enumerate(layer_stack):
        obj       = entry['obj']
        is_via    = entry['is_via']
        z_init    = entry['z_init']
        z_mid_pos = 2 * chip_center_z - z_init

        obj.animation_data_clear()

        if not is_via:
            with kf_interp(easing):
                obj.location.z = z_init
                obj.keyframe_insert(data_path="location", index=2, frame=1)
                obj.location.z = z_mid_pos
                obj.keyframe_insert(data_path="location", index=2, frame=mid)
                obj.location.z = z_init
                obj.keyframe_insert(data_path="location", index=2, frame=total)
        else:
            below = next((layer_stack[j] for j in range(i - 1, -1, -1)
                          if not layer_stack[j]['is_via']), None)
            above = next((layer_stack[j] for j in range(i + 1, len(layer_stack))
                          if not layer_stack[j]['is_via']), None)

            if not below or not above:
                # No neighbours — animate like a plain layer
                with kf_interp(easing):
                    obj.location.z = z_init
                    obj.keyframe_insert(data_path="location", index=2, frame=1)
                    obj.location.z = z_mid_pos
                    obj.keyframe_insert(data_path="location", index=2, frame=mid)
                    obj.location.z = z_init
                    obj.keyframe_insert(data_path="location", index=2, frame=total)
                continue

            # Constants baked into the driver expressions (local-space geometry offsets)
            b_top = below['z_min_local'] + below['height']  # local top of below layer
            a_bot = above['z_min_local']                     # local bottom of above layer
            v_bot = entry['z_min_local']                     # local bottom of via
            h     = entry['height']                          # original via height

            # scale.z: abs() so the via stays visible after layers cross
            s_expr = f"abs(az+{a_bot:.8f}-bz-{b_top:.8f})/{h:.8f}"
            # location.z: sit on whichever surface is currently lower
            l_expr = f"min(bz+{b_top:.8f},az+{a_bot:.8f})-{v_bot:.8f}*abs(az+{a_bot:.8f}-bz-{b_top:.8f})/{h:.8f}"

            _add_via_driver(obj, "scale",    2, s_expr, below['obj'], above['obj'])
            _add_via_driver(obj, "location", 2, l_expr, below['obj'], above['obj'])


def animate_flythrough_layers(layer_stack, anim_cfg):
    """
    Drift layers symmetrically apart from the chip centre.

    Vias above centre grow upward (bottom fixed, top rises).
    Vias below centre grow downward (top fixed, bottom falls).
    The centre of the chip stays stationary.
    """
    drift_start = max(1, anim_cfg['drift_start_frame'])
    drift_end   = anim_cfg['drift_end_frame']
    via_scale   = anim_cfg['via_drift_scale']
    easing      = anim_cfg.get('easing_type', 'BEZIER')

    # Find chip centre Z from assembled bounding extents
    chip_z_bottom = layer_stack[0]['z_init'] + layer_stack[0]['z_min_local']
    chip_z_top    = (layer_stack[-1]['z_init']
                     + layer_stack[-1]['z_min_local']
                     + layer_stack[-1]['height'])
    chip_center_z = (chip_z_bottom + chip_z_top) / 2

    # Tag each via: grows 'up' (above centre) or 'down' (below centre)
    for entry in layer_stack:
        if entry['is_via']:
            via_mid = entry['z_init'] + entry['z_min_local'] + entry['height'] / 2
            entry['_via_dir'] = 'up' if via_mid >= chip_center_z else 'down'

    # Compute signed drift for each layer:
    #   +drift from every 'up' via that sits below this layer (pushes it upward)
    #   -drift from every 'down' via that sits above this layer (pushes it downward)
    for i, entry in enumerate(layer_stack):
        z_drift = 0.0
        for j, other in enumerate(layer_stack):
            if not other['is_via']:
                continue
            growth = other['height'] * (via_scale - 1.0)
            if other['_via_dir'] == 'up' and j < i:
                z_drift += growth
            elif other['_via_dir'] == 'down' and j > i:
                z_drift -= growth
        entry['_z_drift'] = z_drift

    for entry in layer_stack:
        obj         = entry['obj']
        z_init      = entry['z_init']
        z_drift     = entry['_z_drift']
        is_via      = entry['is_via']
        z_min_local = entry['z_min_local']

        obj.animation_data_clear()

        if is_via:
            if entry['_via_dir'] == 'up':
                pivot_correction = -z_min_local * (via_scale - 1.0)
            else:
                z_max_local = z_min_local + entry['height']
                pivot_correction = -z_max_local * (via_scale - 1.0)
            z_final = z_init + z_drift + pivot_correction
        else:
            z_final = z_init + z_drift

        with kf_interp(easing):
            obj.location.z = z_init
            obj.keyframe_insert(data_path="location", index=2, frame=1)
            if drift_start > 1:
                obj.keyframe_insert(data_path="location", index=2, frame=drift_start)
            obj.location.z = z_final
            obj.keyframe_insert(data_path="location", index=2, frame=drift_end)

        if is_via:
            with kf_interp(easing):
                obj.scale.z = 1.0
                obj.keyframe_insert(data_path="scale", index=2, frame=1)
                if drift_start > 1:
                    obj.keyframe_insert(data_path="scale", index=2, frame=drift_start)
                obj.scale.z = via_scale
                obj.keyframe_insert(data_path="scale", index=2, frame=drift_end)


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

    cam_data.lens = cfg['focal_length']
    fstop = cfg.get('dof_fstop', 0)
    if fstop > 0:
        cam_data.dof.use_dof        = True
        cam_data.dof.focus_object   = focus_obj
        cam_data.dof.aperture_fstop = fstop
    else:
        cam_data.dof.use_dof = False
    return cam_obj


def setup_camera_drift_loop(cam_cfg, anim_cfg):
    """Static camera from saved position; animates a zoom-in to midpoint and back."""
    cam_data = bpy.data.cameras.new("Camera")
    cam_obj  = bpy.data.objects.new("Camera", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj

    cam_data.lens        = cam_cfg['focal_length']
    cam_data.dof.use_dof = False

    start_loc = Vector(cam_cfg['location'])
    cam_obj.location       = start_loc
    cam_obj.rotation_euler = cam_cfg['rotation_euler']

    # Camera forward direction in world space (local -Z)
    rot     = cam_obj.rotation_euler.to_matrix()
    forward = (rot @ Vector((0, 0, -1))).normalized()

    zoom_dist = anim_cfg.get('zoom_distance', 0.05)
    total     = anim_cfg['total_frames']
    mid       = total // 2
    easing    = anim_cfg.get('easing_type', 'BEZIER')
    mid_loc   = start_loc + forward * zoom_dist

    with kf_interp(easing):
        cam_obj.location = start_loc
        cam_obj.keyframe_insert(data_path="location", frame=1)
        cam_obj.location = mid_loc
        cam_obj.keyframe_insert(data_path="location", frame=mid)
        cam_obj.location = start_loc
        cam_obj.keyframe_insert(data_path="location", frame=total)

    return cam_obj


def setup_camera_flythrough(cam_cfg, anim_cfg, chip_bounds, chip_center_z):
    cam_data = bpy.data.cameras.new("Camera")
    cam_obj  = bpy.data.objects.new("Camera", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj

    cam_data.lens        = cam_cfg['focal_length']
    cam_data.dof.use_dof = False

    x_min, x_max, y_min, y_max = chip_bounds
    axis      = cam_cfg.get('flight_axis', 'Y')
    direction = cam_cfg.get('flight_direction', 1)
    offset    = cam_cfg.get('start_offset', 2.0)
    height_z  = chip_center_z + cam_cfg.get('height_offset', 0.0)

    if axis == 'Y':
        if direction >= 0:
            start_pos = Vector((0, y_min - offset, height_z))
            end_pos   = Vector((0, y_max + offset, height_z))
        else:
            start_pos = Vector((0, y_max + offset, height_z))
            end_pos   = Vector((0, y_min - offset, height_z))
    else:  # X
        if direction >= 0:
            start_pos = Vector((x_min - offset, 0, height_z))
            end_pos   = Vector((x_max + offset, 0, height_z))
        else:
            start_pos = Vector((x_max + offset, 0, height_z))
            end_pos   = Vector((x_min - offset, 0, height_z))

    fly_dir = (end_pos - start_pos).normalized()
    cam_obj.rotation_euler = fly_dir.to_track_quat('-Z', 'Y').to_euler()

    flight_frames = anim_cfg['flight_duration_frames']
    cam_obj.location = start_pos
    cam_obj.keyframe_insert(data_path="location", frame=1)
    cam_obj.location = end_pos
    cam_obj.keyframe_insert(data_path="location", frame=flight_frames + 1)

    if cam_obj.animation_data and cam_obj.animation_data.action:
        action = cam_obj.animation_data.action
        try:
            fcurves = action.fcurves
        except AttributeError:
            fcurves = [fc for layer in getattr(action, 'layers', [])
                          for strip in layer.strips
                          for cb in strip.channelbags
                          for fc in cb.fcurves]
        for fc in fcurves:
            fc.extrapolation = 'LINEAR'
            for kp in fc.keyframe_points:
                kp.interpolation = 'LINEAR'

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
    anim_type = anim_cfg.get('type', 'layer_explode_loop')
    light_cfg = cfg.get('lighting', {})

    # ── Materials & Z positions ───────────────────────────────
    layer_stack = []
    missing = []
    current_z = 0.0
    for lc in layers:
        obj = bpy.data.objects.get(lc['name'])
        if obj is None:
            missing.append(lc['name'])
            continue

        z_min, z_max = bbox_z_extent(obj)
        obj.location.z = current_z - z_min
        height = z_max - z_min
        current_z += height

        mat = make_material(lc)
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)

        layer_stack.append({
            'lc':          lc,
            'obj':         obj,
            'z_init':      obj.location.z,
            'height':      height,
            'z_min_local': z_min,
            'is_via':      lc.get('is_via', False),
        })

    if missing:
        print(f"WARNING: objects not found (run build_scene.py first): {missing}")

    valid_objects = [e['obj'] for e in layer_stack]

    # ── Chip geometry ─────────────────────────────────────────
    chip_center_z = current_z / 2
    chip_bounds   = compute_chip_bounds(valid_objects) if valid_objects else (0, 0, 0, 0)

    # ── Focus Empty ───────────────────────────────────────────
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

    # ── Camera & lighting ─────────────────────────────────────
    clear_lights_and_camera()

    if anim_type == 'layer_explode_loop':
        setup_camera(cfg['camera'], focus_empty)
    elif anim_type == 'camera_flythrough':
        setup_camera_flythrough(cfg['camera'], anim_cfg, chip_bounds, chip_center_z)
    elif anim_type == 'drift_loop':
        setup_camera_drift_loop(cfg['camera'], anim_cfg)
    else:
        print(f"WARNING: unknown animation type '{anim_type}', skipping camera setup")

    setup_lighting(chip_center_z, light_cfg)

    # ── Visibility & active stack ─────────────────────────────
    active_stack = []
    for entry in layer_stack:
        hidden = entry['lc'].get('hidden', False)
        entry['obj'].hide_render   = hidden
        entry['obj'].hide_viewport = hidden
        if not hidden:
            active_stack.append(entry)

    # ── Animation ─────────────────────────────────────────────
    if anim_type == 'layer_explode_loop':
        rot_empty = bpy.data.objects.get("ChipRotation")
        if rot_empty is None:
            print("ERROR: 'ChipRotation' empty not found — run build_scene.py first.")
            return
        set_linear_rotation(rot_empty, 1, anim_cfg['total_frames'],
                            anim_cfg['rotation_degrees'])
        n_active = len(active_stack)
        for layer_idx, entry in enumerate(active_stack):
            animate_layer(entry['obj'], entry['obj'].location.z, layer_idx, n_active, anim_cfg)

    elif anim_type == 'camera_flythrough':
        animate_flythrough_layers(active_stack, anim_cfg)
        print(f"  Flythrough drift set for {len(active_stack)} layers")
    elif anim_type == 'drift_loop':
        animate_drift_loop_layers(active_stack, anim_cfg)
        print(f"  Drift loop set for {len(active_stack)} layers")

    # ── Render settings ───────────────────────────────────────
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
    print(f"update_scene.py done — {anim_type} updated.")


main()
