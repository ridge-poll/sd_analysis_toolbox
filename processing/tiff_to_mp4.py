import os
import re
import sys
import cv2


def final_number(filename):
    name = os.path.splitext(os.path.basename(filename))[0]
    m = re.search(r'(\d+)$', name)
    return int(m.group(1)) if m else -1


if len(sys.argv) != 3:
    print("Usage: python tiff_to_mp4.py <input_folder> <output_folder>")
    sys.exit(1)

input_folder = sys.argv[1]
output_folder = sys.argv[2]

os.makedirs(output_folder, exist_ok=True)

files = [
    os.path.join(input_folder, f)
    for f in os.listdir(input_folder)
    if f.lower().endswith((".tif", ".tiff"))
]

files.sort(key=final_number)

if not files:
    print("No TIFF files found.")
    sys.exit(1)

# Preview settings
FPS = 30
SCALE = 0.5

output_path = os.path.join(
    output_folder,
    "preview.mp4"
)

# First frame determines size
img = cv2.imread(files[0], cv2.IMREAD_UNCHANGED)

if img is None:
    print("Could not load first frame.")
    sys.exit(1)

if img.dtype != 'uint8':
    img = cv2.normalize(
        img,
        None,
        0,
        255,
        cv2.NORM_MINMAX
    ).astype('uint8')

if len(img.shape) == 2:
    img = cv2.cvtColor(
        img,
        cv2.COLOR_GRAY2BGR
    )

h, w = img.shape[:2]
w2 = int(w * SCALE)
h2 = int(h * SCALE)

fourcc = cv2.VideoWriter_fourcc(*'mp4v')

writer = cv2.VideoWriter(
    output_path,
    fourcc,
    FPS,
    (w2, h2)
)

for i, f in enumerate(files):

    img = cv2.imread(
        f,
        cv2.IMREAD_UNCHANGED
    )

    if img is None:
        continue

    if img.dtype != 'uint8':
        img = cv2.normalize(
            img,
            None,
            0,
            255,
            cv2.NORM_MINMAX
        ).astype('uint8')

    if len(img.shape) == 2:
        img = cv2.cvtColor(
            img,
            cv2.COLOR_GRAY2BGR
        )

    img = cv2.resize(
        img,
        (w2, h2),
        interpolation=cv2.INTER_AREA
    )

    writer.write(img)

    if i % 100 == 0:
        print(f"{i}/{len(files)}")

writer.release()

print("Saved:", output_path)