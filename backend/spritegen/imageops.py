#!/usr/bin/env python3
"""Image finishing: chroma-key, cleanup, pixel finish and cross-frame registration.

The geometry (drop_ground / prune_small / kill_side_clutter / body_metrics /
derive / shared palette) is lifted from the godot project's seq2sprite.py, so a
sweeping weapon does not pump the body and colours never flicker between frames.
The keyer is colour-configurable — green by default (matching the prompt
contract), magenta kept for older assets.
"""
import numpy as np
from PIL import Image, ImageEnhance


# --- chroma key --------------------------------------------------------------

def key_magenta(im, tol=60):
    """Drop a pink/magenta backdrop (legacy key mode; the contract is green now).

    Two families are keyed, both red-dominant over green (r>g and b>g), which is
    what makes them magenta rather than a legitimately warm character:
      * the bright flat magenta #FF00FF field (and its anti-aliased fringe);
      * the DARK magenta/maroon the model likes to draw as sheet frames, grid
        dividers, borders and drop shadows (e.g. #40092A, #AB136B) — dark so the
        bright character stays above the luminance gate.
    A pink character (green channel high, e.g. #FF78CD) and a near-black outline
    (r too low) are both protected.
    """
    rgb = np.asarray(im.convert("RGB")).astype(int)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    bright = (r > 120) & (b > 120) & (g < 110) & (r - g > tol) & (b - g > tol)
    dark = ((g < 90) & (r - g > 30) & (b - g > 12) & (r >= b - 8) &
            (r + g + b < 340) & (r > 40))
    mag = bright | dark
    alpha = np.where(mag, 0, 255).astype("uint8")
    return Image.fromarray(np.dstack([rgb.astype("uint8"), alpha]), "RGBA")


def key_green(im, tol=28):
    """Drop green backdrop: keep interior greens (olive costume tones survive)."""
    rgb = np.asarray(im.convert("RGB")).astype(int)
    g = rgb[:, :, 1]
    greenish = (g > 85) & (g - rgb[:, :, 0] > tol) & (g - rgb[:, :, 2] > tol)
    alpha = np.where(greenish, 0, 255).astype("uint8")
    return Image.fromarray(np.dstack([rgb.astype("uint8"), alpha]), "RGBA")


def key_blue(im, tol=28):
    """Drop blue backdrop — the alternative key for GREEN-costumed characters.

    The model echoes the backdrop hue into a costume for vague prompts, so a
    green-clad character must be drawn on blue (and vice-versa); this keeps the
    character's own colours (e.g. a green tunic: g high, b low) opaque.
    """
    rgb = np.asarray(im.convert("RGB")).astype(int)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    bright = (b > 85) & (b - r > tol) & (b - g > tol)
    # the model often lays a DARK NAVY "graph paper" backdrop behind the blue
    # cards (b ~ 40-60, too dark for the bright gate) — drop that too, while a
    # near-black outline (b-r ~ 0) stays opaque.
    dark = (b > 30) & (b < 110) & (b - r > 20) & (b - g > 12) & (r + g + b < 240)
    alpha = np.where(bright | dark, 0, 255).astype("uint8")
    return Image.fromarray(np.dstack([rgb.astype("uint8"), alpha]), "RGBA")


def key(im, mode="green", tol=None):
    if mode == "blue":
        return key_blue(im, tol if tol is not None else 28)
    if mode == "green":
        return key_green(im, tol if tol is not None else 28)
    return key_magenta(im, tol if tol is not None else 60)


# --- cleanup -----------------------------------------------------------------

def drop_border_blobs(im, keep_frac=0.05):
    """Delete opaque components that touch the image border.

    The model likes to draw a dark 'card' frame around the scene; that frame is a
    ring closing on the image edges, while the character floats inside. Once the
    flat background is keyed out, dropping every edge-touching component removes
    that frame (and any border-cropped clip) by geometry — no colour-threshold
    gymnastics, so it works for any key colour. Among the surviving interior
    blobs only the significant ones are kept (a tiny leftover speck is dropped).
    """
    from scipy import ndimage
    a = np.asarray(im)[:, :, 3] > 0
    lab, n = ndimage.label(a)
    if n == 0:
        return im
    edge = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
    edge.discard(0)
    inner = [i for i in range(1, n + 1) if i not in edge]
    if not inner:
        return im
    sizes = ndimage.sum(a, lab, inner)
    keep = [i for i, s in zip(inner, sizes) if s >= keep_frac * float(np.max(sizes))]
    out = np.asarray(im).copy()
    out[..., 3] = np.where(np.isin(lab, keep), out[..., 3], 0)
    return Image.fromarray(out, "RGBA")


def kill_side_clutter(im, upper=0.55, margin=14):
    """Below the torso, drop opaque pixels outside the body's own column band:
    props beside the feet weld to the character via the ground band."""
    a = np.asarray(im)[:, :, 3] > 0
    h = a.shape[0]
    xs = np.where(a[: int(h * upper)].any(axis=0))[0]
    if len(xs) == 0:
        return im
    x0, x1 = xs.min() - margin, xs.max() + margin
    mask = a.copy()
    y = int(h * upper)
    mask[y:, : max(0, x0)] = False
    mask[y:, x1 + 1:] = False
    out = np.asarray(im).copy()
    out[..., 3] = np.where(mask, out[..., 3], 0)
    return Image.fromarray(out, "RGBA")


def prune_small(im, iters=3, keep_frac=0.05):
    """Keep only the body (and gear); drop stray props. Erode so a thin ground
    pixel does not weld a prop onto the character, then grow the seed back."""
    from scipy import ndimage
    a = np.asarray(im)[:, :, 3] > 0
    er = ndimage.binary_erosion(a, iterations=iters)
    lab, n = ndimage.label(er)
    if n == 0:
        return im
    sizes = ndimage.sum(er, lab, range(1, n + 1))
    seed = np.isin(lab, [i + 1 for i, s in enumerate(sizes) if s >= keep_frac * sizes.max()])
    keep = ndimage.binary_dilation(seed, iterations=iters) & a
    out = np.asarray(im).copy()
    out[..., 3] = np.where(keep, out[..., 3], 0)
    return Image.fromarray(out, "RGBA")


def drop_ground(im):
    """Crop the ground band the model drew along the bottom."""
    a = np.asarray(im)[:, :, 3] > 0
    ys, xs = np.where(a)
    if len(ys) == 0:
        return im
    bw = xs.max() - xs.min() + 1
    rows = a.sum(axis=1)
    y = a.shape[0] - 1
    while y >= 0 and rows[y] > 0.85 * bw:
        y -= 1
    top = y + 1
    if top >= a.shape[0] - 2:
        return im
    return im.crop((0, 0, im.width, top))


def trim_grass(im, frac=0.55):
    """Shave a mostly olive-green ground line off the feet (green-key job)."""
    a = np.asarray(im).astype(int)
    al = a[:, :, 3] > 0
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    grassy = al & (g > b + 8) & (g + 14 >= r) & (g > 55)
    h = a.shape[0]
    floor = int(h * 0.60)
    y = h - 1
    while y >= floor:
        al_row = al[y]
        if not al_row.any():
            y -= 1
            continue
        if grassy[y].sum() >= frac * al_row.sum():
            y -= 1
        else:
            break
    top = y + 1
    if top >= h - 2 or top < floor:
        return im
    return im.crop((0, 0, im.width, top))


def clean(im, mode="green", crop_x=0.0, trim=False, frame=False):
    """Key + cleanup: key the backdrop, drop the model's card frame (edge blobs),
    then prune props / ground. `trim` (olive-ground shave) is off by default — a
    green key already removes a green ground, and trimming risks eating an olive
    costume, so only enable it for legacy green-keyed assets.

    `frame=True` is the i2v-frame path. There the master already carried the flat
    key into the video, so the ONLY thing to remove is the backdrop — and the
    animated character legitimately fills or crops the frame (an arm reaching the
    edge). The geometric steps (drop_border_blobs etc.) delete any edge-touching
    component, i.e. they erase the whole character, so we key and stop."""
    if crop_x > 0:
        m = int(im.width * crop_x)
        im = im.crop((m, 0, im.width - m, im.height))
    keyed = key(im, mode)
    if frame:
        return keyed
    out = drop_border_blobs(keyed)
    out = prune_small(kill_side_clutter(out))
    out = drop_ground(out)
    return trim_grass(out) if trim else out


# --- scale / registration ----------------------------------------------------

def body_height(im, width_frac=0.30):
    """Feet-to-head height, ignoring a thin raised weapon tip."""
    a = np.asarray(im)[:, :, 3] > 0
    ys, xs = np.where(a)
    if len(ys) == 0:
        return im.height
    bw = xs.max() - xs.min() + 1
    rows = a.sum(axis=1)
    for y in range(ys.min(), ys.max() + 1):
        if rows[y] >= width_frac * bw:
            return int(ys.max() - y + 1)
    return int(ys.max() - ys.min() + 1)


def body_metrics(im, anchor_lower=0.30):
    """bbox height and the leg-column centre-x, both in source pixels."""
    a = np.asarray(im)[:, :, 3] > 0
    ys, xs = np.where(a)
    if len(ys) == 0:
        return im.height, im.width / 2, im.height
    y0, y1 = ys.min(), ys.max()
    yb = y1 - int((y1 - y0 + 1) * anchor_lower)
    sxs = np.where(a[yb:y1 + 1].any(axis=0))[0]
    cx = (sxs.min() + sxs.max()) / 2 if len(sxs) else (xs.min() + xs.max()) / 2
    return y1 - y0 + 1, cx, y1


def derive(im, canvas, scale, feet_y, palette, sat=1.0, contrast=1.0, anchor_lower=0.30):
    """Render one cleaned frame onto the fixed canvas at a shared scale."""
    cw, ch = canvas
    body_h, cx_src, y1 = body_metrics(im, anchor_lower)
    nw = max(1, round(im.width * scale))
    nh = max(1, round(body_h * scale))
    small = im.resize((nw, nh), Image.BOX)
    rgb = small.convert("RGB")
    if sat != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(sat)
    if contrast != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
    rgb = rgb.quantize(palette=palette, dither=Image.NONE).convert("RGB")
    alpha = small.getchannel("A").point(lambda v: 255 if v >= 120 else 0)
    rgb.putalpha(alpha)
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    out.alpha_composite(rgb, (round(cw / 2 - cx_src * scale), feet_y - nh))
    return out


def _shared_palette(cleaned, scale, colors, anchor_lower):
    pixels = []
    for im in cleaned:
        body_h, _, _ = body_metrics(im, anchor_lower)
        small = im.resize((max(1, round(im.width * scale)), max(1, round(body_h * scale))), Image.BOX)
        pixels.append(np.asarray(small.convert("RGB")).reshape(-1, 3))
    allpx = np.concatenate(pixels, axis=0)
    return Image.fromarray(allpx.reshape(1, -1, 3), "RGB").quantize(colors=colors, method=Image.MEDIANCUT)


def register_frames(frames, canvas, colors=48, sat=1.0, contrast=1.0,
                    content_h=None, feet_y=None, body_target=0, ref=0,
                    anchor_lower=0.30, mirror=False, key_mode="green", frame=False):
    """Raw frames -> key/clean -> one shared scale + one shared palette -> canvases.

    `content_h` is the target body height in output px (default ~90% of canvas
    height); `feet_y` is the baseline row (default 4px above the bottom).
    """
    cw, ch = canvas
    if feet_y is None:
        feet_y = ch - 4
    if content_h is None:
        content_h = round(ch * 0.90)
    cleaned = [clean(f, mode=key_mode, frame=frame) for f in frames]
    cleaned = [c for c in cleaned if c.getbbox()] or cleaned
    ref = min(ref, len(cleaned) - 1)
    if body_target > 0:
        scale = body_target / max(1, body_height(cleaned[ref]))
    else:
        ref_h, _, _ = body_metrics(cleaned[ref], anchor_lower)
        scale = content_h / max(1, ref_h)
    palette = _shared_palette(cleaned, scale, colors, anchor_lower)
    out = []
    for im in cleaned:
        f = derive(im, canvas, scale, feet_y, palette, sat, contrast, anchor_lower)
        if mirror:
            f = f.transpose(Image.FLIP_LEFT_RIGHT)
        out.append(f)
    return out


def slice_grid(im, cols, rows):
    """Equal-division slice, row-major — the prompt forbids gutters."""
    w, h = im.size
    out = []
    for r in range(rows):
        for c in range(cols):
            out.append(im.crop((round(c * w / cols), round(r * h / rows),
                                round((c + 1) * w / cols), round((r + 1) * h / rows))))
    return out


def figure_boxes(im, key_mode="green", close=2, keep_frac=0.25, pad=6):
    """Row-major bboxes of the significant interior blobs of a keyed figure grid.

    The model reliably floats cleanly-separated figures on the key colour, so
    each one is its own connected blob; the card frames / outlines / ground it
    also likes to draw are either keyed away or sit at the image edge. Instead of
    guessing a grid from transparent gutters — which breaks when a figure sits
    off-centre or the layout is uneven — we label the blobs, drop the edge and
    tiny ones, and return each remaining blob's bbox (padded). This finds the
    layout AND isolates the figure in one step. Row-major order is recovered by
    grouping blob centroids into rows (a y-gap) then sorting by x.

    Returns [(x0, y0, x1, y1)] in source pixels — the offsets are what makes a
    parts sheet reassemblable into its rest pose.
    """
    from scipy import ndimage
    keyed = key(im, key_mode)
    a = np.asarray(keyed)[:, :, 3] > 0
    if close:
        a = ndimage.binary_closing(a, iterations=close)
    lab, n = ndimage.label(a)
    if n == 0:
        return []
    edge = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
    edge.discard(0)
    idx = [i for i in range(1, n + 1) if i not in edge]
    if not idx:
        return []
    sizes = ndimage.sum(a, lab, idx)
    mx = float(np.max(sizes))
    idx = [i for i, s in zip(idx, sizes) if s >= keep_frac * mx]
    cents = ndimage.center_of_mass(a, lab, idx)            # (y, x) per kept blob
    objs = ndimage.find_objects(lab)
    order = sorted(range(len(idx)), key=lambda k: cents[k][0])
    thr = im.height * 0.10
    rows, ordered = [], []
    for k in order:
        if rows and cents[k][0] - cents[rows[-1][-1]][0] <= thr:
            rows[-1].append(k)
        else:
            rows.append([k])
    for row in rows:
        ordered += sorted(row, key=lambda k: cents[k][1])
    out = []
    for k in ordered:
        sl = objs[idx[k] - 1]
        y0, y1 = sl[0].start, sl[0].stop
        x0, x1 = sl[1].start, sl[1].stop
        out.append((max(0, x0 - pad), max(0, y0 - pad),
                    min(im.width, x1 + pad), min(im.height, y1 + pad)))
    return out


def extract_figures(im, key_mode="green", close=2, keep_frac=0.25, pad=6,
                    keep_bg=False):
    """Each figure of a keyed grid as its own tile, in reading order."""
    src = im if keep_bg else key(im, key_mode)   # keep_bg: raw crop, still on key
    return [src.crop(b) for b in figure_boxes(im, key_mode, close, keep_frac, pad)]


def grid_cell_boxes(im, cols, rows, key_mode="green", close=2, pad=6,
                    min_area_frac=5e-4):
    """Row-major bboxes of a grid whose cols x rows layout is KNOWN.

    figure_boxes assumes the blobs are uniformly sized and drops any blob under
    keep_frac of the LARGEST — fine for a pose grid, but a body-parts sheet has
    wildly different part sizes (a sleeves-out torso dwarfs a hand), so the
    relative cut silently deletes the small parts and the count check then fails
    ("drew 6 pieces, wanted 10"). Here the layout is fixed by the prompt
    (part_grid), so each blob is assigned to the cell its centroid falls in and
    the blobs of one cell are unioned: a part the model drew as two shapes still
    yields ONE tile, and only a genuinely empty cell goes missing (the caller
    raises). Edge-touching and sub-min_area specks (the odd 1px leftover) are
    dropped first, by an ABSOLUTE area floor rather than a relative one.
    """
    from scipy import ndimage
    a = np.asarray(key(im, key_mode))[:, :, 3] > 0
    if close:
        a = ndimage.binary_closing(a, iterations=close)
    lab, n = ndimage.label(a)
    if n == 0:
        return []
    edge = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
    edge.discard(0)
    min_area = min_area_frac * im.width * im.height
    objs = ndimage.find_objects(lab)
    cells = {}
    for i in range(1, n + 1):
        if i in edge:
            continue
        sl = objs[i - 1]
        if (sl[0].stop - sl[0].start) * (sl[1].stop - sl[1].start) < min_area:
            continue
        cy = (sl[0].start + sl[0].stop) / 2
        cx = (sl[1].start + sl[1].stop) / 2
        c = min(cols - 1, max(0, int(cx / (im.width / cols))))
        r = min(rows - 1, max(0, int(cy / (im.height / rows))))
        b = cells.get((r, c))
        if b is None:
            cells[(r, c)] = [sl[1].start, sl[0].start, sl[1].stop, sl[0].stop]
        else:
            b[0] = min(b[0], sl[1].start)
            b[1] = min(b[1], sl[0].start)
            b[2] = max(b[2], sl[1].stop)
            b[3] = max(b[3], sl[0].stop)
    out = []
    for r in range(rows):
        for c in range(cols):
            if (r, c) in cells:
                x0, y0, x1, y1 = cells[(r, c)]
                out.append((max(0, x0 - pad), max(0, y0 - pad),
                            min(im.width, x1 + pad), min(im.height, y1 + pad)))
    return out


def extract_parts(im, key_mode="green", close=2, keep_frac=0.25, pad=6,
                  keep_bg=False, cols=None, rows=None):
    """Each body part of a keyed parts sheet as (tile, (x0, y0)), reading order.

    The offset is the part's top-left in the generated sheet — i.e. where the
    part sits in the assembled rest pose — so pasting the tiles back at their
    offsets rebuilds the character, and a binder gets a rest offset for free.

    When the grid layout is known (cols and rows given) use grid_cell_boxes;
    otherwise fall back to the uniform-blob figure_boxes heuristic.
    """
    src = im if keep_bg else key(im, key_mode)
    if cols and rows:
        boxes = grid_cell_boxes(im, cols, rows, key_mode, close, pad)
    else:
        boxes = figure_boxes(im, key_mode, close, keep_frac, pad)
    return [(src.crop(b), (b[0], b[1])) for b in boxes]


def detect_grid(im, key_mode="green", thresh=0.10):
    """Count the character columns/rows the model ACTUALLY drew, by keying the
    grid and looking at the transparent gutters. The model does not reliably
    honour the requested {cols}x{rows} (it draws its own sheet layout), so
    slicing by the requested count misaligns every frame. Returns (cols, rows)."""
    a = np.asarray(key(im, key_mode))[:, :, 3] > 0

    def count(proj):
        # a separator is a WIDE low-content run that does not touch the image
        # border (edge margins are padding, not cell dividers); narrow dips
        # inside a character (between the legs) are ignored.
        min_gap = max(6, int(len(proj) * 0.04))
        lo = proj < thresh
        n, i, L = 0, 0, len(lo)
        while i < L:
            if lo[i]:
                j = i
                while j < L and lo[j]:
                    j += 1
                if (j - i) >= min_gap and i > 2 and j < L - 2:
                    n += 1
                i = j
            else:
                i += 1
        return max(1, n + 1)

    return count(a.mean(axis=0)), count(a.mean(axis=1))


def spill_check(tile, edge=2):
    """Fraction of each tile edge that is opaque — a proxy for crossing the cell
    boundary (the prompt demands clear margin on every side)."""
    a = np.asarray(tile.convert("RGBA"))[:, :, 3] > 0
    if a.size == 0:
        return 0.0
    border = np.concatenate([a[:edge].ravel(), a[-edge:].ravel(),
                             a[:, :edge].ravel(), a[:, -edge:].ravel()])
    return float(border.mean()) if border.size else 0.0
