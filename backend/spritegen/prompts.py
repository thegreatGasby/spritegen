#!/usr/bin/env python3
"""Prompt library and parameter resolution for the sprite pipeline.

Templates (not the LLM) own the strict contract: the flat green key, the
equal-grid layout, and the in-place/locked-camera motion clauses. A small LLM
call only classifies + translates a free-text request into structured fields.
"""
import json
import re

# Flat backdrop produced for chroma-keying. The model renders green and blue
# faithfully, so the USER picks whichever is NOT the character's dominant colour:
# for a vague prompt the model tends to echo the backdrop hue into the costume,
# and a costume drawn in the key colour gets keyed out (holes). Magenta is kept
# only for older assets — the model renders "magenta" as an off hot-pink the key
# misses, and its drawn card frame shows up as a border blob we drop geometrically.
KEYS = {
    "green": {"hex": "#00FF00", "word": "green"},
    "blue":  {"hex": "#0000FF", "word": "blue"},
}
KEY = KEYS["green"]["hex"]        # default backdrop

STYLES = {
    "pixel16":         {"zh": "16-bit 像素",  "family": "pixel",
                        "en": "16-bit era pixel art: chunky readable pixels, a tight limited palette, a clean dark outline, dithered shading, no anti-aliasing, no blur"},
    "retro":           {"zh": "复古掌机",     "family": "pixel",
                        "en": "retro handheld pixel art: very few colours, big square pixels, simple flat shading, crisp pixels"},
    "farm":            {"zh": "田园像素",     "family": "pixel",
                        "en": "cozy farm-life pixel art: warm earthy palette, soft pixels, gentle shading"},
    "dark":            {"zh": "暗黑像素",     "family": "pixel",
                        "en": "dark fantasy pixel art: moody desaturated palette, strong shadows, gritty pixels"},
    "cute":            {"zh": "可爱像素",     "family": "pixel",
                        "en": "cute chibi pixel art: rounded friendly shapes, bright pastel palette, simple shading"},
    "modern_cartoon":  {"zh": "现代卡通",     "family": "illustration",
                        "en": "modern 2D cartoon: bold clean shapes, smooth flat shading, thick outline, vibrant palette"},
    "classic_cartoon": {"zh": "经典卡通",     "family": "illustration",
                        "en": "classic mid-century cartoon: rounded rubbery shapes, painterly flat colour, expressive"},
    "shonen":          {"zh": "热血动漫",     "family": "illustration",
                        "en": "shonen anime: dynamic cel shading, strong outlines, high-contrast colours"},
    "shoujo":          {"zh": "少女动漫",     "family": "illustration",
                        "en": "shoujo anime: soft lineart, delicate pastel palette, sparkle highlights"},
    "chibi":           {"zh": "Q 版",         "family": "illustration",
                        "en": "chibi style: oversized head, tiny body, simple rounded shapes, cute"},
    "flat":            {"zh": "扁平矢量",     "family": "illustration",
                        "en": "flat vector illustration: pure flat colours, no gradients, geometric shapes, minimal outline"},
    "cartoon3d":       {"zh": "3D 卡通",      "family": "illustration",
                        "en": "stylised 3D cartoon render: soft rounded forms, smooth shading, subtle rim light"},
}

VIEWS = {
    "side":          {"zh": "横版",     "en": "in a strict side view (profile)"},
    "front":         {"zh": "正面",     "en": "in a strict front view"},
    "back":          {"zh": "背面",     "en": "in a strict back view"},
    "three_quarter": {"zh": "四分之三", "en": "in a three-quarter view"},
}

FACINGS = {
    "right": {"zh": "朝右", "en": "to the right"},
    "left":  {"zh": "朝左", "en": "to the left"},
}

# Per action: Chinese label, a grid-cell cycle description, and the i2v clause.
ACTIONS = {
    "idle":   {"zh": "待机",
               "grid": "a looping idle: subtle breathing and a slight weight shift",
               "motion": "stands in place in a ready stance as a looping idle: slow, clearly visible breathing with the chest and shoulders rising and falling, a slight bob in the knees, loose parts stirring gently. No steps, no attack, no gestures. The feet stay planted on the same ground line."},
    "walk":   {"zh": "行走",
               "grid": "a walk cycle: alternating contact and passing poses of a steady walk",
               "motion": "walks in place like a looping walk cycle on a treadmill: clearly alternating left and right steps with heel contact, a bent-knee passing pose and toe-off, arms swinging opposite to the legs. The character never moves across the frame; one foot is always on the same ground line."},
    "run":    {"zh": "奔跑",
               "grid": "a run cycle: contact, down, passing and a brief flight phase",
               "motion": "runs in place like a looping run cycle: a fast, determined sprint with the upper body leaning forward, knees pumping and a brief flight phase. The character never moves across the frame; one foot returns to the same ground line."},
    "jump":   {"zh": "跳跃",
               "grid": "a single jump: crouch, take-off, airborne tuck, landing",
               "motion": "performs a single jump in place: a short crouch, an upward leap with the body stretched, a brief airborne tuck, then landing back on the same spot and ground line."},
    "attack": {"zh": "攻击",
               "grid": "a single attack: wind-up, strike, brief hold, recovery",
               "motion": "winds up briefly, then performs ONE fast, powerful attack with its weapon or fists at chest height, holds the strike for a brief moment, then returns to the same ready stance. Only one strike: no second hit, no spin, no jump."},
    "cast":   {"zh": "施法",
               "grid": "a single spell cast: raise, glow, release, recovery",
               "motion": "raises a hand and performs ONE spell-cast gesture with a brief glow, then returns to the same ready stance. Only one cast."},
    "hurt":   {"zh": "受击",
               "grid": "a single hit reaction: recoil and recover",
               "motion": "takes one hit: the head and torso jerk back briefly with a pained recoil, then settle back toward the ready stance. No knock-back across the frame."},
    "death":  {"zh": "死亡",
               "grid": "a fall: stagger, drop to the knees, collapse to the ground",
               "motion": "collapses: staggers, drops to the knees and falls to the ground, ending motionless. One continuous fall; no other action."},
    "fly":    {"zh": "飞行",
               "grid": "a flight loop: wings up and wings down beats",
               "motion": "hovers in place, flapping its wings or propulsion in a looping flight idle, gently bobbing while staying centred in the frame."},
}

# Skeletal-cutout rigs. This is the "parts" feature: instead of N poses of one
# character, the model draws N SEPARATE BODY PARTS of one character, one per grid
# cell, in the order listed here. The cell POSITION is the part's name — the grid
# contract forbids text, so nothing the model writes can be trusted as a label.
#
# Two choices are load-bearing:
#   * `view` is pinned to front. In a side view the two arms occlude each other,
#     so there is nothing coherent to cut apart.
#   * `pose` is an A-pose, NOT an I-pose. In an I-pose the arms lie against the
#     torso and blob segmentation welds arm+torso into one piece — the single
#     most likely way this feature fails. A-pose is also the industry rest pose.
#
# Parts are cut at the joint they rotate about, and "left"/"right" mean the side
# of the IMAGE (unambiguous for a front view).
RIGS = {
    "humanoid": {
        "zh": "人形 · 10 件（含肘/膝）",
        "view": "front",
        "pose": ("a relaxed A-pose: arms angled about 30 degrees away from the "
                 "torso and hanging straight down, legs slightly apart"),
        "parts": [
            ("head",        "the head and neck, cut off at the base of the neck"),
            ("torso",       "the torso, shoulders and hips as one piece"),
            ("upper_arm_l", "the arm on the LEFT of the image, cut at the shoulder and at the elbow, upper arm only"),
            ("upper_arm_r", "the arm on the RIGHT of the image, cut at the shoulder and at the elbow, upper arm only"),
            ("forearm_l",   "the forearm and hand on the LEFT of the image, as ONE piece, cut at the elbow"),
            ("forearm_r",   "the forearm and hand on the RIGHT of the image, as ONE piece, cut at the elbow"),
            ("thigh_l",     "the leg on the LEFT of the image, cut at the hip and at the knee, thigh only"),
            ("thigh_r",     "the leg on the RIGHT of the image, cut at the hip and at the knee, thigh only"),
            ("shin_l",      "the lower leg and foot on the LEFT of the image, as ONE piece, cut at the knee"),
            ("shin_r",      "the lower leg and foot on the RIGHT of the image, as ONE piece, cut at the knee"),
        ],
    },
}

# Generation grid (cols, rows) for image mode — chosen for squarish cells.
GEN_GRID = {6: (3, 2), 8: (4, 2), 12: (4, 3)}
# Part-grid layout per part count. Wide rows beat a tall sheet on this gateway.
PART_GRID = {6: (3, 2), 8: (4, 2), 10: (5, 2), 12: (4, 3)}
# Output sheet layout (cols, rows) — mirrors the site: 6/8 in a row, 12 as 4x3.
SHEET_LAYOUT = {6: (6, 1), 8: (8, 1), 12: (4, 3)}

FRAME_CHOICES = [6, 8, 12]
CELL_CHOICES = ["HD", 16, 32, 64, 96, 128]

# The shared "N equal cells on one flat key field" contract. Rules 1/2/3/5/6 are
# the same for a pose cycle and for a parts sheet; only what fills the cells
# (`fill`), what the cells contain (`noun`) and the scale clause differ — so the
# contract lives here once instead of being forked per use.
_GRID_FILL_CYCLE = "consecutive poses of ONE continuous cycle"
_GRID_SCALE_BODY = (
    "Keep ONE anatomical scale and ONE shared registration origin: each "
    "character sits in the middle 70% of its cell with clear {word} on every "
    "side, no body part touching or crossing a cell boundary."
)
# Parts must be drawn the way they sit on the assembled character, or the
# assembled rest pose (and every rest offset derived from it) is meaningless.
_GRID_SCALE_PART = (
    "Draw every part at the SAME scale and in the SAME orientation it has on the "
    "assembled A-posed character, each in the middle 80% of its cell with clear "
    "{word} on every side, no part touching or crossing a cell boundary."
)


def _size_for(cols, rows):
    """A Qwen-image size string whose aspect matches a cols x rows grid."""
    base, cap = 1328, 1664
    w = base * cols / rows if cols >= rows else base
    h = base if cols >= rows else base * rows / cols
    s = min(1.0, cap / max(w, h))                 # scale both sides, keep aspect
    return f"{round(w * s / 32) * 32}*{round(h * s / 32) * 32}"


def _grid_rules(cols, rows, hexc, word, fill=_GRID_FILL_CYCLE, noun="character",
                scale=None, gap=""):
    """The strict "N equal cells, no frame, one flat key field" contract.

    `fill` is what goes in the cells (a pose cycle, or the ordered part list);
    `noun`, `scale` and `gap` let the parts sheet tighten the wording — parts
    must not touch each other or segmentation welds them into one piece — without
    forking the contract.
    """
    if scale is None:
        scale = _GRID_SCALE_BODY.format(word=word)
    return (
        f"ABSOLUTE RULES: 1. EXACTLY {cols} columns and {rows} rows of EQUAL cells "
        f"({cols * rows} cells total), filled left-to-right, top-to-bottom with "
        f"{fill}. 2. There is NO box, NO panel, NO tile, NO card, NO frame, NO "
        f"border, NO divider, NO gutter and NO rounded rectangle anywhere, and NO "
        f"background shape of any kind sits behind any {noun}. 3. ONE single "
        f"continuous flat pure {word} {hexc} field fills the ENTIRE image and "
        f"stays unbroken around and between the {noun}s — every {noun} floats "
        f"directly on that {word}{gap}. 4. {scale} 5. No shadow, no ground, no "
        f"floor line, no glow. 6. No text, letters, numbers, logos or watermark."
    )


_BAN = "No text, no letters, no numbers, no logo, no watermark."


def _style(style):
    return STYLES.get(style, STYLES["pixel16"])["en"]


def _key(key_mode):
    """(hex, word) for the chosen chroma-key backdrop; green is the default."""
    k = KEYS.get(key_mode, KEYS["green"])
    return k["hex"], k["word"]


def render_master(subject, style="pixel16", view="side", facing="right",
                  key_mode="green"):
    """One square master still on a flat key-coloured backdrop."""
    hexc, word = _key(key_mode)
    return (
        f"Create exactly ONE square image (1024x1024). Subject: {subject}. "
        f"Style: {_style(style)}. Show the FULL body from head to feet, "
        f"{VIEWS.get(view, VIEWS['side'])['en']}, facing {FACINGS.get(facing, FACINGS['right'])['en']}, "
        f"in a neutral standing pose. Centre the character, filling about 80% of the "
        f"frame height with a clear {word} margin on all sides. Background: solid "
        f"flat pure {word} {hexc} everywhere outside the character — no shadow, no "
        f"ground, no glow, no frame, no border. Crisp sharp edges with a clean dark "
        f"outline. {_BAN}."
    )


def render_grid(subject, action="idle", style="pixel16", view="side",
                facing="right", frames=8, key_mode="green"):
    """One image containing `frames` equally spaced poses of a cycle."""
    cols, rows = GEN_GRID.get(frames, (4, 2))
    hexc, word = _key(key_mode)
    rules = _grid_rules(cols, rows, hexc, word)
    return (
        f"One image showing {frames} copies of ONE character ({subject}), laid out "
        f"in {cols} columns and {rows} rows, all floating directly on ONE single "
        f"continuous flat pure {word} {hexc} background — not a sticker sheet, not "
        f"panels, just the characters on {word}. Style: {_style(style)}. "
        f"Each copy shows the FULL body {VIEWS.get(view, VIEWS['side'])['en']}, "
        f"facing {FACINGS.get(facing, FACINGS['right'])['en']}. The {frames} copies "
        f"show {ACTIONS.get(action, ACTIONS['idle'])['grid']}, evenly spaced around "
        f"one loop. {rules} {_BAN}."
    )


def render_views(subject, style="pixel16", facing="right", views=("front", "side"),
                 key_mode="green"):
    """ONE image: the SAME character in several views, left to right. A single
    generation keeps the identity coherent across views (the model can't be fed a
    reference image, so a turnaround is the only way to anchor cross-view)."""
    order = ", ".join(VIEWS.get(v, VIEWS["side"])["en"] for v in views)
    hexc, word = _key(key_mode)
    n = len(views)
    rules = _grid_rules(n, 1, hexc, word)
    return (
        f"One image showing {n} copies of ONE character ({subject}), laid out in "
        f"{n} columns and 1 row, all floating directly on ONE single continuous flat "
        f"pure {word} {hexc} background — not a sticker sheet, not panels, just the "
        f"characters on {word}. Style: {_style(style)}. Every copy is the EXACT same "
        f"character — identical face, hairstyle, costume, colours and proportions — "
        f"in the same neutral standing pose, full body from head to feet, facing "
        f"{FACINGS.get(facing, FACINGS['right'])['en']}. Left to right the {n} copies "
        f"show, in order: {order}. {rules} {_BAN}"
    )


def render_parts(subject, rig="humanoid", style="pixel16", view="front",
                 facing="right", key_mode="green"):
    """ONE image: every body part of ONE character, one per grid cell.

    Same contract as render_grid/render_views (equal cells, no frame, one flat
    key field) with two substitutions: what fills the cells is the ordered part
    list, and scale/orientation are locked to the assembled rest pose. Cell order
    IS the part name, so downstream segmentation needs no labelling model.
    """
    spec = RIGS.get(rig, RIGS["humanoid"])
    parts = spec["parts"]
    n = len(parts)
    cols, rows = part_grid(n)
    hexc, word = _key(key_mode)
    listing = "; ".join(f"cell {i + 1}: {en}" for i, (_, en) in enumerate(parts))
    rules = _grid_rules(
        cols, rows, hexc, word,
        fill=f"exactly ONE body part of ONE character per cell, in this precise "
             f"order — {listing}",
        noun="body part",
        scale=_GRID_SCALE_PART.format(word=word),
        gap=f", with a wide clear {word} gap (at least a fifth of a cell wide) "
            f"between any two parts so that no two of them touch or connect")
    return (
        f"One image showing {n} separate body parts of ONE character ({subject}), "
        f"laid out in {cols} columns and {rows} rows, all floating directly on ONE "
        f"single continuous flat pure {word} {hexc} background — not a sticker "
        f"sheet, not panels, just the parts on {word}. Style: {_style(style)}. "
        f"Every part belongs to the SAME character — identical face, costume, "
        f"palette, outline and shading — and is drawn exactly as it looks on that "
        f"character standing in {spec['pose']}, with each part hanging down from "
        f"its joint. Each part is a clean, complete, closed shape cut at its "
        f"joint: not a rectangular crop, not a photograph fragment, no duplicate "
        f"of another cell. {rules} {_BAN}"
    )


def render_motion(subject, action="idle", style="pixel16", view="side",
                  facing="right", key_mode="green"):
    """i2v instruction: animate the given still, in place, on the same key."""
    hexc, word = _key(key_mode)
    return (
        f"Using the provided still as the ONLY reference, animate the SAME character: "
        f"identical identity, costume, palette and proportions. The character faces "
        f"{FACINGS.get(facing, FACINGS['right'])['en']} the entire time and never turns around. "
        f"{ACTIONS.get(action, ACTIONS['idle'])['motion']} "
        f"The clip starts AND ends in exactly the same pose as the still. Camera locked: "
        f"no zoom, no pan, no rotation; the character stays centred and in place. "
        f"Solid flat pure {word} {hexc} background for the whole shot — no shadow, no "
        f"ground, no glow, no frame. Keep the exact look, palette and proportions of the still. "
        f"Single continuous action."
    )


def grid_size(frames):
    """A Qwen-image size string whose aspect matches the generation grid."""
    return _size_for(*GEN_GRID.get(frames, (4, 2)))


def part_grid(n):
    """(cols, rows) holding exactly n parts, wide rather than tall.

    A part count not in PART_GRID still gets an EXACT layout, so the "exactly
    cols x rows cells" contract never has to be relaxed — add an entry instead
    when a hand-picked layout matters.
    """
    if n in PART_GRID:
        return PART_GRID[n]
    for rows in (2, 3):
        cols = -(-n // rows)
        if cols <= 6 and cols * rows == n:
            return cols, rows
    return n, 1


def parts_size(n):
    """A Qwen-image size string sized for the n-part grid."""
    return _size_for(*part_grid(n))


def views_size(n):
    """A Qwen-image size string for an n-across turnaround (wide, capped)."""
    base, cap = 1024, 1664
    w, h = base * max(1, n), base
    s = min(1.0, cap / max(w, h))
    return f"{round(w * s / 32) * 32}*{round(h * s / 32) * 32}"


# --- LLM classification ------------------------------------------------------

LLM_SYSTEM = (
    "You are a sprite-directing assistant. Given a short character description in "
    "ANY language, reply with ONLY a JSON object (no markdown, no prose) with keys:\n"
    "- subject: a short ENGLISH noun phrase describing the character and its key gear "
    "(e.g. 'a knight in gilded armour holding a sword'). No style words, no background.\n"
    "- style: one of " + ", ".join(STYLES) + "\n"
    "- view: one of " + ", ".join(VIEWS) + "\n"
    "- facing: one of " + ", ".join(FACINGS) + "\n"
    "- action: one of " + ", ".join(ACTIONS) + "\n"
    "Map any style named in the text to the nearest id. Defaults: style pixel16, "
    "view side, facing right, action idle."
)


def llm_messages(text, overrides=None):
    user = text
    if overrides:
        pinned = ", ".join(f"{k}={v}" for k, v in overrides.items() if v is not None)
        if pinned:
            user += f"\n(These are already fixed, echo them back: {pinned}.)"
    return [{"role": "system", "content": LLM_SYSTEM},
            {"role": "user", "content": user}]


def parse_llm_json(text):
    """Tolerant JSON extraction from an LLM reply."""
    if not text:
        return {}
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


def resolve(text, style=None, view=None, facing=None, action=None, subject=None):
    """Static fallback (no LLM): fill fields from explicit flags / defaults.

    `subject` falls back to the raw text — qwen-image reads Chinese fine.
    """
    return {
        "subject": subject or text,
        "style": style or "pixel16",
        "view": view or "side",
        "facing": facing or "right",
        "action": action or "idle",
    }


def presets():
    return {
        "styles": [{"id": k, "zh": v["zh"], "family": v["family"]} for k, v in STYLES.items()],
        "views": [{"id": k, "zh": v["zh"]} for k, v in VIEWS.items()],
        "facings": [{"id": k, "zh": v["zh"]} for k, v in FACINGS.items()],
        "actions": [{"id": k, "zh": v["zh"]} for k, v in ACTIONS.items()],
        "frames": FRAME_CHOICES,
        "cells": CELL_CHOICES,
        "keys": [{"id": "green", "zh": "绿底"}, {"id": "blue", "zh": "蓝底"}],
        "key": KEY,
        "rigs": [{"id": k, "zh": v["zh"], "view": v["view"],
                  "parts": [p[0] for p in v["parts"]]} for k, v in RIGS.items()],
    }
