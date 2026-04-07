"""
utils.py
--------
Shared utilities for the ephys+TIFF sync viewer.
"""

import os
import re
from collections import OrderedDict
from PIL import Image

# ── tuneable constants ────────────────────────────────────────────────────────
MAX_DISPLAY_PX = 768   # longest side of downsampled TIFF display image (pixels)
CACHE_SIZE     = 30    # default LRU cache size (frames or chunks)
# ─────────────────────────────────────────────────────────────────────────────


class LRUCache:
    """Fixed-size LRU cache backed by an OrderedDict."""

    def __init__(self, capacity: int):
        self._cap   = capacity
        self._store = OrderedDict()

    def get(self, key):
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def put(self, key, value):
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = value
        if len(self._store) > self._cap:
            self._store.popitem(last=False)

    def clear(self):
        self._store.clear()

    def resize(self, new_capacity: int):
        self._cap = max(1, new_capacity)
        while len(self._store) > self._cap:
            self._store.popitem(last=False)


def natural_sort_key(path: str):
    """Sort filenames so that e.g. frame2.tif comes before frame10.tif."""
    parts = re.split(r'(\d+)', os.path.basename(path))
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def load_and_downsample(path: str, max_px: int = MAX_DISPLAY_PX) -> Image.Image:
    """
    Open a TIFF file, convert to RGB, and resize for display.
    Only shrinks — never enlarges. Preserves aspect ratio.
    """
    img = Image.open(path)
    img = img.convert("RGB")
    w, h = img.size
    scale = max_px / max(w, h)
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img
