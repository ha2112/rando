# Capture image support

Analyzed the local tablet cache on 2026-11-13 with `rmscene 0.8.0` and
`PyMuPDF 1.27.2.3`. The feature described in reMarkable's
[Capture tool documentation](https://support.remarkable.com/articles/Knowledge/Capture-tool)
stores pasted images separately from handwritten lines.

## Evidence from the supplied documents

| Document | UUID | Active pages | Visible captures |
| --- | --- | ---: | ---: |
| Nhap copy | `ac6c6386-7180-4d1e-aa5a-409c47135a3d` | 19 | 1, on page 19 |
| feliz navidad | `3e6e29f2-a792-48c1-ae2d-bc1e02d740a3` | 2 | 0 |

Both scene files still have the version 6 header. The relevant notebook page is
`5be419b8-d59c-49c5-8ac1-65221e799420.rm`. Its image is a 396 x 475 PNG at:

```text
ac6c6386-7180-4d1e-aa5a-409c47135a3d/
  5be419b8-d59c-49c5-8ac1-65221e799420.rm
  5be419b8-d59c-49c5-8ac1-65221e799420/
    d0f67402-2bbb-45c5-85bb-f293231db9a6.png
```

The notebook's new records are an asset table (`0x0e`, min/current version 3/3,
97 bytes) and an image scene item (`0x0f`, version 2/2, 155 bytes). The PDF's
`173b1c4c-a4eb-4ff5-ab18-7221287133b3.rm` contains a six-byte empty asset table
and a deleted line. It must render successfully without requiring a PNG.

The previous compatibility fixture's empty `0x0e` table and deleted `0x0f`
item are now decoded records, replacing the exact-payload exception.

## Decoded layout

- `0x0e`: tagged subblock 1 contains an entry count and subblock-0 entries.
  Each entry has a raw 16-byte asset key, a timestamped PNG filename in
  subblock 1, and asset metadata in subblock 2. The observed metadata flag is
  zero; its broader meaning is unverified.
- `0x0f`: parent/item/left/right CRDT IDs and a deletion length use the same
  envelope as other scene items. Optional subblock 6 has item type 7, an asset
  reference, a placement ID, a float array, and a triangle-index array.
- The float array's count is the number of float32 values, grouped as
  `(x, y, u, v)`. The integer array's count is the number of uint32 indices,
  grouped into triangles. This interpretation is corroborated by the rendered
  PNG's orientation and position in the tablet thumbnail.

The supplied image has scene bounds
`(-59.6257706, 581.5195923, 219.6257629, 916.4804077)`, UV corners
`(0,0), (1,0), (1,1), (0,1)`, and triangles `(0,1,2), (2,3,0)`.
Its irregular lasso outline is stored in the PNG alpha channel.

## Pasted and moved snippets (2026-11-24)

Moving, resizing or pasting a snippet tombstones the old `0x0f` item and writes
a new one. These newer items are 158 bytes: subblock 6 grows by three bytes,
ending in tagged ID index 5 (`5f 01 xx`). In the sample, that ID always names a
deleted image item on the same page (`1:0x28` for item `1:0x2a`, `1:0x3f` for
item `1:0x44`). It does not affect geometry and is read and ignored. Any other
trailing field still fails.

Page 19 of Nhap copy now has three live snippets (396 x 475, 385 x 473, and
1400 x 1528 PNGs) and a three-entry asset table. Compared with the tablet's
own export (`test_data/Nhap copy.pdf`), all three image boxes match to 0.01 pt,
and paint order (two snippets, ink, then the third snippet) is identical. At
72 dpi, 33 of 264,330 pixels differ by more than 32 levels; the mean absolute
difference is 0.53.

## Implementation and verification

`rm_sync/scene_images.py` decodes these two observed record versions locally,
without modifying rmscene. Images enter the existing scene tree before visible
items are visited. The renderer uses the ink coordinate transform, includes
image geometry in page bounds, and draws textured triangles in scene order.
Triangles sharing an affine transform share a clip to avoid diagonal seams.
Page-local PNG content participates in render fingerprints, and the renderer
revision invalidates old cached results.

Run the regressions from `remarkable-download`:

```bash
python -m unittest discover -s test -p 'test_*.py'
```

Validation completed with **64 tests passing** (9 fixture tests skipped because their local `test_data` folders were missing), including 14 capture tests.
The new tests embed the supplied asset/image record bytes and generate a small
RGBA PNG, so they also run without the local tablet cache. They check notebook
and PDF placement, UV orientation, alpha, rotation, triangle clipping, scene
order, hidden/deleted images, page-state reset, extended page bounds, invalid
records/versions, PNG cache invalidation, and preservation of previous output
on missing or corrupt assets.

Both real documents rendered to `output/pdf/`. In the notebook output, page 19
places the image at approximately `(203.749, 184.447, 292.322, 290.690)` PDF
points. The embedded image retains its 396 x 475 dimensions and all 188,100
alpha samples exactly; opaque RGB samples also match. Decoding the embedded
PNG through MuPDF introduces at most two levels of RGB rounding at partially
transparent pixels. Poppler page renders were inspected against the tablet
thumbnail for placement and lasso shape. Both rendered source-PDF pages have
identical raster pixels to the original PDF at PyMuPDF's default resolution.

Source SHA-256 values for reproducing this analysis:

| File | SHA-256 |
| --- | --- |
| Notebook capture `.rm` | `19dc20505eb6ca19ad896477b0b54fc3f5fad5883a3dd28ea0e210869ce91a6d` |
| Capture PNG | `84ba84900c0c0364cd573cb9f590aa70ef478de02bfde3885ae27bf0e1eb53ad` |
| Source PDF page `.rm` | `a68de29c0a84039ececcd1084feadcbc8b02fde859178177b2fa15178656cfc5` |

Support is limited to the observed block versions and zero-valued asset flag;
new layouts still fail explicitly. Unparsed SceneInfo extensions and existing
brush/template limitations remain reported as before. The tablet thumbnail's
grid background is outside this capture change. The input cache is unchanged;
the generated PDFs are separate outputs.
