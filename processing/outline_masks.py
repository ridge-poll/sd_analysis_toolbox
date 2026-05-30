#!/usr/bin/env python3
"""
outline_masks.py
----------------
Draws the perimeter of each binary mask onto a single source TIFF image,
using a distinct color per mask in the order they are provided.

Usage:
    python outline_masks.py <image.tiff> <mask1.tiff> [mask2.tiff ...] [--save path]

    --save path   Save the result to the given path. If omitted, the result
                  is displayed on screen and not saved.
"""

# ── PARAMETERS ────────────────────────────────────────────────────────────────
LINE_THICKNESS = 2        # Perimeter line width in pixels
# Colors cycle through this list in argument order (RGB tuples).
# Add/remove/reorder to taste.
COLORS = [
    (255,  50,  50),   # red
    (255, 165,   0),   # orange
    (255, 255,   0),   # yellow
    (  0, 220,  80),   # green
    (  0, 180, 255),   # cyan-blue
    (140,  60, 255),   # violet
    (255,  60, 200),   # pink
    (255, 255, 255),   # white
]
# ──────────────────────────────────────────────────────────────────────────────

import sys
import os
import numpy as np
from PIL import Image
from scipy.ndimage import binary_erosion


def load_rgb(path):
    img = Image.open(path)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    elif img.mode == "RGBA":
        img = img.convert("RGB")
    return np.array(img, dtype=np.uint8)


def load_mask(path):
    arr = np.array(Image.open(path))
    return arr > 0   # boolean


def get_perimeter(mask_bool, thickness=1):
    """Return a boolean array that is True only on the inner perimeter pixels."""
    struct = np.ones((3, 3), dtype=bool)
    eroded = mask_bool.copy()
    for _ in range(thickness):
        eroded = binary_erosion(eroded, structure=struct, border_value=0)
    return mask_bool & ~eroded


def main():
    args = sys.argv[1:]

    # Parse optional --save flag
    save_path = None
    if "--save" in args:
        idx = args.index("--save")
        if idx + 1 >= len(args):
            print("Error: --save requires a path argument")
            sys.exit(1)
        save_path = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if len(args) < 2:
        print("Usage: python outline_masks.py <image.tiff> <mask1.tiff> [mask2.tiff ...] [--save path]")
        sys.exit(1)

    image_path = args[0]
    mask_paths = args[1:]

    canvas = load_rgb(image_path)

    for i, mask_path in enumerate(mask_paths):
        color = COLORS[i % len(COLORS)]
        mask  = load_mask(mask_path)

        if mask.shape[:2] != canvas.shape[:2]:
            print(f"  WARNING: mask {mask_path} shape {mask.shape[:2]} != "
                  f"image shape {canvas.shape[:2]} — skipping")
            continue

        perim = get_perimeter(mask, thickness=LINE_THICKNESS)
        canvas[perim] = color
        print(f"  Mask {i+1}: {os.path.basename(mask_path)}  color={color}")

    if save_path:
        Image.fromarray(canvas).save(save_path)
        print(f"\nSaved -> {save_path}")
    else:
        print("\nDisplaying result (no --save specified)...")
        Image.fromarray(canvas).show()


if __name__ == "__main__":
    main()