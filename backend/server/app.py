#!/usr/bin/env python3
"""FastAPI service for the sprite workbench.

Generation takes ~1-2 min, so every request becomes a background job the UI
polls. In-flight jobs live in memory; finished artifacts are read back off disk
so the gallery survives restarts.
"""
import glob
import json
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
ROOT = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)

from spritegen import pipeline, prompts  # noqa: E402
from spritegen.config import load_env  # noqa: E402

ENV_FILE = load_env()
if not os.environ.get("QWEN_MAAS_KEY"):
    print("warn: QWEN_MAAS_KEY not set (put it in .env or export it)")

OUT = os.environ.get("SPRITEGEN_OUT", os.path.join(ROOT, "out"))
os.makedirs(OUT, exist_ok=True)
MAX_URL_AGE = 2 * 3600          # master URLs expire; regenerate a stale master

app = FastAPI(title="spritegen")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

JOBS = {}
LOCK = threading.Lock()
POOL = ThreadPoolExecutor(max_workers=2)


# --- models ------------------------------------------------------------------

class CharacterReq(BaseModel):
    prompt: str
    style: Optional[str] = None
    view: Optional[str] = None
    facing: Optional[str] = None
    key_mode: str = "green"
    use_llm: bool = True
    llm_model: str = "qwen3.8-flash"


class PartsReq(BaseModel):
    prompt: str
    rig: str = "humanoid"
    style: Optional[str] = None
    view: Optional[str] = None
    facing: Optional[str] = None
    key_mode: str = "green"
    use_llm: bool = True
    llm_model: str = "qwen3.8-flash"


class ActionReq(BaseModel):
    prompt: Optional[str] = None
    character_id: Optional[str] = None
    action: str = "idle"
    mode: str = "image"
    frames: int = 8
    cell: str = "64"
    key_mode: str = "green"
    colors: int = 48
    fps: int = 6
    resolution: str = "480P"
    duration: int = 5
    sat: float = 1.0
    contrast: float = 1.0
    atlas: bool = False
    use_llm: bool = True
    llm_model: str = "qwen3.8-flash"
    style: Optional[str] = None
    view: Optional[str] = None
    facing: Optional[str] = None


class BatchReq(BaseModel):
    prompt: str
    views: List[str] = ["side"]
    actions: List[str] = ["idle"]
    style: Optional[str] = None
    facing: Optional[str] = None
    mode: str = "image"
    frames: int = 8
    cell: str = "64"
    key_mode: str = "green"
    colors: int = 48
    fps: int = 6
    resolution: str = "480P"
    duration: int = 5
    sat: float = 1.0
    contrast: float = 1.0
    atlas: bool = False
    use_llm: bool = True
    llm_model: str = "qwen3.8-flash"


# --- helpers -----------------------------------------------------------------

def url_for(path):
    return "/files/" + os.path.relpath(path, OUT).replace(os.sep, "/")


def _raw_png(d):
    """Newest top-level PNG in a job dir — the model's raw output, written before
    any segmentation. Lets a failed job still show what the model drew."""
    ps = sorted(glob.glob(os.path.join(d, "*.png")), key=os.path.getmtime)
    return ps[-1] if ps else None


def _dump(model):
    """pydantic v1/v2 safe serialisation."""
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def action_result(res):
    """The JSON the UI needs for one finished action sheet (shared by single
    and batch runs)."""
    return {
        "kind": "action", "sheet": url_for(res["sheet"]),
        "preview": url_for(res["preview"]), "columns": res["columns"],
        "rows": res["rows"], "frameCount": res["frameCount"],
        "frameWidth": res["frameWidth"], "frameHeight": res["frameHeight"],
        "fps": res["fps"], "action": res["action"], "mode": res["mode"],
        "style": res["style"], "view": res["view"], "facing": res["facing"],
        "subject": res["subject"], "qc": res["qc"],
        "master": url_for(res["master_path"]),
    }


def parts_result(res):
    """The JSON the UI needs for one finished parts sheet."""
    return {
        "kind": "parts", "rig": res["rig"], "view": res["view"],
        "facing": res["facing"], "style": res["style"], "subject": res["subject"],
        "count": res["count"], "sheet": url_for(res["sheet"]),
        "material": url_for(res["material"]),
        "parts": [{"name": p["name"], "url": url_for(p["path"]),
                   "bbox": p["bbox"], "offset": p["offset"]} for p in res["parts"]],
    }


def new_job(kind, request):
    jid = uuid.uuid4().hex[:10]
    with LOCK:
        JOBS[jid] = {"id": jid, "kind": kind, "status": "queued", "stage": "queued",
                     "progress": 0.0, "created": time.time(), "request": request,
                     "result": None, "error": None, "logs": []}
    return jid


def job_logger(jid):
    def log(msg):
        with LOCK:
            if jid in JOBS:
                JOBS[jid]["logs"].append(str(msg))
                del JOBS[jid]["logs"][:-40]
    return log


def character_dir(cid):
    return os.path.join(OUT, "characters", cid)


def load_character(cid):
    p = os.path.join(character_dir(cid), "character.json")
    if not os.path.isfile(p):
        raise HTTPException(404, f"character {cid} not found")
    return json.load(open(p))


def fresh_character(cid, use_llm, llm_model, log):
    """Load a character, regenerating the master still if its URL has expired."""
    rec = load_character(cid)
    if rec.get("master_url") and time.time() - rec.get("created", 0) > MAX_URL_AGE:
        log("  master URL stale; regenerating master still")
        rec = pipeline.make_character(
            rec.get("text") or rec["fields"]["subject"], style=rec["fields"]["style"],
            view=rec["fields"]["view"], facing=rec["fields"]["facing"],
            key_mode=rec.get("key_mode", "green"),
            out_dir=character_dir(cid), use_llm=use_llm, llm_model=llm_model, log=log)
    return rec


# --- job runners -------------------------------------------------------------

def run_character(jid, req):
    log = job_logger(jid)
    cid = uuid.uuid4().hex[:8]
    out = character_dir(cid)

    def prog(fr, st):
        with LOCK:
            JOBS[jid].update(progress=round(fr, 3), stage=st)

    try:
        with LOCK:
            JOBS[jid].update(status="running", stage="parsing")
        rec = pipeline.make_character(req.prompt, style=req.style, view=req.view,
                                      facing=req.facing, key_mode=req.key_mode,
                                      out_dir=out, use_llm=req.use_llm,
                                      llm_model=req.llm_model, log=log, progress=prog)
        with LOCK:
            JOBS[jid].update(status="done", progress=1.0, stage="done",
                             result={"kind": "character", "character_id": cid,
                                     "master": url_for(rec["master_path"]),
                                     "fields": rec["fields"]})
    except Exception as e:                       # noqa: BLE001
        with LOCK:
            JOBS[jid].update(status="error", error=str(e), stage="error")


def run_parts(jid, req):
    log = job_logger(jid)
    out = os.path.join(OUT, "jobs", jid)

    def prog(fr, st):
        with LOCK:
            JOBS[jid].update(progress=round(fr, 3), stage=st)

    try:
        with LOCK:
            JOBS[jid].update(status="running", stage="parsing")
        res = pipeline.make_parts(req.prompt, rig=req.rig, style=req.style,
                                  view=req.view, facing=req.facing,
                                  key_mode=req.key_mode, out_dir=out,
                                  use_llm=req.use_llm, llm_model=req.llm_model,
                                  log=log, progress=prog)
        with LOCK:
            JOBS[jid].update(status="done", progress=1.0, stage="done",
                             result=parts_result(res))
    except Exception as e:                       # noqa: BLE001
        # the raw sheet was drawn before the failure — hand it to the UI so the
        # user can still preview (and re-roll) what the model produced.
        raw = _raw_png(out)
        with LOCK:
            JOBS[jid].update(status="error", error=str(e), stage="error",
                             result={"kind": "parts", "raw": url_for(raw),
                                     "material": url_for(raw), "count": 0,
                                     "parts": [], "rig": req.rig, "view": req.view,
                                     "facing": req.facing, "style": req.style,
                                     "subject": req.prompt, "error": str(e)}
                             if raw else None)


def run_action(jid, req):
    log = job_logger(jid)
    out = os.path.join(OUT, "jobs", jid)

    def prog(fr, st):
        with LOCK:
            JOBS[jid].update(progress=round(fr, 3), stage=st)

    try:
        with LOCK:
            JOBS[jid].update(status="running", stage="start")
        character = fresh_character(req.character_id, req.use_llm, req.llm_model, log) \
            if req.character_id else None
        res = pipeline.make_action(
            action=req.action, mode=req.mode, frames=req.frames, cell=req.cell,
            character=character, text=req.prompt, style=req.style, view=req.view,
            facing=req.facing, out_dir=out, use_llm=req.use_llm, llm_model=req.llm_model,
            key_mode=req.key_mode, colors=req.colors, fps=req.fps,
            resolution=req.resolution, duration=req.duration, sat=req.sat,
            contrast=req.contrast, atlas=req.atlas, log=log, progress=prog)
        with LOCK:
            JOBS[jid].update(status="done", progress=1.0, stage="done",
                             result=action_result(res))
    except Exception as e:                       # noqa: BLE001
        with LOCK:
            JOBS[jid].update(status="error", error=str(e), stage="error")


def run_batch(jid, req):
    """Many (view x action) sheets in one job. One master per view, shared by
    every action of that view (so a view's actions are the SAME character);
    results stream into JOBS[jid]['result']['items'] as they finish."""
    log = job_logger(jid)
    views = req.views or ["side"]
    actions = req.actions or ["idle"]
    total = len(views) * len(actions)
    root = os.path.join(OUT, "jobs", jid)

    def prog(fr, st):
        with LOCK:
            JOBS[jid].update(stage=st)

    try:
        with LOCK:
            JOBS[jid].update(status="running", stage="parsing", result={
                "kind": "batch", "items": [], "masters": [], "total": total, "done": 0})
        fields = pipeline.resolve_fields(req.prompt, req.style, None, req.facing,
                                         None, req.use_llm, req.llm_model, log)
        fs, ff = fields["style"], fields["facing"]
        log(f"  batch: {len(views)} views x {len(actions)} actions = {total}; "
            f"style={fs} facing={ff} mode={req.mode}")

        masters = {}

        def per_view_masters(vs):
            for v in vs:
                cid = uuid.uuid4().hex[:8]
                log(f"  [view {v}] master still")
                rec = pipeline.make_character(fields["subject"], style=fs, view=v,
                                              facing=ff, key_mode=req.key_mode,
                                              out_dir=character_dir(cid),
                                              use_llm=False, llm_model=req.llm_model,
                                              log=log, progress=prog)
                masters[v] = {"view": v, "character_id": cid,
                              "master": url_for(rec["master_path"])}
                with LOCK:
                    JOBS[jid]["result"]["masters"] = list(masters.values())

        if len(views) >= 2:                      # one turnaround -> consistent views
            try:
                recs = pipeline.make_views(fields["subject"], views, style=fs, facing=ff,
                                           key_mode=req.key_mode,
                                           char_root=os.path.join(OUT, "characters"),
                                           use_llm=False, llm_model=req.llm_model,
                                           log=log, progress=prog)
                for v, rec in zip(views, recs):
                    masters[v] = {"view": v,
                                  "character_id": os.path.basename(os.path.dirname(rec["master_path"])),
                                  "master": url_for(rec["master_path"])}
                with LOCK:
                    JOBS[jid]["result"]["masters"] = list(masters.values())
                log(f"  turnaround: {len(masters)} views from one generation (consistent)")
            except Exception as e:               # noqa: BLE001 - fall back, don't kill batch
                log(f"  turnaround failed ({e}); per-view masters instead")
                masters.clear()
        if not masters:
            per_view_masters(views)

        done = 0
        for v in views:
            rec = load_character(masters[v]["character_id"])
            for a in actions:
                label = f"{v}_{a}"
                log(f"  [{label}] {req.mode} generation")
                try:
                    res = pipeline.make_action(
                        action=a, mode=req.mode, frames=req.frames, cell=req.cell,
                        character=rec, out_dir=os.path.join(root, label),
                        use_llm=False, llm_model=req.llm_model, key_mode=req.key_mode,
                        colors=req.colors, fps=req.fps, resolution=req.resolution,
                        duration=req.duration, sat=req.sat, contrast=req.contrast,
                        atlas=req.atlas, log=log, progress=prog)
                    item = action_result(res)
                except Exception as e:            # noqa: BLE001 - keep the batch going
                    log(f"  [{label}] FAILED: {e}")
                    item = {"kind": "action", "view": v, "action": a, "error": str(e)}
                done += 1
                with LOCK:
                    JOBS[jid]["result"]["items"].append(item)
                    JOBS[jid]["result"]["done"] = done
                    JOBS[jid].update(progress=round(done / total, 3),
                                     stage=f"{done}/{total} 完成")
        with LOCK:
            JOBS[jid].update(status="done", progress=1.0, stage="done")
    except Exception as e:                       # noqa: BLE001
        with LOCK:
            JOBS[jid].update(status="error", error=str(e), stage="error")


# --- routes ------------------------------------------------------------------

@app.get("/api/presets")
def api_presets():
    return prompts.presets()

@app.post("/api/characters")
def api_create_character(req: CharacterReq):
    jid = new_job("character", _dump(req))
    POOL.submit(run_character, jid, req)
    return {"job_id": jid}

@app.post("/api/parts")
def api_create_parts(req: PartsReq):
    if req.rig not in prompts.RIGS:
        raise HTTPException(400, f"unknown rig {req.rig!r}")
    jid = new_job("parts", _dump(req))
    POOL.submit(run_parts, jid, req)
    return {"job_id": jid}

@app.post("/api/actions")
def api_create_action(req: ActionReq):
    if not req.character_id and not req.prompt:
        raise HTTPException(400, "need character_id or prompt")
    jid = new_job("action", _dump(req))
    POOL.submit(run_action, jid, req)
    return {"job_id": jid}

@app.post("/api/batch")
def api_create_batch(req: BatchReq):
    if not req.prompt:
        raise HTTPException(400, "need prompt")
    if not req.views or not req.actions:
        raise HTTPException(400, "need at least one view and one action")
    jid = new_job("batch", _dump(req))
    POOL.submit(run_batch, jid, req)
    return {"job_id": jid}

@app.get("/api/jobs/{jid}")
def api_job(jid: str):
    with LOCK:
        job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, "job not found")
    return job

@app.get("/api/characters")
def api_characters():
    out = []
    for p in sorted(glob.glob(os.path.join(OUT, "characters", "*", "character.json")),
                    key=os.path.getmtime, reverse=True):
        try:
            rec = json.load(open(p))
        except (ValueError, OSError):
            continue
        cid = os.path.basename(os.path.dirname(p))
        out.append({"character_id": cid, "master": url_for(rec["master_path"]),
                    "fields": rec["fields"], "text": rec.get("text")})
    return out

@app.get("/api/gallery")
def api_gallery():
    out = []
    for p in sorted(glob.glob(os.path.join(OUT, "jobs", "*", "result.json")),
                    key=os.path.getmtime, reverse=True)[:60]:
        try:
            rec = json.load(open(p))
        except (ValueError, OSError):
            continue
        out.append({"job_id": os.path.basename(os.path.dirname(p)),
                    "kind": rec.get("kind", "action"),
                    "sheet": url_for(rec["sheet"]),
                    # a parts result has no looping preview — don't 500 the gallery
                    "preview": url_for(rec["preview"]) if rec.get("preview") else None,
                    "action": rec.get("action"), "mode": rec.get("mode"),
                    "style": rec.get("style"), "frames": rec.get("frameCount")})
    return out


app.mount("/files", StaticFiles(directory=OUT), name="files")

DIST = os.path.join(ROOT, "frontend", "dist")
if os.path.isdir(DIST):
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
else:
    @app.get("/", response_class=HTMLResponse)
    def index():
        return ("<h3>spritegen backend is up.</h3><p>Frontend not built. Run:<br>"
                "<code>cd frontend &amp;&amp; bun install &amp;&amp; bun run dev</code> "
                "(dev, port 5173) — or <code>bun run build</code> to serve from here.</p>")
