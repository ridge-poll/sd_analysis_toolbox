#!/usr/bin/env python3
"""
master.py
---------
Runs both figures — the SD outline figure and the ephys trace figure —
from a single command.

Usage:
    python master.py <reference_tiff> <bitmask_folder> <h5_file> \
                     <start_time> <stop_time>                     \
                     <ts1> <ts2> ... <tsN>                        \
                     [--save-outline path] [--save-ephys path]

Arguments:
    reference_tiff    Single TIFF image to draw mask outlines onto.
    bitmask_folder    Folder of binary mask TIFFs named  NNNNNN_*.tiff
                      where NNNNNN is the frame/second index.
    h5_file           WaveSurfer HDF5 ephys recording.
    start_time        Left edge of ephys display window (seconds, float).
    stop_time         Right edge of ephys display window (seconds, float).
    ts1 ts2 ... tsN   Integer event timestamps (seconds). Each must be an
                      integer, else the script exits with an error message.
                      Each timestamp selects the mask file whose leading
                      number equals that timestamp (1 frame per second,
                      so timestamp k -> file 000k_*).

Optional flags (can appear anywhere after the positional args):
    --save-outline path    Where to save the outline figure.
                           If omitted the figure is displayed on screen.
    --save-ephys   path    Where to save the ephys figure.
                           If omitted the figure is displayed on screen.

Example:
    python master.py frame.tiff masks/ rec.h5 100 200 120 145 167 \
        --save-outline sd_outline.png --save-ephys sd_ephys.png
"""

import sys
import os
import glob

# ── helpers ───────────────────────────────────────────────────────────────────

def parse_args(argv):
    """
    Returns:
        reference_tiff  str
        bitmask_folder  str
        h5_file         str
        start_time      float
        stop_time       float
        timestamps      list[int]   sorted ascending
        save_outline    str | None
        save_ephys      str | None
    """
    args = list(argv)

    # --- pull named flags first so they don't interfere with positionals ------
    def pop_flag(flag):
        if flag in args:
            idx = args.index(flag)
            if idx + 1 >= len(args):
                print(f"Error: {flag} requires a path argument.")
                sys.exit(1)
            val = args[idx + 1]
            args[idx:idx + 2] = []
            return val
        return None

    save_outline = pop_flag("--save-outline")
    save_ephys   = pop_flag("--save-ephys")

    # --- positional arguments -------------------------------------------------
    if len(args) < 6:
        print(__doc__)
        sys.exit(1)

    reference_tiff = args[0]
    bitmask_folder = args[1]
    h5_file        = args[2]

    try:
        start_time = float(args[3])
        stop_time  = float(args[4])
    except ValueError:
        print("Error: start_time and stop_time must be numbers.")
        sys.exit(1)

    if stop_time <= start_time:
        print("Error: stop_time must be greater than start_time.")
        sys.exit(1)

    # --- timestamps (floats for ephys, rounded to int for mask lookup) --------
    timestamps = []
    for raw in args[5:]:
        try:
            timestamps.append(float(raw))
        except ValueError:
            print(f"Error: timestamp '{raw}' is not a valid number.")
            sys.exit(1)

    if not timestamps:
        print("Error: at least one timestamp is required.")
        sys.exit(1)

    timestamps_sorted = sorted(timestamps)          # sort for mask color order

    return (reference_tiff, bitmask_folder, h5_file,
            start_time, stop_time, timestamps_sorted,
            save_outline, save_ephys)


def find_mask_for_timestamp(bitmask_folder: str, ts: int) -> str:
    """
    Return the path of the mask file whose leading number equals ts.
    E.g. ts=223 -> '000223_event_mask.tiff'
    Raises FileNotFoundError if no match.
    """
    pattern = os.path.join(bitmask_folder, f"{ts:06d}_*.tif*")
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(
            f"No mask file found for timestamp {ts} "
            f"(looked for {pattern})")
    if len(matches) > 1:
        print(f"  Warning: multiple masks match timestamp {ts}; using {matches[0]}")
    return matches[0]


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    (reference_tiff, bitmask_folder, h5_file,
     start_time, stop_time, timestamps,
     save_outline, save_ephys) = parse_args(sys.argv[1:])

    # --- resolve mask files ---------------------------------------------------
    print(f"Timestamps (sorted): {timestamps}")
    mask_paths = []
    for ts in timestamps:
        try:
            p = find_mask_for_timestamp(bitmask_folder, round(ts))
            mask_paths.append(p)
            print(f"  ts={round(ts):06d}  ->  {os.path.basename(p)}")
        except FileNotFoundError as e:
            print(f"  WARNING: {e} — skipping this timestamp.")

    if not mask_paths:
        print("Error: no valid mask files found. Aborting.")
        sys.exit(1)

    # --- figure 1: SD outline -------------------------------------------------
    print("\n── Generating outline figure ──")
    from outline_masks import load_rgb, load_mask, get_perimeter, COLORS, LINE_THICKNESS
    from PIL import Image
    import numpy as np

    canvas = load_rgb(reference_tiff)
    for i, mask_path in enumerate(mask_paths):
        color = COLORS[i % len(COLORS)]
        mask  = load_mask(mask_path)
        if mask.shape[:2] != canvas.shape[:2]:
            print(f"  WARNING: mask shape mismatch for {mask_path} — skipping")
            continue
        perim = get_perimeter(mask, thickness=LINE_THICKNESS)
        canvas[perim] = color
        print(f"  Mask {i+1}: {os.path.basename(mask_path)}  color={color}")

    if save_outline:
        Image.fromarray(canvas).save(save_outline, format="PNG")
        print(f"Outline figure saved -> {save_outline}")
    else:
        print("Displaying outline figure...")
        Image.fromarray(canvas).show()

    # --- figure 2: ephys traces -----------------------------------------------
    print("\n── Generating ephys figure ──")
    from ephys_figure import plot_ephys_figure

    plot_ephys_figure(
        h5_path    = h5_file,
        start_time = start_time,
        stop_time  = stop_time,
        timestamps = timestamps,
        save_path  = save_ephys,
    )


if __name__ == "__main__":
    main()