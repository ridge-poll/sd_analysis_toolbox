"""
section_extraction.py
---------------------
Extract a time-range slice of ephys + TIFF data into a new output folder.

Usage:
    python section_extraction.py ephys_file tiff_folder output_folder \
        -start 1622 -end 1781 [-offset 30]

    -start / -end   : TIFF frame indices (inclusive) that define the window.
    -offset         : optional integer frame offset applied to the TIFF range
                      before mapping to ephys time (default 0).

Outputs inside <output_folder>:
    ephys_section.h5        – HDF5 file with the sliced analogScans + header
    tiffs/                  – copy of the selected TIFF frames
"""

import argparse
import os
import re
import shutil
import sys

import h5py
import numpy as np


# ── natural sort helper (same logic as the viewer) ────────────────────────────

def _natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def collect_tiffs(folder: str) -> list[str]:
    paths = [
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if f.lower().endswith((".tif", ".tiff"))
    ]
    paths.sort(key=lambda p: _natural_key(os.path.basename(p)))
    return paths


# ── ephys slicing ─────────────────────────────────────────────────────────────

def extract_ephys(src_path: str, dst_path: str,
                  t_start: float, t_end: float) -> None:
    """
    Copy the ephys HDF5, replacing every sweep's analogScans with the
    [t_start, t_end) time slice (seconds).  Header is copied verbatim.
    """
    with h5py.File(src_path, "r") as src, h5py.File(dst_path, "w") as dst:
        # ── copy header exactly ───────────────────────────────────────
        src.copy("header", dst, name="header")

        sample_rate = float(src["header"]["AcquisitionSampleRate"][0, 0])

        sweeps = sorted(k for k in src.keys() if k.startswith("sweep_"))

        cumulative_offset = 0  # running sample count across sweeps

        for sweep in sweeps:
            grp_src = src[sweep]
            n_samples_sweep = grp_src["analogScans"].shape[1]

            sweep_t_start = cumulative_offset / sample_rate
            sweep_t_end   = (cumulative_offset + n_samples_sweep) / sample_rate

            # local sample indices within this sweep
            local_start = max(0, int((t_start - sweep_t_start) * sample_rate))
            local_stop  = min(n_samples_sweep,
                               int((t_end   - sweep_t_start) * sample_rate))

            grp_dst = dst.require_group(sweep)

            for key in grp_src.keys():
                if key == "analogScans":
                    if local_stop > local_start:
                        chunk = grp_src["analogScans"][:, local_start:local_stop]
                    else:
                        # this sweep falls entirely outside the window
                        chunk = grp_src["analogScans"][:, 0:0]
                    grp_dst.create_dataset("analogScans", data=chunk,
                                           compression="gzip", compression_opts=4)
                else:
                    grp_src.copy(key, grp_dst, name=key)

            # copy sweep-level attributes
            for attr_key, attr_val in grp_src.attrs.items():
                grp_dst.attrs[attr_key] = attr_val

            cumulative_offset += n_samples_sweep


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract a TIFF-frame window of ephys + imaging data."
    )
    parser.add_argument("ephys_file",   help="Path to WaveSurfer HDF5 file")
    parser.add_argument("tiff_folder",  help="Folder containing source TIFFs")
    parser.add_argument("output_folder", help="Destination folder (created if absent)")
    parser.add_argument("-start",  type=int, required=True,
                        help="First TIFF frame index to keep (0-based, inclusive)")
    parser.add_argument("-end",    type=int, required=True,
                        help="Last  TIFF frame index to keep (0-based, inclusive)")
    parser.add_argument("-offset", type=int, default=0,
                        help="Frame offset added before mapping to ephys time (default 0)")
    args = parser.parse_args()

    # ── validate inputs ───────────────────────────────────────────────
    if not os.path.isfile(args.ephys_file):
        sys.exit(f"ERROR: ephys file not found: {args.ephys_file}")
    if not os.path.isdir(args.tiff_folder):
        sys.exit(f"ERROR: tiff folder not found: {args.tiff_folder}")
    if args.start > args.end:
        sys.exit(f"ERROR: -start ({args.start}) must be <= -end ({args.end})")

    # ── collect TIFFs ─────────────────────────────────────────────────
    all_tiffs = collect_tiffs(args.tiff_folder)
    if not all_tiffs:
        sys.exit(f"ERROR: no .tif/.tiff files found in {args.tiff_folder}")

    n_frames = len(all_tiffs)

    # -start/-end are in ephys-time seconds.
    # tiff_frame = ephys_second - offset  (tiffs started `offset` seconds late)
    tiff_start = args.start - args.offset
    tiff_end   = args.end   - args.offset  # inclusive

    if tiff_start < 0 or tiff_end >= n_frames:
        sys.exit(
            f"ERROR: TIFF frame range [{tiff_start}, {tiff_end}] out of bounds "
            f"(folder has {n_frames} frames, indices 0–{n_frames - 1})"
        )

    frame_start = tiff_start
    frame_end   = tiff_end

    selected_tiffs = all_tiffs[frame_start : frame_end + 1]
    print(f"Selected {len(selected_tiffs)} TIFFs "
          f"(frames {frame_start}–{frame_end})")

    # ── derive ephys time window ───────────────────────────────────────
    # Frame rate is always 1 Hz, so frame index == seconds.
    # TIFFs started `offset` seconds into the ephys recording, so:
    #   ephys_second = tiff_frame + offset
    t_start = float(frame_start + args.offset)
    t_end   = float(frame_end   + args.offset + 1)  # +1 to include last frame fully

    print(f"Ephys window   : {t_start:.1f} s  →  {t_end:.1f} s")

    # ── create output structure ───────────────────────────────────────
    os.makedirs(args.output_folder, exist_ok=True)
    tiff_out_dir = os.path.join(args.output_folder, "tiffs")
    os.makedirs(tiff_out_dir, exist_ok=True)

    # ── copy TIFFs ────────────────────────────────────────────────────
    print(f"\nCopying TIFFs → {tiff_out_dir}")
    for i, src_path in enumerate(selected_tiffs):
        dst_path = os.path.join(tiff_out_dir, os.path.basename(src_path))
        shutil.copy2(src_path, dst_path)
        if (i + 1) % 50 == 0 or (i + 1) == len(selected_tiffs):
            print(f"  {i + 1}/{len(selected_tiffs)} copied", end="\r")
    print()

    # ── slice ephys ───────────────────────────────────────────────────
    ephys_out = os.path.join(args.output_folder, "ephys_section.h5")
    print(f"Slicing ephys  → {ephys_out}")
    extract_ephys(args.ephys_file, ephys_out, t_start, t_end)

    print("\nDone.")
    print(f"  {len(selected_tiffs)} TIFFs  →  {tiff_out_dir}")
    print(f"  Ephys slice     →  {ephys_out}")


if __name__ == "__main__":
    main()