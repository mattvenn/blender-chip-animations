"""
save_camera.py
Run after positioning your camera and/or tweaking lights/materials in Blender.
Saves camera transform, focal length, light energies, and object materials to
layer_config.json so that build_scene.py restores them exactly on every run.

For fib_cut scenes with two camera positions:
  Set SAVE_MODE = 'camera'       to save the zoomed-in hold position  (default)
  Set SAVE_MODE = 'camera_start' to save the initial wide-shot position
"""

import bpy
import json
import os

# ── Set this before running ───────────────────────────────────────────────────
# 'camera'       → saves to cfg['camera']       (zoom-in / hold position)
# 'camera_start' → saves to cfg['camera_start'] (initial wide-shot position)
SAVE_MODE = 'camera'

# Derive config path from the open .blend file's location.
if not bpy.data.filepath:
    raise RuntimeError(
        "No .blend file is open/saved. Open your animation's chip_scene.blend first."
    )
CONFIG_PATH = os.path.join(os.path.dirname(bpy.data.filepath), "layer_config.json")

with open(CONFIG_PATH) as f:
    cfg = json.load(f)

# ── Camera ────────────────────────────────────────────────────────────────────
cam = bpy.context.scene.camera
if cam is None:
    print("ERROR: No active camera in scene.")
else:
    cam.animation_data_clear()
    print(f"Camera animation cleared — saving to '{SAVE_MODE}'.")

    cam_entry = {
        'location':       [round(v, 6) for v in cam.location],
        'rotation_euler': [round(v, 6) for v in cam.rotation_euler],
        'focal_length':   round(cam.data.lens, 2),
    }
    cfg[SAVE_MODE] = cam_entry
    print(f"Camera saved → [{SAVE_MODE}]:  "
          f"location={cam_entry['location']}  "
          f"rotation={cam_entry['rotation_euler']}  "
          f"focal_length={cam_entry['focal_length']}")

# ── Lights ────────────────────────────────────────────────────────────────────
light_map = {
    'KeyLight':  'key_energy',
    'FillLight': 'fill_energy',
    'RimLight':  'rim_energy',
}

if 'lighting' not in cfg:
    cfg['lighting'] = {}

for obj_name, cfg_key in light_map.items():
    obj = bpy.data.objects.get(obj_name)
    if obj and obj.type == 'LIGHT':
        cfg['lighting'][cfg_key] = round(obj.data.energy, 2)
        loc_key = cfg_key.replace('_energy', '_location')
        rot_key = cfg_key.replace('_energy', '_rotation')
        cfg['lighting'][loc_key] = [round(v, 6) for v in obj.location]
        cfg['lighting'][rot_key] = [round(v, 6) for v in obj.rotation_euler]
        print(f"Light saved:   {obj_name} energy={cfg['lighting'][cfg_key]}  "
              f"loc={cfg['lighting'][loc_key]}")
    else:
        print(f"WARNING: light '{obj_name}' not found in scene — skipped.")

# ── World strength ────────────────────────────────────────────────────────────
world = bpy.context.scene.world
if world and world.use_nodes:
    bg = world.node_tree.nodes.get('Background')
    if bg:
        cfg['lighting']['world_strength'] = round(bg.inputs['Strength'].default_value, 4)
        print(f"World saved:   strength={cfg['lighting']['world_strength']}")

# ── cut_cube ──────────────────────────────────────────────────────────────────
cube = bpy.data.objects.get("cut_cube")
if cube:
    if cube.animation_data:
        cube.animation_data_clear()
        print("cut_cube animation cleared — reading base position.")
    mat_data = {}
    if cube.data.materials and cube.data.materials[0]:
        mat = cube.data.materials[0]
        if mat.use_nodes:
            bsdf = mat.node_tree.nodes.get('Principled BSDF')
            if bsdf:
                r, g, b, _ = bsdf.inputs['Base Color'].default_value
                mat_data = {
                    'color':        [round(r, 4), round(g, 4), round(b, 4)],
                    'metallic':     round(bsdf.inputs['Metallic'].default_value, 4),
                    'roughness':    round(bsdf.inputs['Roughness'].default_value, 4),
                    'transmission': round(
                        next((bsdf.inputs[k].default_value
                              for k in ('Transmission Weight', 'Transmission')
                              if k in bsdf.inputs), 0.0), 4),
                }
    cfg['cut_cube'] = {
        'location':       [round(v, 6) for v in cube.location],
        'rotation_euler': [round(v, 6) for v in cube.rotation_euler],
        'dimensions':     [round(v, 6) for v in cube.dimensions],
        'material':       mat_data,
    }
    print(f"cut_cube saved: loc={cfg['cut_cube']['location']}  "
          f"dimensions={cfg['cut_cube']['dimensions']}  material={mat_data}")
else:
    print("WARNING: 'cut_cube' not found in scene — skipped.")

# ── sio2 material ─────────────────────────────────────────────────────────────
sio2_obj = bpy.data.objects.get("sio2")
if sio2_obj and sio2_obj.data.materials:
    mat = sio2_obj.data.materials[0]
    if mat and mat.use_nodes:
        bsdf = mat.node_tree.nodes.get('Principled BSDF')
        if bsdf:
            r, g, b, _ = bsdf.inputs['Base Color'].default_value
            if 'sio2' not in cfg:
                cfg['sio2'] = {}
            cfg['sio2']['color']        = [round(r, 4), round(g, 4), round(b, 4)]
            cfg['sio2']['roughness']    = round(bsdf.inputs['Roughness'].default_value, 4)
            cfg['sio2']['transmission'] = round(
                next((bsdf.inputs[k].default_value
                      for k in ('Transmission Weight', 'Transmission')
                      if k in bsdf.inputs), 0.0), 4)
            if 'IOR' in bsdf.inputs:
                cfg['sio2']['ior'] = round(bsdf.inputs['IOR'].default_value, 4)
            print(f"sio2 saved:    {cfg['sio2']}")
else:
    print("sio2 not found in scene — skipped.")

# ── fib_rect material ─────────────────────────────────────────────────────────
fib_rect_obj = bpy.data.objects.get("fib_rect")
if fib_rect_obj and fib_rect_obj.data.materials:
    mat = fib_rect_obj.data.materials[0]
    if mat and mat.use_nodes:
        bsdf = mat.node_tree.nodes.get('Principled BSDF')
        if bsdf:
            if 'fib_rect' not in cfg:
                cfg['fib_rect'] = {}
            for key in ('Emission Color', 'Emission'):
                if key in bsdf.inputs:
                    r, g, b, _ = bsdf.inputs[key].default_value
                    cfg['fib_rect']['color'] = [round(r, 4), round(g, 4), round(b, 4)]
                    break
            if 'Emission Strength' in bsdf.inputs:
                cfg['fib_rect']['emission_strength'] = round(
                    bsdf.inputs['Emission Strength'].default_value, 2)
            print(f"fib_rect saved: {cfg['fib_rect']}")
else:
    print("fib_rect not found in scene — skipped.")

# ── fib_beam material ─────────────────────────────────────────────────────────
fib_beam_obj = bpy.data.objects.get("fib_beam")
if fib_beam_obj and fib_beam_obj.data.materials:
    mat = fib_beam_obj.data.materials[0]
    if mat and mat.use_nodes:
        bsdf = mat.node_tree.nodes.get('Principled BSDF')
        if bsdf:
            if 'fib_beam' not in cfg:
                cfg['fib_beam'] = {}
            for key in ('Emission Color', 'Emission'):
                if key in bsdf.inputs:
                    r, g, b, _ = bsdf.inputs[key].default_value
                    cfg['fib_beam']['color'] = [round(r, 4), round(g, 4), round(b, 4)]
                    break
            if 'Emission Strength' in bsdf.inputs:
                cfg['fib_beam']['emission_strength'] = round(
                    bsdf.inputs['Emission Strength'].default_value, 2)
            print(f"fib_beam saved: {cfg['fib_beam']}")
else:
    print("fib_beam not found in scene — skipped.")

with open(CONFIG_PATH, 'w') as f:
    json.dump(cfg, f, indent=2)

print("Done — layer_config.json updated.")
