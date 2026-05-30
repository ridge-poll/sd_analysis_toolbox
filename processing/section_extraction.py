"""
section_extraction.py
---------------------
Extract a time-range slice of ephys + TIFF data into a new output folder.

Usage:
    python section_extraction.py ephys_file tiff_folder output_folder \
        -start 2900 -end 3100 [-offset 30]

    -start / -end   : Ephys time in seconds (inclusive) defining the window.
    -offset         : Frames the TIFF recording started after ephys t=0.
                      tiff_frame = ephys_second - offset  (default 0).

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


# ── TIFF collection ───────────────────────────────────────────────────────────

def collect_tiffs(folder: str) -> dict:
    """
    Return a dict mapping embedded frame number -> full path for every
    .tif/.tiff in folder.  The frame number is the last integer in the
    filename, e.g. '012726(Slice3)02870.tif' -> 2870.
    """
    result = {}
    for f in os.listdir(folder):
        if f.lower().endswith((".tif", ".tiff")):
            nums = re.findall(r"\d+", f)
            if nums:
                frame_num = int(nums[-1])
                result[frame_num] = os.path.join(folder, f)
    return result


# ── ephys slicing ─────────────────────────────────────────────────────────────

def extract_ephys(src_path, dst_path, t_start, t_end):
    """
    Copy the ephys HDF5, replacing every sweep's analogScans with the
    [t_start, t_end) time slice (seconds).  Header is copied verbatim.
    """
    with h5py.File(src_path, "r") as src, h5py.File(dst_path, "w") as dst:
        src.copy("header", dst, name="header")

        sample_rate = float(src["header"]["AcquisitionSampleRate"][0, 0])
        sweeps = sorted(k for k in src.keys() if k.startswith("sweep_"))

        cumulative_offset = 0

        for sweep in sweeps:
            grp_src = src[sweep]
            n_samples_sweep = grp_src["analogScans"].shape[1]

            sweep_t_start = cumulative_offset / sample_rate

            local_start = max(0, int((t_start - sweep_t_start) * sample_rate))
            local_stop  = min(n_samples_sweep,
                               int((t_end   - sweep_t_start) * sample_rate))

            grp_dst = dst.require_group(sweep)

            for key in grp_src.keys():
                if key == "analogScans":
                    if local_stop > local_start:
                        chunk = grp_src["analogScans"][:, local_start:local_stop]
                    else:
                        chunk = grp_src["analogScans"][:, 0:0]
                    grp_dst.create_dataset("analogScans", data=chunk,
                                           compression="gzip", compression_opts=4)
                else:
                    grp_src.copy(key, grp_dst, name=key)

            for attr_key, attr_val in grp_src.attrs.items():
                grp_dst.attrs[attr_key] = attr_val

            cumulative_offset += n_samples_sweep


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract a time-window slice of ephys + TIFF imaging data."
    )
    parser.add_argument("ephys_file",    help="Path to WaveSurfer HDF5 file")
    parser.add_argument("tiff_folder",   help="Folder containing source TIFFs")
    parser.add_argument("output_folder", help="Destination folder (created if absent)")
    parser.add_argument("-start",  type=int, required=True,
                        help="Start of window in ephys seconds (inclusive)")
    parser.add_argument("-end",    type=int, required=True,
                        help="End of window in ephys seconds (inclusive)")
    parser.add_argument("-offset", type=int, default=0,
                        help="Frames the TIFF recording lags behind ephys t=0 (default 0)")
    args = parser.parse_args()

    # ── validate inputs ───────────────────────────────────────────────
    if not os.path.isfile(args.ephys_file):
        sys.exit(f"ERROR: ephys file not found: {args.ephys_file}")
    if not os.path.isdir(args.tiff_folder):
        sys.exit(f"ERROR: tiff folder not found: {args.tiff_folder}")
    if args.start > args.end:
        sys.exit(f"ERROR: -start ({args.start}) must be <= -end ({args.end})")

    # ── collect TIFFs by embedded frame number ────────────────────────
    all_tiffs = collect_tiffs(args.tiff_folder)
    if not all_tiffs:
        sys.exit(f"ERROR: no .tif/.tiff files found in {args.tiff_folder}")

    # tiff_frame_number = ephys_second - offset
    tiff_start = args.start - args.offset
    tiff_end   = args.end   - args.offset  # inclusive

    selected_tiffs = [
        all_tiffs[n] for n in sorted(all_tiffs)
        if tiff_start <= n <= tiff_end
    ]

    if not selected_tiffs:
        sys.exit(
            f"ERROR: no TIFF frames found in range [{tiff_start}, {tiff_end}]. "
            f"Available frame numbers: {min(all_tiffs)}–{max(all_tiffs)}"
        )

    print(f"Selected {len(selected_tiffs)} TIFFs "
          f"(frame numbers {tiff_start}–{tiff_end})")

    # ── ephys time window (1 Hz so frame number == second) ───────────
    t_start = float(args.start)
    t_end   = float(args.end + 1)  # +1 to include the last second fully

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