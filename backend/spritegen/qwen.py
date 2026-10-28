#!/usr/bin/env python3
"""Self-contained client for the MaaS Qwen gateway (Alibaba Model Studio / DashScope shape).

Text and image models use the OpenAI-compatible surface (image is synchronous);
video uses the native async surface with task polling. The key is read from
$QWEN_MAAS_KEY (never hard-coded, never logged). Vendored from the godot
project's gw.py so this app stands alone.

    export QWEN_MAAS_KEY=sk-...
    python -m spritegen.qwen chat   --prompt "hi"
    python -m spritegen.qwen image  --prompt-file p.txt --out-dir out/img
    python -m spritegen.qwen i2v    --img-url https://...png --prompt-file m.txt --out-dir out/anim
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from .config import load_env

load_env()                                   # pick up <root>/.env before reading BASE

BASE = os.environ.get("QWEN_MAAS_BASE", "https://maas.qianwenaiapi.com")
IMG_COMPAT_PATH = "/compatible-mode/v1/images/generations"   # sync image surface
VID_PATH = "/api/v1/services/aigc/video-generation/video-synthesis"
CHAT_PATH = "/compatible-mode/v1/chat/completions"
TASK_PATH = "/api/v1/tasks/"
IMG_MODEL = "qwen-image-2.1-pro"    # native `qwen-image` was retired by the gateway
VID_MODEL = "wan2.2-i2v-plus"
LLM_MODEL = "qwen3.8-flash"


class QwenError(RuntimeError):
    """A non-retryable gateway error. Unlike the old SystemExit, this is an
    Exception, so a job runner's `except Exception` records it instead of
    letting the worker die and leaving the job stuck 'running' forever."""


def _key():
    k = os.environ.get("QWEN_MAAS_KEY")
    if not k:
        raise QwenError("set QWEN_MAAS_KEY")
    return k


def _req(path, body=None, method=None, timeout=60, async_header=True):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    r.add_header("Authorization", "Bearer " + _key())
    r.add_header("content-type", "application/json")
    if data is not None and async_header:      # submit-only flag; on GET it 403s
        r.add_header("X-DashScope-Async", "enable")
    for attempt in range(6):
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            if e.code == 429:
                wait = 15 * (attempt + 1)
                print(f"  429 rate-limited, waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise QwenError(f"error: HTTP {e.code}: {raw[:400]}")
    raise QwenError("error: still rate-limited after retries")


def poll(task_id, interval=8, limit=600):
    t0 = time.time()
    while time.time() - t0 < limit:
        d = _req(TASK_PATH + task_id)
        o = d.get("output", {})
        st = o.get("task_status")
        print(f"  {st} ({int(time.time()-t0)}s)", file=sys.stderr)
        if st == "SUCCEEDED":
            return o
        if st in ("FAILED", "CANCELED", "UNKNOWN"):
            raise RuntimeError(f"task {st}: {o.get('message') or d.get('message')}")
        time.sleep(interval)
    raise RuntimeError("task timed out")


def _download(url, path):
    urllib.request.urlretrieve(url, path)
    return path


def chat(messages, model=LLM_MODEL, temperature=0.7, timeout=120):
    """One non-streaming chat turn. Returns the assistant text."""
    body = {"model": model, "messages": messages}
    if temperature is not None:
        body["temperature"] = temperature
    d = _req(CHAT_PATH, body, async_header=False, timeout=timeout)
    try:
        return d["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise RuntimeError(f"no message in chat response: {json.dumps(d)[:400]}")


def generate_image(prompt, out_dir, size="1328*1328", n=1, tag="generated"):
    """Text2image on the OpenAI-compatible image surface (synchronous).

    The gateway used to serve text2image on the native async surface with
    `qwen-image`; that model has since been retired, and the current image
    models are only reachable here. The call answers synchronously with one
    URL per image (no async header, no task polling). `size` is "W*H" or
    "WxH". Returns (paths, urls).
    """
    body = {"model": IMG_MODEL, "prompt": prompt, "n": n,
            "size": str(size).replace("*", "x")}
    d = _req(IMG_COMPAT_PATH, body, async_header=False, timeout=180)
    os.makedirs(out_dir, exist_ok=True)
    paths, urls = [], []
    for i, res in enumerate(d.get("data") or []):
        url = res.get("url")
        if not url:
            continue
        p = os.path.join(out_dir, f"{tag}_{i}.png")
        _download(url, p)
        paths.append(os.path.abspath(p))
        urls.append(url)
    if not paths:
        raise QwenError(f"image model returned no images: {json.dumps(d)[:300]}")
    open(os.path.join(out_dir, "job.json"), "w").write(json.dumps(
        {"model": IMG_MODEL, "kind": "image", "prompt": prompt, "size": body["size"],
         "files": paths, "sourceUrls": urls}, indent=1))
    return paths, urls


def generate_video(img_url, prompt, out_dir, resolution="480P", duration=5):
    """Submit an image2video task, download the clip. Returns the mp4 path."""
    body = {"model": VID_MODEL,
            "input": {"prompt": prompt, "img_url": img_url},
            "parameters": {"resolution": resolution, "duration": duration}}
    o = _req(VID_PATH, body)
    tid = o["output"]["task_id"]
    print(f"task {tid}", file=sys.stderr)
    return _finish_video(tid, out_dir, {"resolution": resolution, "prompt": prompt})


def _finish_video(tid, out_dir, meta):
    o = poll(tid)
    os.makedirs(out_dir, exist_ok=True)
    open(os.path.join(out_dir, "job.json"), "w").write(json.dumps(
        dict({"task_id": tid, "model": VID_MODEL, "kind": "video"}, **meta), indent=1))
    url = o.get("video_url")
    if not url:
        raise RuntimeError(f"no video_url in {json.dumps(o)[:300]}")
    p = os.path.join(out_dir, "generated.mp4")
    _download(url, p)
    return p


# --- CLI ---------------------------------------------------------------------

def _cmd_chat(a):
    prompt = open(a.prompt_file).read().strip() if a.prompt_file else a.prompt
    if not prompt:
        sys.exit("error: pass --prompt or --prompt-file")
    messages = []
    if a.system:
        messages.append({"role": "system", "content": a.system})
    messages.append({"role": "user", "content": prompt})
    print(chat(messages, model=a.model, temperature=a.temperature))


def _cmd_image(a):
    prompt = open(a.prompt_file).read().strip()
    paths, _ = generate_image(prompt, a.out_dir, size=a.size, n=a.n)
    for p in paths:
        print(p)


def _cmd_i2v(a):
    prompt = open(a.prompt_file).read().strip()
    print(generate_video(a.img_url, prompt, a.out_dir,
                         resolution=a.resolution, duration=a.duration))


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("chat")
    pc.add_argument("--prompt")
    pc.add_argument("--prompt-file")
    pc.add_argument("--system")
    pc.add_argument("--model", default=LLM_MODEL)
    pc.add_argument("--temperature", type=float, default=0.7)
    pc.set_defaults(fn=_cmd_chat)

    pi = sub.add_parser("image")
    pi.add_argument("--prompt-file", required=True)
    pi.add_argument("--size", default="1328*1328")
    pi.add_argument("--n", type=int, default=1)
    pi.add_argument("--out-dir", required=True)
    pi.set_defaults(fn=_cmd_image)

    pv = sub.add_parser("i2v")
    pv.add_argument("--img-url", required=True)
    pv.add_argument("--prompt-file", required=True)
    pv.add_argument("--resolution", default="480P")
    pv.add_argument("--duration", type=int, default=5)
    pv.add_argument("--out-dir", required=True)
    pv.set_defaults(fn=_cmd_i2v)

    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except QwenError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
