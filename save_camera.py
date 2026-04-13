"""
save_camera.py
Run this ONCE after positioning your camera in Blender.
It writes the camera's exact location and rotation into layer_config.json
so that update_scene.py will restore it exactly on every run.
"""

import bpy
import json

CONFIG_PATH = "/Users/mattvenn/blender/layer_config.json"

cam = bpy.context.scene.camera
if cam is None:
    print("ERROR: No active camera in scene.")
else:
    loc = list(cam.location)
    rot = list(cam.rotation_euler)

    with open(CONFIG_PATH) as f:
        cfg = json.load(f)

    cfg['camera']['location']       = [round(v, 6) for v in loc]
    cfg['camera']['rotation_euler'] = [round(v, 6) for v in rot]

    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)

    print(f"Camera saved to config:")
    print(f"  location:       {cfg['camera']['location']}")
    print(f"  rotation_euler: {cfg['camera']['rotation_euler']}")
    print("update_scene.py will now use this exact camera position.")
