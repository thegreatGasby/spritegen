#!/usr/bin/env python3
"""Orchestration: description -> master still -> action frames -> sprite sheet.

Entry points:
  make_character(...)  text      -> a master still (reused for many actions)
  make_views(...)      text      -> one master per view, from one turnaround
  make_parts(...)      text      -> cut-out body parts for 2D skeletal rigging
  make_action(...)     master    -> one action (image-grid or i2v) -> sheet
  make(...)            one-shot  -> character + action in one call
"""
import base64
import glob
import json
import os
import subprocess
import tempfile
import time
import uuid

from PIL import Image

from . import imageops, packer, prompts, qwen

CELL_HD = 384


def cell_to_canvas(cell):
    """Output cell size in px -> square per-frame canvas."""
    if isinstance(cell, str):
        if cell.upper() == "HD":
            return CELL_HD
        cell = int(cell)
    return int(cell)


def resolve_fields(text, style=None, view=None, facing=None, action=None,
                   use_llm=True, llm_model=qwen.LLM_MODEL, log=print):
    """LLM classifies the free text; on any failure fall back to static fields."""
    if use_llm and text:
        overrides = {k: v for k, v in
                     (("style", style), ("view", view), ("facing", facing), ("action", action)) if v}
        try:
            reply = qwen.chat(prompts.llm_messages(text, overrides),
                              model=llm_model, temperature=0.3)
            d = prompts.parse_llm_json(reply)
            if d.get("subject"):
                return {
                    "subject": d["subject"],
                    "style": style or d.get("style") or "pixel16",
                    "view": view or d.get("view") or "side",
                    "facing": facing or d.get("facing") or "right",
                    "action": action or d.get("action") or "idle",
                }
            log(f"  llm: no usable JSON, falling back ({reply[:80]!r})")
        except Exception as e:                       # noqa: BLE001 - best-effort
            log(f"  llm failed ({e}); falling back to static fields")
    return prompts.resolve(text, style, view, facing, action)


def extract_frames(video, n, fps=6, pick=None):
    """ffmpeg -> fps sample -> pick n frames spread over the clip (half-open, so
    the loop's end pose is not duplicated at the start)."""
    tmp = tempfile.mkdtemp(prefix="spritegen_")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video,
                    "-vf", f"fps={fps}", f"{tmp}/r_%03d.png"], check=True)
    files = sorted(glob.glob(f"{tmp}/r_*.png"))
    if not files:
        raise RuntimeError("ffmpeg produced no frames")
    if pick:
        idx = [int(x) for x in pick if 0 <= int(x) < len(files)]
    else:
        total = len(files)
        seen, idx = set(), []
        for i in range(n):
            j = min(round(i * total / n), total - 1)
            if j not in seen:
                seen.add(j)
                idx.append(j)
    return [Image.open(files[i]).convert("RGBA") for i in idx]


def _noop(*_):
    pass


def _data_uri(path):
    """Inline an image as a data URI — lets i2v use a locally-cropped still that
    has no public URL (i2v accepts a base64 data URI)."""
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    with open(path, "rb") as fh:
        return f"data:{mime};base64," + base64.b64encode(fh.read()).decode()


def make_character(text, style=None, view=None, facing=None, key_mode="green",
                   out_dir="out/characters", use_llm=True, llm_model=qwen.LLM_MODEL,
                   log=print, progress=_noop):
    progress(0.05, "parsing")
    fields = resolve_fields(text, style, view, facing, None, use_llm, llm_model, log)
    master_prompt = prompts.render_master(fields["subject"], fields["style"],
                                          fields["view"], fields["facing"],
                                          key_mode=key_mode)
    log(f"  character: style={fields['style']} view={fields['view']} "
        f"facing={fields['facing']} key={key_mode}")
    progress(0.2, "master still")
    paths, urls = qwen.generate_image(master_prompt, out_dir, size="1024*1024", tag="master")
    rec = {"fields": fields, "master_path": paths[0], "master_url": urls[0],
           "master_prompt": master_prompt, "key_mode": key_mode,
           "created": time.time(), "text": text}
    json.dump(rec, open(os.path.join(out_dir, "character.json"), "w"), indent=1)
    progress(1.0, "done")
    return rec


def make_views(text, views, style=None, facing=None, key_mode="green",
               char_root="out/characters", use_llm=True, llm_model=qwen.LLM_MODEL,
               log=print, progress=_noop):
    """One turnaround image -> one master still PER view, mutually consistent.

    The gateway cannot take a reference image, so the only way to anchor a
    character across views is to draw them in ONE generation. We ask for an
    n-across turnaround on the key colour, segment each figure (extract_figures)
    and save it as that view's master. Raises if the model did not draw exactly
    `len(views)` figures, so the caller can fall back to per-view generation.
    Each returned record mirrors make_character's, with master_url left null —
    i2v inlines the local master as a data URI instead.
    """
    progress(0.05, "parsing")
    fields = resolve_fields(text, style, None, facing, None, use_llm, llm_model, log)
    subj, st, fc = fields["subject"], fields["style"], fields["facing"]
    progress(0.2, "view turnaround")
    gp = prompts.render_views(subj, st, fc, views, key_mode=key_mode)
    paths, urls = qwen.generate_image(gp, char_root, size=prompts.views_size(len(views)),
                                      tag="views")
    img = Image.open(paths[0]).convert("RGB")
    tiles = imageops.extract_figures(img, key_mode=key_mode)
    log(f"  turnaround {len(views)} views: segmented {len(tiles)} figures (asked {len(views)})")
    if len(tiles) != len(views):
        raise RuntimeError(f"turnaround drew {len(tiles)} figures, wanted {len(views)}")
    bg = tuple(bytes.fromhex(prompts.KEYS.get(key_mode, prompts.KEYS["green"])["hex"].lstrip("#")))
    out = []
    for v, tile in zip(views, tiles):
        # the model adds a dark card frame + a white seam line + "%" text to the
        # turnaround; keying drops the flat/card bits, drop_border_blobs removes
        # the seam (it spans the crop, so it touches a border), then flatten the
        # clean figure back onto flat green — a master i2v can read directly.
        tile = imageops.drop_border_blobs(tile)
        bb = tile.getbbox()
        if bb:
            tile = tile.crop(bb)
        pad = 16
        # i2v rejects a reference whose shorter side is under ~240px; a slim
        # side-view figure can crop narrower than that, so pad up to a floor.
        w = max(tile.width + 2 * pad, 256)
        h = max(tile.height + 2 * pad, 256)
        flat = Image.new("RGB", (w, h), bg)
        flat.paste(tile, ((w - tile.width) // 2, (h - tile.height) // 2), tile)
        cid = uuid.uuid4().hex[:8]
        d = os.path.join(char_root, cid)
        os.makedirs(d, exist_ok=True)
        mp = os.path.join(d, "master_0.png")
        flat.save(mp)
        rec = {"fields": {"subject": subj, "style": st, "view": v, "facing": fc},
               "master_path": os.path.abspath(mp), "master_url": None,
               "master_prompt": gp, "key_mode": key_mode,
               "created": time.time(), "text": text}
        json.dump(rec, open(os.path.join(d, "character.json"), "w"), indent=1)
        out.append(rec)
    progress(1.0, "done")
    return out


def make_parts(text, rig="humanoid", style=None, view=None, facing="right",
               key_mode="green", out_dir="out/parts", use_llm=True,
               llm_model=qwen.LLM_MODEL, log=print, progress=_noop):
    """Text -> the cut-out body parts of ONE character, for 2D skeletal rigging.

    Same one-generation trick as make_views: this client only talks text2image,
    so a parts sheet drawn in ONE generation is the only way the pieces are
    guaranteed to belong to the same character. The grid cell order IS the part
    name (extract_parts returns reading order), so nothing needs labelling.

    Emits one transparent PNG per part, a reassembled rest-pose sheet, and a
    manifest carrying each part's bbox plus its rest offset in character-height
    units (+x right, +y down, origin at the assembled character's centre).
    """
    progress(0.05, "parsing")
    spec = prompts.RIGS.get(rig) or prompts.RIGS["humanoid"]
    view = view or spec["view"]          # side view occludes the arms; rig says front
    fields = resolve_fields(text, style, view, facing, None, use_llm, llm_model, log)
    parts = spec["parts"]
    n = len(parts)
    prompt = prompts.render_parts(fields["subject"], rig, fields["style"], view,
                                  facing, key_mode=key_mode)
    cols, rows = prompts.part_grid(n)     # the exact layout we asked the model for
    # The model does not always honour the grid — it drops a part or draws two as
    # one — so generate until the sheet actually holds n parts. Grouping the
    # blobs by the KNOWN cells also stitches a part the model drew in one image
    # as two shapes. We never guess a cell→part mapping, so a short sheet is
    # regenerated (up to tries), and only then reported as a failure.
    tries = 3
    tiles = []
    for attempt in range(1, tries + 1):
        progress(0.2, f"parts sheet ({n})" + (f" · try {attempt}" if attempt > 1 else ""))
        paths, _ = qwen.generate_image(prompt, out_dir, size=prompts.parts_size(n),
                                       tag="parts")
        img = Image.open(paths[0]).convert("RGB")
        tiles = imageops.extract_parts(img, key_mode=key_mode, cols=cols, rows=rows)
        log(f"  parts: segmented {len(tiles)} pieces (asked {n})"
            + (f" — retry {attempt}/{tries}" if len(tiles) != n else ""))
        if len(tiles) == n:
            break
    if len(tiles) != n:
        raise RuntimeError(
            f"parts sheet drew {len(tiles)} pieces, wanted {n} (after {tries} tries)")

    # Rest pose: paste every part back at the offset it was drawn at, into a
    # canvas cropped to the union. All tiles share extract_parts' pad, so the
    # relative placement is the model's own and the assembly is geometrically
    # the character it cut apart.
    x0 = min(o[0] for _, o in tiles)
    y0 = min(o[1] for _, o in tiles)
    x1 = max(o[0] + t.width for t, o in tiles)
    y1 = max(o[1] + t.height for t, o in tiles)
    rest = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    for tile, o in tiles:
        rest.alpha_composite(tile, (o[0] - x0, o[1] - y0))
    rest_path = os.path.join(out_dir, "parts_sheet.png")
    rest.save(rest_path)

    gh = max(1, y1 - y0)
    gcx, gcy = (x0 + x1) / 2, (y0 + y1) / 2
    pdir = os.path.join(out_dir, "parts")
    os.makedirs(pdir, exist_ok=True)
    manifest = []
    for (name, _), (tile, off) in zip(parts, tiles):
        p = os.path.join(pdir, f"{name}.png")
        tile.save(p)
        cx, cy = off[0] + tile.width / 2, off[1] + tile.height / 2
        manifest.append({
            "name": name, "path": os.path.abspath(p),
            "bbox": [off[0], off[1], tile.width, tile.height],
            "offset": {"x": round((cx - gcx) / gh, 4),
                       "y": round((cy - gcy) / gh, 4)},
        })
    res = {"kind": "parts", "rig": rig, "view": view, "facing": facing,
           "style": fields["style"], "subject": fields["subject"], "count": n,
           "material": os.path.abspath(paths[0]), "sheet": os.path.abspath(rest_path),
           "parts": manifest}
    json.dump(res, open(os.path.join(out_dir, "result.json"), "w"), indent=1)
    progress(1.0, "done")
    return res


def make_action(action="idle", mode="image", frames=8, cell=64, character=None,
                text=None, style=None, view=None, facing=None, out_dir="out/actions",
                use_llm=True, llm_model=qwen.LLM_MODEL, key_mode="green", colors=48,
                fps=6, resolution="480P", duration=5, sat=1.0, contrast=1.0,
                preview_scale=0, atlas=False, pick=None, log=print, progress=_noop):
    if not character:
        character = make_character(text, style, view, facing, key_mode=key_mode,
                                   out_dir=os.path.join(out_dir, "character"),
                                   use_llm=use_llm, llm_model=llm_model, log=log,
                                   progress=lambda fr, st: progress(fr * 0.5, st))
    f = character["fields"]
    action = action or f.get("action") or "idle"
    canvas_px = cell_to_canvas(cell)
    canvas = (canvas_px, canvas_px)
    gen_dir = os.path.join(out_dir, "raw")
    os.makedirs(gen_dir, exist_ok=True)

    qc = {}
    progress(0.55, f"{mode} generation")
    if mode == "image":
        gp = prompts.render_grid(f["subject"], action, f["style"], f["view"], f["facing"],
                                 frames, key_mode=key_mode)
        paths, _ = qwen.generate_image(gp, gen_dir, size=prompts.grid_size(frames), tag="grid")
        grid = Image.open(paths[0]).convert("RGB")
        # Segment each character blob: this finds the layout AND isolates the
        # figure, so the model's card frame / outlines / ground never reach the
        # sheet (no gutter guessing, no colour-threshold tuning).
        tiles = imageops.extract_figures(grid, key_mode=key_mode)
        if len(tiles) < 2:                           # degenerate: fall back to gutters
            cols, rows = imageops.detect_grid(grid, key_mode=key_mode)
            tiles = imageops.slice_grid(grid, cols, rows)
            log(f"  grid: blob segment degenerate; sliced by gutters {cols}x{rows}")
        else:
            log(f"  grid: segmented {len(tiles)} figures (asked {frames})")
        if len(tiles) != frames and len(tiles) > 1:  # keep the 6/8/12 sheet contract
            idx = [round(i * (len(tiles) - 1) / (frames - 1)) for i in range(frames)] \
                if frames > 1 else [0]
            tiles = [tiles[i] for i in idx]
        qc = {f"cell{i}": round(imageops.spill_check(t), 4) for i, t in enumerate(tiles)}
        raw = tiles
    else:
        mp = prompts.render_motion(f["subject"], action, f["style"], f["view"],
                                   f["facing"], key_mode=key_mode)
        ref = character.get("master_url")            # turnaround masters have none
        if not (ref and ref.startswith("http")):
            ref = _data_uri(character["master_path"])
        mp4 = qwen.generate_video(ref, mp, gen_dir,
                                  resolution=resolution, duration=duration)
        raw = extract_frames(mp4, frames, fps=fps, pick=pick)

    progress(0.85, "keying & registration")
    # i2v frames: key-only (the animated character may crop the frame, so the
    # geometric border/blob cleanup would erase it). Image-mode tiles are already
    # isolated figures, so they keep the full cleanup.
    finished = imageops.register_frames(raw, canvas, colors=colors, sat=sat,
                                        contrast=contrast, key_mode=key_mode,
                                        frame=(mode != "image"))
    res = packer.pack_sheet(finished, out_dir, fps=fps, preview_scale=preview_scale, atlas=atlas)
    progress(1.0, "done")
    res.update({"action": action, "mode": mode, "style": f["style"], "view": f["view"],
                "facing": f["facing"], "subject": f["subject"], "cell": cell,
                "qc": qc, "master_path": character["master_path"],
                "master_url": character["master_url"]})
    json.dump(res, open(os.path.join(out_dir, "result.json"), "w"), indent=1)
    return res


def make(text, action=None, mode="image", frames=8, cell=64, key_mode="green",
         out_dir="out/make", progress=_noop, **kw):
    log = kw.pop("log", print)
    ch_kw = ("style", "view", "facing", "use_llm", "llm_model")
    ch = make_character(text, key_mode=key_mode,
                        out_dir=os.path.join(out_dir, "character"), log=log,
                        progress=lambda fr, st: progress(fr * 0.5, st),
                        **{k: v for k, v in kw.items() if k in ch_kw})
    return make_action(action=action or ch["fields"]["action"], mode=mode, character=ch,
                       frames=frames, cell=cell, key_mode=key_mode, out_dir=out_dir, log=log,
                       progress=lambda fr, st: progress(0.5 + fr * 0.5, st),
                       **{k: v for k, v in kw.items() if k not in ch_kw})
