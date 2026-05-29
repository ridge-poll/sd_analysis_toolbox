#!/usr/bin/env python3
"""
merge_masks.py
--------------
Merges two folders of binary mask TIFFs by summing (OR) overlapping frames.
Frames present in only one folder are copied as-is.

Usage:
    python merge_masks.py <folder_a> <folder_b> <output_folder>

Frame index is extracted from the numeric prefix of each filename, e.g.
    000223_event_mask.tiff  ->  index 223
"""

import sys
import os
import re
import shutil
import numpy as np
from PIL import Image


def extract_index(filename):
    """Return the leading integer from a filename, or None if not found."""
    m = re.match(r"^(\d+)", os.path.basename(filename))
    return int(m.group(1)) if m else None


def collect_files(folder):
    """Return a dict mapping frame_index -> full_path for all TIFFs in folder."""
    files = {}
    for name in os.listdir(folder):
        if name.lower().endswith((".tiff", ".tif")):
            idx = extract_index(name)
            if idx is not None:
                files[idx] = os.path.join(folder, name)
    return files


def merge_masks(path_a, path_b, output_path):
    os.makedirs(output_path, exist_ok=True)

    files_a = collect_files(path_a)
    files_b = collect_files(path_b)

    all_indices = set(files_a) | set(files_b)
    print(f"Folder A: {len(files_a)} frames")
    print(f"Folder B: {len(files_b)} frames")
    print(f"Total unique frames: {len(all_indices)}")

    for idx in sorted(all_indices):
        in_a = idx in files_a
        in_b = idx in files_b

        if in_a and in_b:
            # Sum / OR the two masks
            img_a = np.array(Image.open(files_a[idx]))
            img_b = np.array(Image.open(files_b[idx]))
            merged = np.clip(img_a.astype(np.uint16) + img_b.astype(np.uint16),
                             0, np.iinfo(img_a.dtype).max).astype(img_a.dtype)
            out_name = os.path.basename(files_a[idx])
            Image.fromarray(merged).save(os.path.join(output_path, out_name))
            print(f"  Merged  {out_name}")

        elif in_a:
            out_name = os.path.basename(files_a[idx])
            shutil.copy2(files_a[idx], os.path.join(output_path, out_name))
            print(f"  Copied A {out_name}")

        else:
            out_name = os.path.basename(files_b[idx])
            shutil.copy2(files_b[idx], os.path.join(output_path, out_name))
            print(f"  Copied B {out_name}")

    print(f"\nDone. {len(all_indices)} files written to: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python merge_masks.py <folder_a> <folder_b> <output_folder>")
        sys.exit(1)

    print("starting")
    merge_masks(sys.argv[1], sys.argv[2], sys.argv[3])