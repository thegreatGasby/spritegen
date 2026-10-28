#!/usr/bin/env python3
"""Load a project-root .env into os.environ (no external dependency).

Real environment variables always win (setdefault), so exporting the key still
overrides the file. Looked up in order: $SPRITEGEN_ENV_FILE, <project-root>/.env,
./.env.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _pairs(text):
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        yield k.strip(), v.strip().strip('"').strip("'")


def load_env(path=None):
    """Return the path loaded, or None."""
    cands = [path, os.environ.get("SPRITEGEN_ENV_FILE"),
             os.path.join(ROOT, ".env"), os.path.join(os.getcwd(), ".env")]
    for p in cands:
        if p and os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    for k, v in _pairs(fh.read()):
                        os.environ.setdefault(k, v)
            except OSError:
                continue
            return p
    return None
