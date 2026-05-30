# MEA Trace Viewer

Lightweight Tkinter GUI for visualizing MEA `.brw` / HDF5 recordings.

## Features

- Spatial electrode grid based on physical channel layout
- Click-to-select electrodes
- Embedded matplotlib trace viewer
- Dual-handle time-range slider
- Save plots as PNG

## Dependencies

```bash
pip install numpy matplotlib h5py
```

## Run
Open Normally
```bash
python mea_gui.py
```

Or with a recording
```bash
python mea_gui.py recording.brw
```