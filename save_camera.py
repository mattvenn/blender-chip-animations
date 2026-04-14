"""
save_camera.py
Run after positioning your camera and/or tweaking lights in Blender.
Saves camera transform, focal length, and light energies to layer_config.json
so that update_scene.py restores them exactly on every run.
"""

import bpy
import json
import os

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
    # Clear animation so keyframes don't override the saved position and so the
    # camera is free to move after this script runs. Run update_scene.py to
    # re-apply the loop animation once you are happy with the position.
    cam.animation_data_clear()
    print("Camera animation cleared — camera is now free to move.")

    cfg['camera']['location']       = [round(v, 6) for v in cam.location]
    cfg['camera']['rotation_euler'] = [round(v, 6) for v in cam.rotation_euler]
    cfg['camera']['focal_length']   = round(cam.data.lens, 2)
    print(f"Camera saved:  location={cfg['camera']['location']}  rotation={cfg['camera']['rotation_euler']}  focal_length={cfg['camera']['focal_length']}")

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
        print(f"Light saved:   {obj_name} energy={cfg['lighting'][cfg_key]}")
    else:
        print(f"WARNING: light '{obj_name}' not found in scene — skipped.")

# ── World strength ────────────────────────────────────────────────────────────
world = bpy.context.scene.world
if world and world.use_nodes:
    bg = world.node_tree.nodes.get('Background')
    if bg:
        cfg['lighting']['world_strength'] = round(bg.inputs['Strength'].default_value, 4)
        print(f"World saved:   strength={cfg['lighting']['world_strength']}")

with open(CONFIG_PATH, 'w') as f:
    json.dump(cfg, f, indent=2)

print("Done — layer_config.json updated.")
