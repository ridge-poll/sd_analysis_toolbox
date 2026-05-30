#!/usr/bin/env python3
"""
overlay_masks.py
----------------
Overlays a faded (brightened) version of binary masks onto their corresponding
source TIFF frames.

The mask folder uses a sequential index (000000, 000001, …) while the source
TIFF folder uses a raw frame number (e.g. 10868, 10869, …).  The lowest frame
number in the source folder is treated as index 0.

  mask index 0  <->  lowest source frame number (e.g. 10868)
  mask index 1  <->  next source frame number   (e.g. 10869)
  …

Usage:
    python overlay_masks.py <mask_folder> <source_tiff_folder> <output_folder>

Tuneable parameters (edit below):
"""

# ── PARAMETERS ────────────────────────────────────────────────────────────────
MASK_ALPHA = 0.3        # 0.0 = invisible overlay, 1.0 = fully opaque overlay
OVERLAY_COLOR = (255, 255, 255)  # RGB tint applied to the mask region
# ──────────────────────────────────────────────────────────────────────────────

import sys
import os
import re
import numpy as np
from PIL import Image


def extract_leading(filename):
    """For mask files: 000223_event_mask.tiff -> 223"""
    m = re.match(r"^(\d+)", os.path.basename(filename))
    return int(m.group(1)) if m else None


def extract_trailing(filename):
    """For source tiffs: 012726(Slice3)10868.tif -> 10868"""
    m = re.search(r"(\d+)\.[^.]+$", os.path.basename(filename))
    return int(m.group(1)) if m else None


def collect_files(folder, extractor):
    files = {}
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith((".tiff", ".tif")):
            idx = extractor(name)
            if idx is not None:
                files[idx] = os.path.join(folder, name)
    return files


def to_rgb(arr):
    """Convert a grayscale or palette image array to uint8 RGB."""
    img = Image.fromarray(arr)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    elif img.mode == "RGBA":
        img = img.convert("RGB")
    return np.array(img, dtype=np.uint8)


def overlay_masks(mask_folder, source_folder, output_folder):
    os.makedirs(output_folder, exist_ok=True)

    mask_files   = collect_files(mask_folder,   extract_leading)   # {mask_idx: path}
    source_files = collect_files(source_folder, extract_trailing)  # {raw_frame_num: path}

    if not source_files:
        print("No source TIFFs found.")
        sys.exit(1)
    if not mask_files:
        print("No mask TIFFs found.")
        sys.exit(1)

    # Map: mask index 0 == smallest raw frame number
    source_sorted = sorted(source_files.keys())   # ascending raw frame numbers
    frame0 = source_sorted[0]                      # raw number that equals mask index 0

    print(f"Source frames : {len(source_files)}  (first raw number = {frame0})")
    print(f"Mask frames   : {len(mask_files)}")
    print(f"Overlay alpha : {MASK_ALPHA}")
    print(f"Overlay colour: {OVERLAY_COLOR}")

    written = 0
    skipped = 0

    for mask_idx, mask_path in sorted(mask_files.items()):
        raw_num = frame0 + mask_idx
        if raw_num not in source_files:
            print(f"  SKIP  mask {mask_idx:06d} -> raw frame {raw_num} not found in source folder")
            skipped += 1
            continue

        src_path = source_files[raw_num]
        src_arr  = np.array(Image.open(src_path))
        msk_arr  = np.array(Image.open(mask_path))

        # Normalise mask to a boolean map
        mask_bool = msk_arr > 0

        # Work in float32 for blending
        src_rgb = to_rgb(src_arr).astype(np.float32)

        # Build colour overlay
        overlay = np.zeros_like(src_rgb, dtype=np.float32)
        overlay[mask_bool] = OVERLAY_COLOR  # broadcast RGB tuple

        # Alpha blend: result = src + alpha * (overlay - src)  where mask is True
        blended = src_rgb.copy()
        blended[mask_bool] = (
            src_rgb[mask_bool] * (1.0 - MASK_ALPHA)
            + overlay[mask_bool] * MASK_ALPHA
        )
        blended = np.clip(blended, 0, 255).astype(np.uint8)

        out_name = os.path.basename(src_path)
        Image.fromarray(blended).save(os.path.join(output_folder, out_name))
        print(f"  Overlay mask {mask_idx:06d} -> {out_name}")
        written += 1

    print(f"\nDone. {written} overlaid, {skipped} skipped -> {output_folder}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python overlay_masks.py <mask_folder> <source_tiff_folder> <output_folder>")
        sys.exit(1)

    overlay_masks(sys.argv[1], sys.argv[2], sys.argv[3])