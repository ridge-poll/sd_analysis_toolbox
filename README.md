# Ephys + TIFF Sync Viewer

A synchronized viewer for HDF5 electrophysiology recordings and TIFF image stacks. Both data streams are displayed side by side and played together on a single shared timeline.

## Visual Overview
![Trace view](docs/analysis_gui_screenshot.png)

*Example of the GUI showing synchronized electrophysiology and TIFF playback. The view includes DC and AC traces with an optional spectrogram for frequency-domain analysis.*

---

## Requirements

```
Python 3.10+
h5py
numpy
matplotlib
scipy
Pillow
tkinter
```

> `tkinter` is included with most Python installations and does not need to be installed via pip.

```bash
pip install -r requirements.txt
```

---

## Usage

```bash
# Launch with open dialogs
python main_gui.py

# Launch with files pre-loaded
python main_gui.py path/to/recording.h5 path/to/tiff_folder
```

```bash
# Or use the provided launcher scripts:
# macOS
run_viewer.command

# Windows
run_viewer.bat
```

---

## File Structure

```
sd_analysis_toolbox/
│
├── sd_viewer/
│   ├── main_gui.py              Top-level application window
│   ├── ephys_file.py            HDF5 data-access layer (no GUI)
│   ├── ephys_panel.py           Ephys trace + spectrogram panel (tk.Frame)
│   ├── tiff_panel.py            TIFF image stack viewer panel (tk.Frame)
│   ├── spectrogram.py           Spectrogram computation (signal processing only)
│   ├── sync_controller.py       Master clock — drives both panels in sync
│   └── utils.py                 Shared utilities: LRUCache, helpers
│
├── scripts/
│   ├── run_viewer.command
│   ├── run_viewer.bat
│   ├── setup.command
│   └── setup.bat
│
├── requirements.txt
└── README.md
```

---

## Interface

### TIFF Panel (left)

| Control | Description |
|---|---|
| **Open TIFFs…** | Select a flat folder of `.tif` / `.tiff` files. Frames are sorted naturally (e.g. `frame2.tif` before `frame10.tif`). |
| **Normalize** | Toggle percentile contrast normalization (1st–99th percentile). Flushes cache and redraws immediately. |
| **Select ROI** | Pause playback and draw a rectangle on the canvas. Only the selected region is extracted from each frame and displayed. Cropping is applied before downsampling to preserve full resolution within the ROI. |
| **Clear ROI** | Remove the active crop and display the full frame. |
| Frame counter | Shows current frame index and total frame count (e.g. `42 / 300`). |

> ROI selection pauses playback automatically. Click-drag on the image to define a region. The crop is applied at full resolution before downsampling.

![ROI demo](docs/sd_roi_demo.gif)

### Ephys Panel (right)

The ephys panel displays DC and AC traces on a shared time axis, with an optional spectrogram view.

| Control | Description |
|---|---|
| **Open Ephys…** | Select a WaveSurfer `.h5` / `.hdf5` recording file. Sets the master timeline duration. The first sweep is loaded automatically. |
| **Window (s)** | Width of the visible trace window in seconds. |
| **Decimate** | Display downsampling factor. Higher = faster redraws, less detail. At 10,000 Hz, a value of 10 renders 1,000 points/second — sufficient for visual inspection. |
| **Cache** | Number of data chunks to keep in memory. Rarely needs adjustment. |
| **Spectrogram** | Toggle a third subplot below the traces showing a power spectrogram of the AC channel. When hidden, no FFT computation is performed. |
| **Y range** | Per-channel min/max entry fields at the bottom of the panel. Edit and press **Apply** to rescale without reloading. |

### Bottom Sync Controls

| Control | Description |
|---|---|
| **◀◀ / ▶▶** | Step backward or forward by half the current ephys window width. |
| **▶ Play / ⏸ Pause** | Start and stop synchronized playback across both panels. |
| **Speed** | Playback speed multiplier: `0.25x`, `0.5x`, `1x`, `2x`, `4x`, `8x`, `16x`. |
| **TIFF offset (s)** | Temporal offset between the two recordings (see below). Press **Enter** or click away to apply. |
| **Scrubber** | Drag to jump to any point in the recording. Driven by the ephys timeline in seconds. |
| **Time display** | Current playback position in seconds. |

---

## Spectrogram

The spectrogram shows the AC channel’s frequency content over time.

- X-axis: time (seconds)
- Y-axis: frequency (Hz)
- Color: power in dB (inferno colormap)

It updates only when the visible time window changes.

---

## TIFF Offset

The offset accounts for the fact that the ephys and TIFF recordings may not have started at the same moment.

**Definition:** the number of seconds into the ephys recording when the first TIFF frame was captured.

| Offset value | Meaning |
|---|---|
| `0`  | Synchronized start |
| `+7` | TIFF starts 7 s after ephys; initial frames are held |
| `-5` | TIFF starts 5 s before ephys; early frames are skipped |

---

## Performance Notes

- **Ephys traces:** only a windowed slice of samples is loaded at a time. Data is decimated before plotting and cached in an LRU cache keyed by `(sweep, channel, start, stop)`.
- **Spectrogram:** recomputed only when the sample window changes. A small overhang beyond the visible window is fetched for the FFT so the trailing edge of the display is always fully populated.
- **TIFF:** frames are loaded lazily from disk, cropped to the active ROI at full resolution, downsampled to at most 768 px on the longest side, and cached in an LRU cache. A thread-pool prefetcher (4 workers, up to 128 frames ahead at high speed) keeps frames ready before they are needed. The panel only redraws when the frame index actually changes.
- **Playback loop:** driven by Tkinter's `after()` scheduler at ~33 fps with wall-clock drift correction — no threads, no blocking calls.