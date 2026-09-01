"""Hugging Face Space entrypoint for the Pearls AQI FastAPI service (D10).

The Space's Gradio SDK runs `python app.py` and proxies whatever binds
0.0.0.0:7860 — it does not require the process to be a bare `gradio.Blocks`
app, only that *something* is listening there. This file wraps the existing
FastAPI service (`aqi.serving.api:app`) with a minimal Gradio landing page at
`/ui` (per HF's documented `gr.mount_gradio_app` pattern for adding custom
routes to a Gradio Space) and serves the combined app with uvicorn.
`src/aqi/serving/api.py` is imported unmodified — nothing here edits it.

**Getting the code:** `pip install git+https://github.com/alizataimur/
aqi-pearls.git` was considered and rejected. It installs the `aqi` package's
*source* into site-packages, but `ParquetFeatureStore.DEFAULT_ROOT` and
`LocalModelRegistry.DEFAULT_ROOT` are computed as
`Path(__file__).resolve().parents[3] / "data" / "..."` — a path relative to
wherever the `aqi` package physically lives — and that default is bound once,
at class-definition time, as the functions' default argument value. A
site-packages install can never make that path resolve to real data, and
there is no override hook to inject a different root without editing
`aqi/store/__init__.py` or `aqi/serving/inference.py`, both out of scope here.

**Chosen instead:** a shallow `git clone --depth 1` of the same public repo
into `/tmp/aqi-pearls` at Space startup, then `sys.path.insert` its `src/`
directory — the exact mechanism `app/streamlit_app.py` already uses for
Streamlit Community Cloud (see that file). This makes `__file__` for every
`aqi.*` module resolve *inside* a real checkout, so `DEFAULT_ROOT` finds the
committed `data/feature_store/` and `data/model_registry/` exactly as it
would in a full local clone — solving the code and the data with one
mechanism, zero changes to any existing source file, and no environment
variable plumbing to maintain. The clone gets the whole repo (a few hundred
MB including ledger/registry history); `--depth 1` keeps that to one commit's
worth of blobs rather than the full history.

The clone happens once per container lifetime (Spaces restart the container
on redeploy or after a sleep/wake cycle, so a stale clone never persists
across those events) and is skipped if `/tmp/aqi-pearls/src/aqi` already
exists, so a warm reload of this module doesn't re-clone.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/alizataimur/aqi-pearls.git"
CLONE_ROOT = Path("/tmp/aqi-pearls")


def _ensure_repo_cloned() -> Path:
    if not (CLONE_ROOT / "src" / "aqi").exists():
        subprocess.run(
            ["git", "clone", "--depth", "1", REPO_URL, str(CLONE_ROOT)],
            check=True,
        )
    return CLONE_ROOT


_repo_root = _ensure_repo_cloned()
_src = _repo_root / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import gradio as gr  # noqa: E402
import uvicorn  # noqa: E402

from aqi.serving.api import app  # noqa: E402

with gr.Blocks(title="Pearls AQI Predictor — API") as _landing:
    gr.Markdown(
        """
        # Pearls AQI Predictor — API service

        This Space serves the **FastAPI backend** (D10) behind the Pearls AQI
        Predictor. It is not a dashboard — the Streamlit UI that calls this
        API is at **[aqi-pearls-predictor.streamlit.app]
        (https://aqi-pearls-predictor.streamlit.app)**.

        **[Open the interactive API docs → /docs](/docs)**

        Endpoints: `/health`, `/cities`, `/current`, `/forecast`, `/explain`,
        `/metrics`. (`/benchmark`, named in the original design, was never
        built — the forecast ledger is too short for an honest scorecard;
        see `docs/DECISIONS.md`.)
        """
    )


@app.get("/", include_in_schema=False)
def _root() -> dict[str, str]:
    """api.py defines no "/" route. Added here, not in api.py, so a visitor
    landing on the bare Space URL is pointed at /docs and /ui instead of a
    bare 404 — mounting the Gradio Blocks page at /ui (not /) per the
    Space's ZeroGPU hardware tier means / would otherwise be unclaimed."""
    return {
        "service": "Pearls AQI Predictor API",
        "docs": "/docs",
        "ui": "/ui",
        "dashboard": "https://aqi-pearls-predictor.streamlit.app",
    }


app = gr.mount_gradio_app(app, _landing, path="/ui")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
