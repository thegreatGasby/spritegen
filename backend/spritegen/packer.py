#!/usr/bin/env python3
"""Pack finished frames into a transparent PNG sheet + a looping preview.

Mirrors the site's output: 6 and 8 frames in a single row, 12 as a 4x3 grid;
transparent background and even spacing. Also emits a generic atlas JSON and an
optional Godot 4 SpriteFrames resource.
"""
import json
import os

from PIL import Image

from .prompts import SHEET_LAYOUT


def layout_for(n, cols=None, rows=None):
    if cols and rows:
        return cols, rows
    if n in SHEET_LAYOUT:
        return SHEET_LAYOUT[n]
    cols = n if n <= 8 else 4
    return cols, -(-n // cols)


def _grid(frames, cols, rows, cell):
    cw, ch = cell
    sheet = Image.new("RGBA", (cols * cw, rows * ch), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        r, c = divmod(i, cols)
        sheet.alpha_composite(f, (c * cw, r * ch))
    return sheet


def _gif(frames, path, fps, bg, scale):
    rgb = []
    for f in frames:
        base = Image.new("RGBA", f.size, tuple(bg) + (255,))
        base.alpha_composite(f)
        rgb.append(base.convert("RGB").resize((f.width * scale, f.height * scale), Image.NEAREST))
    rgb[0].save(path, save_all=True, append_images=rgb[1:],
                duration=max(20, round(1000 / max(1, fps))), loop=0, disposal=2)


def _atlas(frames, cols, rows, fps, image_name):
    cw, ch = frames[0].size
    return {
        "image": image_name,
        "frameCount": len(frames),
        "fps": fps,
        "columns": cols,
        "rows": rows,
        "frameWidth": cw,
        "frameHeight": ch,
        "frames": [
            {"index": i, "name": f"f{i}", "x": (i % cols) * cw, "y": (i // cols) * ch,
             "w": cw, "h": ch, "durationMs": round(1000 / max(1, fps))}
            for i in range(len(frames))
        ],
    }


def _godot(frames, cols, rows, fps, image_name):
    cw, ch = frames[0].size
    subs = ",\n".join(
        '{"id": "%d", "path": "res://%s", "region": Rect2(%d, %d, %d, %d)}'
        % (i, image_name, (i % cols) * cw, (i // cols) * ch, cw, ch)
        for i in range(len(frames))
    )
    return (
        '[gd_resource type="SpriteFrames" load_steps=%d format=3]\n\n'
        '[ext_resource type="Texture2D" path="res://%s" id="1"]\n\n'
        '[sub_resource type="AtlasTexture" id="A0"]\ntexture = ExtResource("1")\n'
        'region = Rect2(0, 0, %d, %d)\n\n'
        '[resource]\nanimations = [{\n"frames": [%s],\n'
        '"loop": true,\n"name": &"default",\n"speed": %.1f\n}]\n'
        % (len(frames) + 2, image_name, cw, ch, subs, fps)
    )


def pack_sheet(frames, out_dir, name="sheet", cols=None, rows=None, fps=8,
               preview_scale=4, preview_bg=(60, 40, 60), atlas=False):
    """Write sheet.png, preview.gif and (optionally) atlas/godot metadata."""
    if not frames:
        raise ValueError("no frames to pack")
    os.makedirs(out_dir, exist_ok=True)
    cols, rows = layout_for(len(frames), cols, rows)
    cell = frames[0].size
    sheet_name = f"{name}.png"

    sheet = _grid(frames, cols, rows, cell)
    sheet_path = os.path.join(out_dir, sheet_name)
    sheet.save(sheet_path)

    gif_path = os.path.join(out_dir, f"{name}.gif")
    scale = preview_scale if preview_scale and preview_scale > 0 else max(1, round(256 / max(cell)))
    _gif(frames, gif_path, fps, preview_bg, scale)

    result = {
        "sheet": sheet_path, "preview": gif_path,
        "columns": cols, "rows": rows, "frameCount": len(frames),
        "frameWidth": cell[0], "frameHeight": cell[1], "fps": fps,
    }

    for i, f in enumerate(frames):
        f.save(os.path.join(out_dir, f"f{i:02d}.png"))

    if atlas:
        ap = os.path.join(out_dir, "atlas.json")
        json.dump(_atlas(frames, cols, rows, fps, sheet_name), open(ap, "w"), indent=1)
        gp = os.path.join(out_dir, "spriteframes.tres")
        open(gp, "w").write(_godot(frames, cols, rows, fps, sheet_name))
        result["atlas"] = ap
        result["godot"] = gp

    return result
