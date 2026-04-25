"""
build_scene.py
Paste into Blender's Text Editor (Scripting workspace) and click Run Script.
Before running: File > Save As → animations/<name>/chip_scene.blend
The config is read from the same folder as the saved .blend.
Tested against Blender 3.6 and 4.x.
"""

import bpy
import json
import math
import os
import contextlib
from mathutils import Vector

# Derive config path from the open .blend file's location.
# Before running: File > Save As → animations/<name>/chip_scene.blend
if not bpy.data.filepath:
    raise RuntimeError(
        "Save a blank .blend into your animation folder first "
        "(File > Save As → animations/<name>/chip_scene.blend), then run this script."
    )
CONFIG_PATH = os.path.join(os.path.dirname(bpy.data.filepath), "layer_config.json")

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


def recreate_cut_cube(cfg):
    """Recreates cut_cube from saved transform and material in layer_config.json."""
    cc = cfg.get('cut_cube')
    if not cc:
        return
    bpy.ops.mesh.primitive_cube_add(size=2, location=cc['location'])
    cube = bpy.context.active_object
    cube.name           = "cut_cube"
    cube.rotation_euler = cc['rotation_euler']
    if 'dimensions' in cc:
        cube.dimensions = cc['dimensions']
    else:
        cube.scale = cc['scale']

    mat_data = cc.get('material')
    if mat_data:
        mat = make_material({'name': 'cut_cube', **mat_data})
        if cube.data.materials:
            cube.data.materials[0] = mat
        else:
            cube.data.materials.append(mat)

    print(f"cut_cube recreated: loc={cc['location']}  dimensions={cube.dimensions[:]}")


def add_boolean_cuts(active_stack, cutter_obj):
    """Add a DIFFERENCE Boolean modifier to every visible layer using cutter_obj."""
    for entry in active_stack:
        mod           = entry['obj'].modifiers.new(name="CutCube", type='BOOLEAN')
        mod.operation = 'DIFFERENCE'
        mod.object    = cutter_obj
        mod.solver    = 'FLOAT'
    print(f"  Boolean DIFFERENCE (FAST) applied to {len(active_stack)} layers")


def import_stl(filepath):
    """Import one STL and return the new object. Handles Blender 3.x and 4.x."""
    before = set(o.name for o in bpy.data.objects)
    try:
        bpy.ops.wm.stl_import(filepath=filepath)
    except AttributeError:
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
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs = [v.x for v in corners]
    ys = [v.y for v in corners]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


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


# ─────────────────────────────────────────────
# Materials
# ─────────────────────────────────────────────

def make_material(layer_cfg):
    mat = bpy.data.materials.new(name=layer_cfg['name'])
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf is None:
        return mat

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


# ─────────────────────────────────────────────
# Camera
# ─────────────────────────────────────────────

def setup_camera(cfg, focus_obj):
    """Static/positioned camera for layer_explode_loop."""
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
        cx = dist * math.cos(elev) * math.cos(azim)
        cy = dist * math.cos(elev) * math.sin(azim)
        cz = dist * math.sin(elev) + focus_obj.location.z
        cam_obj.location = (cx, cy, cz)
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


def setup_camera_analog_zoom(cam_cfg, anim_cfg, chip_bounds, chip_center_z):
    """
    Top-down start, then for each waypoint in anim_cfg['camera_waypoints']:
      hold hold_frames, then BEZIER ease over move_frames.
    Location, rotation, and focal length are all animated.
    """
    cam_data = bpy.data.cameras.new("Camera")
    cam_obj  = bpy.data.objects.new("Camera", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj
    cam_data.dof.use_dof = False

    x_min, x_max, y_min, y_max = chip_bounds
    chip_h = y_max - y_min

    start_fl = anim_cfg.get('start_focal_length', 50.0)
    sensor_h = 36.0 * (1080.0 / 1920.0)
    fraction = anim_cfg.get('overhead_chip_fraction', 0.5)
    overhead_h = chip_center_z + chip_h * start_fl / (fraction * sensor_h)

    start_loc = Vector((0.0, 0.0, overhead_h))
    start_rot = (0.0, 0.0, 0.0)

    hold_frames = anim_cfg.get('hold_frames', 30)
    move_frames = anim_cfg.get('move_frames', 50)
    waypoints   = anim_cfg.get('camera_waypoints', [cam_cfg])

    def insert_all(frame, loc, rot, fl, interp):
        with kf_interp(interp):
            cam_obj.location       = loc
            cam_obj.rotation_euler = rot
            cam_data.lens          = fl
            cam_obj.keyframe_insert(data_path="location",       frame=frame)
            cam_obj.keyframe_insert(data_path="rotation_euler", frame=frame)
            cam_data.keyframe_insert(data_path="lens",          frame=frame)

    # Frame 1: hold start state (CONSTANT so nothing moves yet)
    insert_all(1, start_loc, start_rot, start_fl, 'CONSTANT')

    cur_frame = 1
    cur_loc, cur_rot, cur_fl = start_loc, start_rot, start_fl

    for wp in waypoints:
        wp_loc = Vector(wp['location'])
        wp_rot = wp['rotation_euler']
        wp_fl  = wp.get('focal_length', cur_fl)

        hold_frame = cur_frame + hold_frames
        move_frame = hold_frame + move_frames

        # Repeat current state at hold_frame with BEZIER so ease-in starts here
        insert_all(hold_frame, cur_loc, cur_rot, cur_fl, 'BEZIER')
        # Arrive at waypoint
        insert_all(move_frame, wp_loc, wp_rot, wp_fl, 'BEZIER')

        print(f"  Waypoint: frames {hold_frame}-{move_frame} → "
              f"loc={tuple(round(v,3) for v in wp_loc)}, fl={wp_fl}")

        cur_frame = move_frame
        cur_loc, cur_rot, cur_fl = wp_loc, wp_rot, wp_fl

    # ── Optional outro: BEZIER ease to saved camera target ──────────────────
    outro_frames = anim_cfg.get('outro_frames', 0)
    if outro_frames > 0:
        outro_end = cur_frame + outro_frames
        # Re-anchor the last waypoint so ease-in starts cleanly from here
        insert_all(cur_frame, cur_loc, cur_rot, cur_fl, 'BEZIER')
        outro_loc = Vector(cam_cfg['location'])
        outro_rot = cam_cfg['rotation_euler']
        outro_fl  = cam_cfg.get('focal_length', cur_fl)
        insert_all(outro_end, outro_loc, outro_rot, outro_fl, 'BEZIER')
        anim_cfg['total_frames'] = outro_end
        print(f"  Outro: frames {cur_frame}–{outro_end} → "
              f"loc={tuple(round(v,3) for v in outro_loc)}, fl={outro_fl}")

    print(f"  Analog zoom: overhead_h={overhead_h:.3f}, chip_h={chip_h:.3f}")
    return cam_obj, cur_frame


def animate_cut_cube_outro(end_frame, outro_frames):
    """
    Animates cut_cube.scale.z: held at 0 for the main animation, then BEZIER
    ease from 0 to its current (final) value over end_frame → end_frame+outro_frames.
    If the cube's origin is at its centre (Blender default), it will grow
    symmetrically; move the origin to the bottom face first for upward growth.
    """
    cube = bpy.data.objects.get("cut_cube")
    if cube is None:
        print("  cut_cube not found — skipping outro scale animation")
        return

    target_scale_z = cube.scale.z
    final_loc_z    = cube.location.z
    # Top face stays fixed; cube grows downward. For a size=2 cube, the top is
    # at location.z + scale.z, so when scale=0 the flat plane sits at the top.
    top_z     = final_loc_z + target_scale_z
    outro_end = end_frame + outro_frames

    # ── Visibility: hidden until outro starts ────────────────────────────────
    with kf_interp('CONSTANT'):
        cube.hide_viewport = True
        cube.hide_render   = True
        cube.keyframe_insert(data_path="hide_viewport", frame=1)
        cube.keyframe_insert(data_path="hide_render",   frame=1)
        cube.hide_viewport = False
        cube.hide_render   = False
        cube.keyframe_insert(data_path="hide_viewport", frame=end_frame)
        cube.keyframe_insert(data_path="hide_render",   frame=end_frame)

    # ── Scale + location: grow downward from top ─────────────────────────────
    with kf_interp('CONSTANT'):
        cube.scale.z    = 0.001   # non-zero so Boolean cutter is never degenerate
        cube.location.z = top_z
        cube.keyframe_insert(data_path="scale",    index=2, frame=1)
        cube.keyframe_insert(data_path="location", index=2, frame=1)

    with kf_interp('BEZIER'):
        cube.scale.z    = 0.001
        cube.location.z = top_z
        cube.keyframe_insert(data_path="scale",    index=2, frame=end_frame)
        cube.keyframe_insert(data_path="location", index=2, frame=end_frame)

        cube.scale.z    = target_scale_z
        cube.location.z = final_loc_z
        cube.keyframe_insert(data_path="scale",    index=2, frame=outro_end)
        cube.keyframe_insert(data_path="location", index=2, frame=outro_end)

    cube.scale.z    = target_scale_z
    cube.location.z = final_loc_z

    # ── Camera: Track To constraint active only during outro ─────────────────
    cam_obj = bpy.context.scene.camera
    if cam_obj:
        con = cam_obj.constraints.new(type='TRACK_TO')
        con.name       = "TrackCutCube"
        con.target     = cube
        con.track_axis = 'TRACK_NEGATIVE_Z'
        con.up_axis    = 'UP_Y'
        with kf_interp('CONSTANT'):
            con.influence = 0.0
            con.keyframe_insert(data_path="influence", frame=1)
            con.keyframe_insert(data_path="influence", frame=end_frame - 1)
            con.influence = 1.0
            con.keyframe_insert(data_path="influence", frame=end_frame)

    print(f"  cut_cube: hidden until {end_frame}, grows top-down to {outro_end}, camera tracks")


def setup_camera_flythrough(cam_cfg, anim_cfg, chip_bounds, chip_center_z):
    """Animated camera flying through the chip along one horizontal axis."""
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


# ─────────────────────────────────────────────
# Lighting
# ─────────────────────────────────────────────

def setup_lighting(chip_center_z, light_cfg):
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()

    bg_color = light_cfg.get('background_color', [0.05, 0.06, 0.10])
    r, g, b  = bg_color
    world_strength = light_cfg.get('world_strength', 1.5)

    out  = nt.nodes.new('ShaderNodeOutputWorld')
    bg   = nt.nodes.new('ShaderNodeBackground')
    bg.inputs['Color'].default_value    = (r, g, b, 1.0)
    bg.inputs['Strength'].default_value = world_strength
    nt.links.new(bg.outputs['Background'], out.inputs['Surface'])

    if world_strength == 0.0:
        # Show background_color to camera rays but contribute zero lighting
        lp     = nt.nodes.new('ShaderNodeLightPath')
        bg_cam = nt.nodes.new('ShaderNodeBackground')
        mix    = nt.nodes.new('ShaderNodeMixShader')
        bg_cam.inputs['Color'].default_value    = (r, g, b, 1.0)
        bg_cam.inputs['Strength'].default_value = 1.0
        nt.links.new(lp.outputs['Is Camera Ray'], mix.inputs['Fac'])
        nt.links.new(bg.outputs['Background'],     mix.inputs[1])
        nt.links.new(bg_cam.outputs['Background'], mix.inputs[2])
        nt.links.new(mix.outputs['Shader'],        out.inputs['Surface'])

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
# Animation helpers
# ─────────────────────────────────────────────

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
    """Fly-off / drop-back-in loop for layer_explode_loop."""
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
                # Bottom stays fixed: pivot correction offsets the downward shift of the pivot
                pivot_correction = -z_min_local * (via_scale - 1.0)
            else:
                # Top stays fixed: pivot correction offsets the upward shift of the pivot
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


# ─────────────────────────────────────────────
# Render settings
# ─────────────────────────────────────────────

def create_emissive_material(name, color, strength):
    """Principled BSDF with emission — used for FIB rect and beam."""
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        r, g, b = color
        bsdf.inputs['Base Color'].default_value = (r, g, b, 1.0)
        bsdf.inputs['Roughness'].default_value  = 1.0
        for key in ('Emission Color', 'Emission'):
            if key in bsdf.inputs:
                bsdf.inputs[key].default_value = (r, g, b, 1.0)
                break
        if 'Emission Strength' in bsdf.inputs:
            bsdf.inputs['Emission Strength'].default_value = strength
    return mat


def setup_camera_fib_cut(cfg, anim_cfg):
    """BEZIER zoom from camera_start (frame 1) to camera (zoom_end_frame), then holds."""
    cam_data = bpy.data.cameras.new("Camera")
    cam_obj  = bpy.data.objects.new("Camera", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj
    cam_data.dof.use_dof = False

    zoom_end  = anim_cfg.get('zoom_end_frame', 77)
    start_cfg = cfg.get('camera_start', cfg['camera'])
    end_cfg   = cfg['camera']

    def insert_cam(frame, c, interp):
        with kf_interp(interp):
            cam_obj.location       = c['location']
            cam_obj.rotation_euler = c['rotation_euler']
            cam_data.lens          = c['focal_length']
            cam_obj.keyframe_insert(data_path="location",       frame=frame)
            cam_obj.keyframe_insert(data_path="rotation_euler", frame=frame)
            cam_data.keyframe_insert(data_path="lens",          frame=frame)

    insert_cam(1,        start_cfg, 'BEZIER')
    insert_cam(zoom_end, end_cfg,   'BEZIER')
    print(f"  FIB camera: zoom frames 1→{zoom_end}, then holds")
    return cam_obj


def setup_fib_cut_animation(cfg, active_stack, chip_bounds, chip_top_z):
    anim_cfg     = cfg['animation']
    sio2_cfg     = cfg.get('sio2', {})
    fib_rect_cfg = cfg.get('fib_rect', {})
    fib_beam_cfg = cfg.get('fib_beam', {})

    sio2_start   = anim_cfg['sio2_start_frame']
    sio2_end     = anim_cfg['sio2_end_frame']
    beam_start   = anim_cfg['beam_start_frame']
    cut_start    = anim_cfg['cut_start_frame']
    cut_end      = anim_cfg['cut_end_frame']
    raster_lines = anim_cfg.get('raster_lines', 25)

    cut_cube = bpy.data.objects.get("cut_cube")
    if cut_cube is None:
        print("  WARNING: cut_cube not found — FIB animation incomplete")
        return

    # hide_render keeps it out of renders; hide_viewport must stay False or the
    # Boolean modifier stops evaluating. Keyframe hide_render so it can't be
    # overridden by collection-level visibility.
    cut_cube.hide_render   = True
    cut_cube.hide_viewport = False
    with kf_interp('CONSTANT'):
        cut_cube.keyframe_insert(data_path="hide_render", frame=1)

    # ── Boolean cuts on all visible layers ───────────────────────────────────
    add_boolean_cuts(active_stack, cut_cube)

    # ── Geometry references ───────────────────────────────────────────────────
    cc_loc        = cut_cube.location.copy()
    cc_scale      = cut_cube.scale.copy()
    x_min_cc      = cc_loc.x - cc_scale.x
    x_max_cc      = cc_loc.x + cc_scale.x
    y_min_cc      = cc_loc.y - cc_scale.y
    y_max_cc      = cc_loc.y + cc_scale.y
    top_z         = cc_loc.z + cc_scale.z   # final top face of cut_cube
    final_loc_z   = cc_loc.z               # final centre Z
    final_scale_z = cc_scale.z             # final scale.z

    # ── SiO2 slab (Z=0 to chip_top_z + cap, full chip footprint) ────────────
    # Covers from the substrate up to just above the met4 wires.
    sio2_cap      = sio2_cfg.get('cap_thickness', 0.017)
    sio2_top_z    = chip_top_z + sio2_cap   # surface the FIB beam scans
    x0, x1, y0, y1 = chip_bounds
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    w  = x1 - x0
    d  = y1 - y0
    h  = sio2_top_z                       # full height from Z=0

    sio2_final_sz = h / 2                 # final scale.z for a size=2 cube
    sio2_final_lz = sio2_final_sz         # centre when bottom is at Z=0

    bpy.ops.mesh.primitive_cube_add(size=2, location=(cx, cy, sio2_final_lz))
    sio2 = bpy.context.active_object
    sio2.name = "sio2"
    sio2.dimensions = (w, d, h)

    sio2_mat = make_material({
        'name':         'sio2',
        'color':        sio2_cfg.get('color',        [0.02, 0.05, 0.10]),
        'metallic':     0.0,
        'roughness':    sio2_cfg.get('roughness',    0.05),
        'transmission': sio2_cfg.get('transmission', 0.80),
    })
    sio2_bsdf = sio2_mat.node_tree.nodes.get('Principled BSDF')
    if sio2_bsdf and 'IOR' in sio2_bsdf.inputs:
        sio2_bsdf.inputs['IOR'].default_value = sio2_cfg.get('ior', 1.46)
    sio2.data.materials.append(sio2_mat)

    # Boolean cut on SiO2 — EXACT is more reliable for clean primitive geometry
    sio2_mod           = sio2.modifiers.new("CutCube", type='BOOLEAN')
    sio2_mod.operation = 'DIFFERENCE'
    sio2_mod.object    = cut_cube
    sio2_mod.solver    = 'EXACT'

    # Grows upward from Z=0 (substrate); bottom stays fixed at 0, so location.z = scale.z
    with kf_interp('CONSTANT'):
        sio2.hide_viewport = True
        sio2.hide_render   = True
        sio2.keyframe_insert(data_path="hide_viewport", frame=1)
        sio2.keyframe_insert(data_path="hide_render",   frame=1)
        sio2.scale.z    = 0.001
        sio2.location.z = 0.001
        sio2.keyframe_insert(data_path="scale",    index=2, frame=1)
        sio2.keyframe_insert(data_path="location", index=2, frame=1)
        sio2.hide_viewport = False
        sio2.hide_render   = False
        sio2.keyframe_insert(data_path="hide_viewport", frame=sio2_start)
        sio2.keyframe_insert(data_path="hide_render",   frame=sio2_start)

    with kf_interp('BEZIER'):
        sio2.scale.z    = 0.001
        sio2.location.z = 0.001
        sio2.keyframe_insert(data_path="scale",    index=2, frame=sio2_start)
        sio2.keyframe_insert(data_path="location", index=2, frame=sio2_start)
        sio2.scale.z    = sio2_final_sz
        sio2.location.z = sio2_final_lz
        sio2.keyframe_insert(data_path="scale",    index=2, frame=sio2_end)
        sio2.keyframe_insert(data_path="location", index=2, frame=sio2_end)

    sio2.scale.z    = sio2_final_sz
    sio2.location.z = sio2_final_lz

    # ── FIB rectangle (emissive plane marking the scan area) ─────────────────
    rect_cx = (x_min_cc + x_max_cc) / 2
    rect_cy = (y_min_cc + y_max_cc) / 2
    bpy.ops.mesh.primitive_plane_add(size=2,
                                     location=(rect_cx, rect_cy, sio2_top_z + 0.005))
    fib_rect = bpy.context.active_object
    fib_rect.name = "fib_rect"
    fib_rect.dimensions = (x_max_cc - x_min_cc, y_max_cc - y_min_cc, 0)
    fib_rect.data.materials.append(create_emissive_material(
        "fib_rect",
        fib_rect_cfg.get('color', [1.0, 0.6, 0.1]),
        fib_rect_cfg.get('emission_strength', 5.0),
    ))

    with kf_interp('CONSTANT'):
        fib_rect.hide_viewport = True
        fib_rect.hide_render   = True
        fib_rect.keyframe_insert(data_path="hide_viewport", frame=1)
        fib_rect.keyframe_insert(data_path="hide_render",   frame=1)
        fib_rect.hide_viewport = False
        fib_rect.hide_render   = False
        fib_rect.keyframe_insert(data_path="hide_viewport", frame=beam_start)
        fib_rect.keyframe_insert(data_path="hide_render",   frame=beam_start)
        # Disappears when the cut starts so it doesn't cover the trench
        fib_rect.hide_viewport = True
        fib_rect.hide_render   = True
        fib_rect.keyframe_insert(data_path="hide_viewport", frame=cut_start)
        fib_rect.keyframe_insert(data_path="hide_render",   frame=cut_start)

    # ── FIB beam cylinder ────────────────────────────────────────────────────
    b_radius = fib_beam_cfg.get('radius', 0.04)
    b_height = fib_beam_cfg.get('height', 0.3)
    beam_z   = sio2_top_z + b_height / 2   # base sits on SiO2 surface

    bpy.ops.mesh.primitive_cylinder_add(
        radius=b_radius, depth=b_height,
        location=(x_min_cc, y_min_cc, beam_z),
    )
    beam = bpy.context.active_object
    beam.name = "fib_beam"
    beam.data.materials.append(create_emissive_material(
        "fib_beam",
        fib_beam_cfg.get('color', [1.0, 0.85, 0.3]),
        fib_beam_cfg.get('emission_strength', 30.0),
    ))

    # Raster scan: raster_passes full sweeps of the area, each pass covers
    # raster_lines Y positions. Cap total so each sweep gets ≥1 frame.
    raster_passes = anim_cfg.get('raster_passes', 1)
    total_sweeps  = min(raster_lines * raster_passes, cut_end - beam_start)
    fpline        = (cut_end - beam_start) / total_sweeps
    beam.animation_data_clear()
    with kf_interp('LINEAR'):
        for i in range(total_sweeps):
            f0    = int(beam_start + i * fpline)
            f1    = int(beam_start + (i + 1) * fpline)
            y_idx = i % raster_lines
            y     = y_min_cc + y_idx * (y_max_cc - y_min_cc) / max(raster_lines - 1, 1)
            x0    = x_min_cc if i % 2 == 0 else x_max_cc
            x1    = x_max_cc if i % 2 == 0 else x_min_cc
            beam.location = (x0, y, beam_z)
            beam.keyframe_insert(data_path="location", frame=f0)
            beam.location = (x1, y, beam_z)
            beam.keyframe_insert(data_path="location", frame=f1)

    with kf_interp('CONSTANT'):
        beam.hide_viewport = True
        beam.hide_render   = True
        beam.keyframe_insert(data_path="hide_viewport", frame=1)
        beam.keyframe_insert(data_path="hide_render",   frame=1)
        beam.hide_viewport = False
        beam.hide_render   = False
        beam.keyframe_insert(data_path="hide_viewport", frame=beam_start)
        beam.keyframe_insert(data_path="hide_render",   frame=beam_start)
        beam.hide_viewport = True
        beam.hide_render   = True
        beam.keyframe_insert(data_path="hide_viewport", frame=cut_end + 1)
        beam.keyframe_insert(data_path="hide_render",   frame=cut_end + 1)

    # ── cut_cube: starts as thin slice at SiO2 surface, grows downward ─────────
    # Cut extends cut_above above the SiO2 surface so it cleanly removes the top
    # face (avoids z-fighting when top is exactly co-planar with SiO2 surface).
    cut_depth    = anim_cfg.get('cut_depth', 0.02)
    cut_above    = anim_cfg.get('cut_above', 0.01)
    total_cut_h  = cut_above + sio2_cap + cut_depth
    final_cut_sz = total_cut_h / 2
    cut_top_z    = sio2_top_z + cut_above        # top of cut, always above SiO2
    final_cut_lz = cut_top_z - final_cut_sz
    start_cut_lz = cut_top_z - 0.001            # nearly flat, top above SiO2

    with kf_interp('CONSTANT'):
        cut_cube.scale.z    = 0.001
        cut_cube.location.z = start_cut_lz
        cut_cube.keyframe_insert(data_path="scale",    index=2, frame=1)
        cut_cube.keyframe_insert(data_path="location", index=2, frame=1)

    with kf_interp('BEZIER'):
        cut_cube.scale.z    = 0.001
        cut_cube.location.z = start_cut_lz
        cut_cube.keyframe_insert(data_path="scale",    index=2, frame=cut_start)
        cut_cube.keyframe_insert(data_path="location", index=2, frame=cut_start)
        cut_cube.scale.z    = final_cut_sz
        cut_cube.location.z = final_cut_lz
        cut_cube.keyframe_insert(data_path="scale",    index=2, frame=cut_end)
        cut_cube.keyframe_insert(data_path="location", index=2, frame=cut_end)

    cut_cube.scale.z    = final_cut_sz
    cut_cube.location.z = final_cut_lz

    print(f"  FIB: sio2 {sio2_start}→{sio2_end}, rect+beam from {beam_start}, "
          f"cut {cut_start}→{cut_end}, {raster_lines} raster lines")


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

    scene.render.use_motion_blur      = motion_blur
    scene.render.motion_blur_shutter  = 0.3
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = "/tmp/chip_render/frame_"


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    cfg       = load_config(CONFIG_PATH)
    layers    = cfg['layers']
    anim_cfg  = cfg['animation']
    anim_type = anim_cfg.get('type', 'layer_explode_loop')
    cam_cfg   = cfg['camera']
    light_cfg = cfg.get('lighting', {})
    folder          = cfg['stl_folder']
    scale           = cfg['scale']
    layer_thickness = cfg['layer_thickness']

    print(f"\n=== Building chip scene ({anim_type}) ===")
    clear_scene()
    recreate_cut_cube(cfg)

    # ── 1. Import & position layers ──────────────────────────
    layer_stack = []
    current_z = 0.0
    for lc in layers:
        filepath = os.path.join(folder, lc['filename'])
        if not os.path.exists(filepath):
            print(f"  SKIP (not found): {lc['filename']}")
            continue

        print(f"  Importing {lc['name']} …")
        obj = import_stl(filepath)
        if obj is None:
            continue

        obj.name = lc['name']
        thickness_mult = lc.get('thickness_multiplier', 1.0)
        obj.scale = (scale, scale, scale * layer_thickness * thickness_mult)
        apply_scale(obj)

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
        print(f"    → placed at Z = {obj.location.z:.3f}, height = {height:.4f}")

    valid_objects = [e['obj'] for e in layer_stack]
    if not valid_objects:
        print("ERROR: No objects imported — check STL folder path in config.")
        return

    # ── 2. Centre in XY ──────────────────────────────────────
    cx, cy = bbox_center_xy(valid_objects[0])
    print(f"\nChip XY centre (world): ({cx:.3f}, {cy:.3f})")
    for entry in layer_stack:
        entry['obj'].location.x -= cx
        entry['obj'].location.y -= cy

    # ── 3. Chip geometry ──────────────────────────────────────
    bpy.context.view_layer.update()   # flush transforms after XY centering
    chip_center_z = current_z / 2
    chip_top_z    = current_z
    chip_bounds   = compute_chip_bounds(valid_objects)
    print(f"Chip bounds:  X=[{chip_bounds[0]:.3f}, {chip_bounds[1]:.3f}]  "
          f"Y=[{chip_bounds[2]:.3f}, {chip_bounds[3]:.3f}]  centre_Z={chip_center_z:.3f}")

    # ── 4. Focus Empty ────────────────────────────────────────
    bpy.ops.object.empty_add(type='SPHERE', location=(0, 0, chip_center_z), scale=(0.1, 0.1, 0.1))
    focus_empty = bpy.context.active_object
    focus_empty.name        = "FocusPoint"
    focus_empty.hide_render = True

    # ── 5. Visibility ─────────────────────────────────────────
    active_stack = []
    for entry in layer_stack:
        hidden = entry['lc'].get('hidden', False)
        entry['obj'].hide_render   = hidden
        entry['obj'].hide_viewport = hidden
        if not hidden:
            active_stack.append(entry)

    # ── 6. Camera ─────────────────────────────────────────────
    if anim_type == 'layer_explode_loop':
        cam_obj = setup_camera(cam_cfg, focus_empty)
    elif anim_type == 'camera_flythrough':
        cam_obj = setup_camera_flythrough(cam_cfg, anim_cfg, chip_bounds, chip_center_z)
    elif anim_type == 'drift_loop':
        cam_obj = setup_camera_drift_loop(cam_cfg, anim_cfg)
    elif anim_type == 'analog_zoom':
        cam_obj, _outro_start = setup_camera_analog_zoom(cam_cfg, anim_cfg, chip_bounds, chip_center_z)
    elif anim_type == 'fib_cut':
        cam_obj = setup_camera_fib_cut(cfg, anim_cfg)
    else:
        raise ValueError(f"Unknown animation type: '{anim_type}'")

    # ── 7. Lighting ───────────────────────────────────────────
    setup_lighting(chip_center_z, light_cfg)
    print(f"Camera at {tuple(round(v, 2) for v in cam_obj.location)}")

    # ── 8. Rotation empty (layer_explode_loop only) ──────────
    if anim_type == 'layer_explode_loop':
        bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0, 0, 0))
        rot_empty = bpy.context.active_object
        rot_empty.name = "ChipRotation"
        for entry in active_stack:
            obj = entry['obj']
            obj.parent = rot_empty
            obj.matrix_parent_inverse = rot_empty.matrix_world.inverted()
        set_linear_rotation(rot_empty, 1, anim_cfg['total_frames'], anim_cfg['rotation_degrees'])

    # ── 9. Layer animations ───────────────────────────────────
    if anim_type == 'layer_explode_loop':
        n_active = len(active_stack)
        for layer_idx, entry in enumerate(active_stack):
            final_z = entry['obj'].location.z
            animate_layer(entry['obj'], final_z, layer_idx, n_active, anim_cfg)
            print(f"  {entry['lc']['name']:12s} layer_idx={layer_idx}, Z={final_z:.3f}")
    elif anim_type == 'camera_flythrough':
        animate_flythrough_layers(active_stack, anim_cfg)
        print(f"  Flythrough drift set for {len(active_stack)} layers")
    elif anim_type == 'drift_loop':
        animate_drift_loop_layers(active_stack, anim_cfg)
        print(f"  Drift loop set for {len(active_stack)} layers")
    elif anim_type == 'analog_zoom':
        print(f"  analog_zoom: layers static, camera animates")
        cube = bpy.data.objects.get("cut_cube")
        if cube:
            add_boolean_cuts(active_stack, cube)
        if anim_cfg.get('outro_frames', 0) > 0:
            animate_cut_cube_outro(_outro_start, anim_cfg['outro_frames'])
    elif anim_type == 'fib_cut':
        setup_fib_cut_animation(cfg, active_stack, chip_bounds, chip_top_z)

    # ── 10. Render settings ───────────────────────────────────
    setup_render(bpy.context.scene, anim_cfg, cfg.get('motion_blur', False), cfg)
    bpy.context.scene.frame_set(1)

    # ── 11. Save .blend ───────────────────────────────────────
    blend_path = os.path.join(os.path.dirname(CONFIG_PATH), "chip_scene.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"\nSaved: {blend_path}")
    print("\n=== Done ===")
    print("  • Edit layer_config.json then run update_scene.py for fast iteration.")
    print("  • Press Space to preview the animation.")


main()
