#!/usr/bin/env python3
"""
test_config.py — Validate a layer_config.json without Blender.

Usage:
    python test_config.py animations/chip_layers/layer_config.json
    python test_config.py                          # checks all configs under animations/

Exits 0 if all checks pass, 1 if any fail.
"""

import json
import os
import sys

# ─────────────────────────────────────────────
# Required fields per animation type
# ─────────────────────────────────────────────

REQUIRED_TOP = ['stl_folder', 'scale', 'layer_thickness', 'animation', 'camera', 'layers']
REQUIRED_LAYER = ['name', 'filename', 'color']
REQUIRED_CAMERA_COMMON = ['focal_length']

ANIMATION_TYPES = {
    'layer_explode_loop': [
        'total_frames', 'fps', 'rotation_degrees',
        'fly_off_first_frame', 'stagger_frames', 'duration_frames',
        'gap_frames', 'drop_height', 'overshoot',
    ],
    'camera_flythrough': [
        'total_frames', 'fps',
        'flight_duration_frames', 'drift_start_frame', 'drift_end_frame', 'via_drift_scale',
    ],
    'drift_loop': [
        'total_frames', 'fps', 'zoom_distance',
    ],
}

# ─────────────────────────────────────────────
# Checker
# ─────────────────────────────────────────────

class Results:
    def __init__(self, path):
        self.path = path
        self.errors = []
        self.warnings = []

    def fail(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def check(self, condition, msg):
        if not condition:
            self.fail(msg)
        return condition

    def report(self):
        label = os.path.relpath(self.path)
        if not self.errors and not self.warnings:
            print(f"  OK  {label}")
            return True
        for w in self.warnings:
            print(f"  WARN  {label}: {w}")
        for e in self.errors:
            print(f"  FAIL  {label}: {e}")
        return not self.errors


def validate(path):
    r = Results(path)

    # Load JSON
    try:
        with open(path) as f:
            cfg = json.load(f)
    except Exception as e:
        r.fail(f"cannot load JSON: {e}")
        r.report()
        return False

    # Top-level keys
    for key in REQUIRED_TOP:
        r.check(key in cfg, f"missing top-level key '{key}'")

    if r.errors:
        r.report()
        return False

    # stl_folder
    stl_folder = cfg['stl_folder']
    if not r.check(os.path.isdir(stl_folder), f"stl_folder not found: {stl_folder}"):
        r.warn("STL file checks skipped (stl_folder missing)")

    # scale / layer_thickness
    r.check(isinstance(cfg['scale'], (int, float)) and cfg['scale'] > 0,
            "scale must be a positive number")
    r.check(isinstance(cfg['layer_thickness'], (int, float)) and cfg['layer_thickness'] > 0,
            "layer_thickness must be a positive number")

    # animation
    anim = cfg['animation']
    anim_type = anim.get('type', '')
    if r.check(anim_type in ANIMATION_TYPES,
               f"animation.type '{anim_type}' unknown; valid types: {list(ANIMATION_TYPES)}"):
        for key in ANIMATION_TYPES[anim_type]:
            r.check(key in anim, f"animation missing required key '{key}' for type '{anim_type}'")
        r.check(anim.get('total_frames', 0) > 0, "animation.total_frames must be > 0")
        r.check(anim.get('fps', 0) > 0, "animation.fps must be > 0")

    # camera
    cam = cfg['camera']
    for key in REQUIRED_CAMERA_COMMON:
        r.check(key in cam, f"camera missing required key '{key}'")
    r.check('location' in cam or 'distance' in cam or 'flight_axis' in cam,
            "camera needs 'location', 'distance', or 'flight_axis'")
    if 'location' in cam:
        r.check(len(cam['location']) == 3, "camera.location must have 3 components")
    if 'rotation_euler' in cam:
        r.check(len(cam['rotation_euler']) == 3, "camera.rotation_euler must have 3 components")

    # layers
    layers = cfg['layers']
    r.check(len(layers) > 0, "layers list is empty")

    stl_ok = os.path.isdir(stl_folder)
    visible_count = 0
    for i, layer in enumerate(layers):
        label = layer.get('name', f"[{i}]")
        for key in REQUIRED_LAYER:
            r.check(key in layer, f"layer '{label}' missing required key '{key}'")

        if 'color' in layer:
            color = layer['color']
            r.check(len(color) == 3, f"layer '{label}' color must have 3 components")
            r.check(all(isinstance(c, (int, float)) and 0.0 <= c <= 1.0 for c in color),
                    f"layer '{label}' color values must be floats in [0, 1]")

        hidden = layer.get('hidden', False)
        if not hidden:
            visible_count += 1
            if stl_ok and 'filename' in layer:
                filepath = os.path.join(stl_folder, layer['filename'])
                r.check(os.path.exists(filepath), f"STL not found: {filepath}")

    r.check(visible_count > 0, "no visible layers (all are hidden)")

    return r.report()


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def find_all_configs(root):
    configs = []
    for dirpath, dirnames, filenames in os.walk(root):
        if 'layer_config.json' in filenames:
            configs.append(os.path.join(dirpath, 'layer_config.json'))
    return sorted(configs)


def main():
    root = os.path.dirname(os.path.abspath(__file__))

    if len(sys.argv) > 1:
        paths = sys.argv[1:]
    else:
        animations_dir = os.path.join(root, 'animations')
        if os.path.isdir(animations_dir):
            paths = find_all_configs(animations_dir)
        else:
            paths = find_all_configs(root)

    if not paths:
        print("No layer_config.json files found.")
        sys.exit(1)

    print(f"Checking {len(paths)} config(s)...\n")
    all_ok = True
    for path in paths:
        if not validate(path):
            all_ok = False

    print()
    if all_ok:
        print(f"All {len(paths)} config(s) passed.")
        sys.exit(0)
    else:
        sys.exit(1)


main()
