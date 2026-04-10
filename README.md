# Ephys + TIFF Sync Viewer

A synchronized viewer for HDF5 electrophysiology recordings and TIFF image stacks. Both data streams are displayed side by side and played together on a single shared timeline.

---

## Requirements

```
Python 3.10+
h5py
numpy
matplotlib
Pillow
tkinter
```

Install dependencies:

```bash
pip install h5py numpy matplotlib scipy Pillow 
```

---

## Usage

```bash
# Launch with open dialogs
python main_gui.py

# Launch with files pre-loaded
python main_gui.py path/to/recording.h5 path/to/tiff_folder
```

---

## File Structure

```
utils.py            Shared utilities: LRUCache, image helpers, sort key
ephys_file.py       HDF5 data-access layer (no GUI)
ephys_panel.py      Ephys trace viewer panel (tk.Frame)
tiff_panel.py       TIFF image stack viewer panel (tk.Frame)
sync_controller.py  Master clock — drives both panels in sync
main_gui.py         Top-level application window
```

---

## Interface

### TIFF Panel (left)

| Control | Description |
|---|---|
| **Open TIFFs…** | Select a flat folder of `.tif` / `.tiff` files. Frames are sorted naturally (e.g. `frame2.tif` before `frame10.tif`). |
| Frame counter | Shows current frame and total frame count. Updates automatically during playback. |

### Ephys Panel (right)

| Control | Description |
|---|---|
| **Open Ephys…** | Select a WaveSurfer `.h5` / `.hdf5` recording file. This file sets the master timeline duration. |
| **Sweep** | Switch between sweeps in the HDF5 file. Resets the view and cache. |
| **Window (s)** | Width of the visible trace window in seconds. |
| **Decimate** | Display downsampling factor. Higher = faster redraws, less detail. At 10,000 Hz, a value of 10 renders 1,000 points/second — sufficient for visual inspection. |
| **Cache** | Number of data chunks to keep in memory. Rarely needs adjustment. |
| **Y range** | Per-channel y-axis min/max fields at the bottom of the panel. Edit and press **Apply** to rescale without reloading. |
| **Spectrogram** | Currently disabled. Will toggle between trace and spectrogram views once implemented. |

### Bottom Sync Controls

| Control | Description |
|---|---|
| **◀◀ / ▶▶** | Step backward or forward by half the current ephys window width. |
| **▶ Play / ⏸ Pause** | Start and stop synchronized playback across both panels. |
| **Speed** | Playback speed multiplier: `0.25x`, `0.5x`, `1x`, `2x`, `4x`. |
| **TIFF offset (s)** | Temporal offset between the two recordings (see below). Press **Enter** or click away to apply. |
| **Scrubber** | Drag to jump to any point in the recording. Driven by the ephys timeline in seconds. |
| **Time display** | Current playback position in seconds. |

---

## TIFF Offset

The offset accounts for the fact that the ephys and TIFF recordings may not have started at the same moment.

**Definition:** the number of seconds into the ephys recording when the first TIFF frame was captured.

| Offset value | Meaning |
|---|---|
| `0` | Both recordings started at the same time. |
| `+7` | The TIFFs started 7 seconds after the ephys. During the first 7 seconds of playback the TIFF panel holds on frame 1. At t = 7s the TIFF stack begins advancing. |
| `-5` | The TIFFs started 5 seconds before the ephys. The first 5 frames of the TIFF stack have no corresponding ephys data and are skipped. |

---

## Performance Notes

- **Ephys:** only a windowed slice of samples is loaded at a time. Data is decimated before plotting and cached in an LRU cache keyed by `(sweep, channel, start, stop)`.
- **TIFF:** frames are loaded lazily from disk on demand, downsampled to at most 768px on the longest side, and cached in an LRU cache. The panel only redraws when the frame index actually changes.
- **Playback loop:** driven by Tkinter's `after()` scheduler at ~33 fps with wall-clock drift correction — no threads, no blocking calls.

---

## Extending

**Adding spectrogram support:**
1. Implement `EphysPanel._redraw_spectrogram()` in `ephys_panel.py`
2. Change the Spectrogram button state from `tk.DISABLED` to `tk.NORMAL`

The toggle logic, display mode state, and data-fetching path are already in place.

**Changing TIFF frame rate:**
The `frame_rate` parameter on `TiffPanel` (set in `main_gui.py`) controls how many TIFF frames correspond to one second of real time. Currently `1.0`.