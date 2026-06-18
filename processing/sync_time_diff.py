#!/usr/bin/env python3

"""
usage: python sync_time_diff.py file.h5 file.tif
"""

import argparse
import h5py
from tifffile import TiffFile
from datetime import datetime


def parse_h5_time(path):
    with h5py.File(path, "r") as f:
        raw = f["header/ClockAtRunStart"][()].flatten()

        year, month, day, hour, minute, second = raw

        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
        )


def parse_tif_time(path):
    with TiffFile(path) as tif:
        page = tif.pages[0]
        tag = page.tags.get("DateTimeOriginal")

        if tag is None:
            raise ValueError("DateTimeOriginal not found in TIFF")

        raw = str(tag.value)

        date_part, time_part = raw.split(".")
        y, m, d = map(int, date_part.split("-"))
        hh, mm, ss = map(int, time_part.split(":"))

        return datetime(y, m, d, hh, mm, ss)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("h5_file")
    parser.add_argument("tif_file")
    args = parser.parse_args()

    h5_time = parse_h5_time(args.h5_file)
    tif_time = parse_tif_time(args.tif_file)

    diff = abs(tif_time - h5_time)

    print("\n=== H5 time ===")
    print(h5_time)

    print("\n=== TIFF time ===")
    print(tif_time)

    print("\n=== Absolute difference ===")
    print(diff)
    print(f"seconds: {diff.total_seconds():.3f}")


if __name__ == "__main__":
    main()