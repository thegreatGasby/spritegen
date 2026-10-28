#!/usr/bin/env python3
"""Command-line entry for the sprite pipeline.

    python cli.py presets
    python cli.py character --prompt "持剑骑士，深色像素风，横版朝右" --out out/knight
    python cli.py action --character out/knight/character.json --action walk --mode video
    python cli.py make --prompt "粉红史莱姆，可爱像素风" --action idle --mode image --frames 8
    python cli.py parts --prompt "持剑骑士，深色像素风" --out out/parts/knight
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from spritegen import pipeline, prompts  # noqa: E402


def _add_common(p, default_key="magenta", keys=("magenta", "green")):
    p.add_argument("--no-llm", action="store_true", help="skip the LLM, use flags/defaults")
    p.add_argument("--llm-model", default="qwen3.8-flash")
    p.add_argument("--style", help="one of: " + ", ".join(prompts.STYLES))
    p.add_argument("--view", help="one of: " + ", ".join(prompts.VIEWS))
    p.add_argument("--facing", help="one of: " + ", ".join(prompts.FACINGS))
    p.add_argument("--key", dest="key_mode", default=default_key, choices=list(keys))


def _action_args(p):
    p.add_argument("--action", help="one of: " + ", ".join(prompts.ACTIONS))
    p.add_argument("--mode", default="image", choices=["video", "image"])
    p.add_argument("--frames", type=int, default=8, choices=prompts.FRAME_CHOICES)
    p.add_argument("--cell", default="64", help="HD or 16/32/64/96/128")
    p.add_argument("--colors", type=int, default=48)
    p.add_argument("--fps", type=int, default=6)
    p.add_argument("--resolution", default="480P")
    p.add_argument("--duration", type=int, default=5)
    p.add_argument("--sat", type=float, default=1.0)
    p.add_argument("--contrast", type=float, default=1.0)
    p.add_argument("--atlas", action="store_true")


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("presets")

    pc = sub.add_parser("character")
    pc.add_argument("--prompt", required=True)
    pc.add_argument("--out", required=True)
    _add_common(pc)

    pa = sub.add_parser("action")
    pa.add_argument("--character", help="path to character.json")
    pa.add_argument("--prompt")
    pa.add_argument("--out", required=True)
    _add_common(pa)
    _action_args(pa)

    pp = sub.add_parser("parts", help="cut-out body parts for 2D skeletal rigging")
    pp.add_argument("--prompt", required=True)
    pp.add_argument("--out", required=True)
    pp.add_argument("--rig", default="humanoid", choices=list(prompts.RIGS))
    # the parts matrix is green-on-nothing: green is the contract default, and
    # blue must be reachable (a green-costumed character needs the blue key)
    _add_common(pp, default_key="green", keys=("green", "blue"))

    pm = sub.add_parser("make")
    pm.add_argument("--prompt", required=True)
    pm.add_argument("--out", required=True)
    _add_common(pm)
    _action_args(pm)

    a = ap.parse_args(argv)

    if a.cmd == "presets":
        print(json.dumps(prompts.presets(), ensure_ascii=False, indent=1))
        return

    llm_kw = {"use_llm": not a.no_llm, "llm_model": a.llm_model}

    if a.cmd == "character":
        rec = pipeline.make_character(
            a.prompt, style=a.style, view=a.view, facing=a.facing,
            out_dir=a.out, **llm_kw)
        print(f"master -> {rec['master_path']}")
        return

    if a.cmd == "parts":
        res = pipeline.make_parts(a.prompt, rig=a.rig, style=a.style, view=a.view,
                                  facing=a.facing, key_mode=a.key_mode,
                                  out_dir=a.out, **llm_kw)
        print(f"rest pose -> {res['sheet']}  ({res['count']} parts)")
        for p in res["parts"]:
            print(f"  {p['name']:<12} {p['path']}  "
                  f"offset=({p['offset']['x']}, {p['offset']['y']})")
        return

    if a.cmd == "action":
        ch = json.load(open(a.character)) if a.character else None
        common = dict(action=a.action, mode=a.mode, frames=a.frames, cell=a.cell,
                      out_dir=a.out, key_mode=a.key_mode, colors=a.colors, fps=a.fps,
                      resolution=a.resolution, duration=a.duration, sat=a.sat,
                      contrast=a.contrast, atlas=a.atlas, **llm_kw)
        if ch:
            res = pipeline.make_action(character=ch, **common)
        else:
            res = pipeline.make(a.prompt, style=a.style, view=a.view, facing=a.facing, **common)
    else:  # make
        res = pipeline.make(a.prompt, action=a.action, mode=a.mode, frames=a.frames,
                            cell=a.cell, out_dir=a.out, style=a.style, view=a.view,
                            facing=a.facing, key_mode=a.key_mode, colors=a.colors,
                            fps=a.fps, resolution=a.resolution, duration=a.duration,
                            sat=a.sat, contrast=a.contrast, atlas=a.atlas, **llm_kw)

    print(f"sheet   -> {res['sheet']}  ({res['columns']}x{res['rows']} @ "
          f"{res['frameWidth']}px, {res['frameCount']} frames)")
    print(f"preview -> {res['preview']}")
    if res.get("qc"):
        print(f"qc(spill) -> {res['qc']}")


if __name__ == "__main__":
    main()
