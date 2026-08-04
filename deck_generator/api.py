"""
api.py — FastAPI REST server for the Deck Generator pipeline.

Endpoints:
    POST /api/generate          — Accept a DeckBrief JSON body; enqueue a
                                   background generation job; return {job_id}.
    GET  /api/jobs/{job_id}     — Poll the status of a job.  Transitions:
                                   pending → running → complete | failed.
                                   Returns download_url + elapsed_seconds on success.
    GET  /api/download/{fname}  — Stream the generated .pptx file to the browser.
    GET  /api/health            — Liveness check.

Job lifecycle:
    Jobs are stored in the module-level _jobs dict (keyed by UUID).  Each job
    runs as a FastAPI BackgroundTask — i.e. an asyncio coroutine that runs
    concurrently with the HTTP server in the same event loop.  The HTTP response
    for POST /api/generate returns as soon as the job is queued (HTTP 202);
    the caller must poll GET /api/jobs/{job_id} to learn when it is done.

Scaling note:
    The in-memory job store is cleared when the process restarts.  For
    multi-worker or persistent deployments replace _jobs with Redis/ARQ/Celery.

Frontend:
    The companion SPA at deck_frontend/index.html submits briefs here and
    polls for completion.  Start the server with: python start_server.py
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from deck_generator.config import get_settings
from deck_generator.models import DeckBrief, DeckState

logger = logging.getLogger("deck_generator.api")

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Deck Generator API",
    description="ML arteka multi-agent PowerPoint generation pipeline",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten to specific origins in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Job store (in-memory) ─────────────────────────────────────────────────────

class JobRecord(BaseModel):
    """Runtime state for a single generation job, kept in the in-memory store."""
    job_id: str
    status: str                     # pending | running | complete | failed
    pptx_path: Optional[str] = None       # absolute path on the server filesystem
    pptx_filename: Optional[str] = None   # basename only, used to build download_url
    error: Optional[str] = None           # exception message when status == failed
    started_at: Optional[float] = None    # unix timestamp when pipeline began
    finished_at: Optional[float] = None   # unix timestamp when pipeline ended
    brief_title: str = ""                 # deck title, echoed back in status responses
    execution_logs: list[str] = []        # agent log entries streamed to the frontend
    slide_count: int = 0

# In-memory job store: job_id (UUID str) → JobRecord.
# Lives for the lifetime of the process; not persisted across restarts.
_jobs: Dict[str, JobRecord] = {}


# ── Pipeline runner ───────────────────────────────────────────────────────────

async def _run_pipeline(job_id: str, brief: DeckBrief) -> None:
    """
    Execute the full LangGraph pipeline as a background coroutine.

    Called by FastAPI's BackgroundTasks; runs concurrently with the HTTP server.
    Updates _jobs[job_id] throughout so polling clients see live progress.
    """
    from deck_generator.workflow.graph import build_deck_graph

    job = _jobs[job_id]
    job.status = "running"
    job.started_at = time.time()
    logger.info("Job %s: starting pipeline for '%s'", job_id, brief.title)

    try:
        initial_state = DeckState(
            deck_brief=brief,
            audience=brief.audience,
            industry=brief.industry,
            brand=brief.brand,
            status="initialized",
            max_retries=2,
        )
        graph = build_deck_graph()
        result = await graph.ainvoke(initial_state)
        final: DeckState = (
            DeckState.model_validate(result) if isinstance(result, dict) else result
        )

        job.status = "complete"
        job.pptx_path = final.pptx_path
        job.pptx_filename = Path(final.pptx_path).name if final.pptx_path else None
        job.execution_logs = final.execution_logs
        job.slide_count = len(final.slides)
        logger.info("Job %s: complete → %s", job_id, final.pptx_path)

    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        logger.exception("Job %s: pipeline failed", job_id)
    finally:
        job.finished_at = time.time()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/api/generate", status_code=202)
async def generate(brief: DeckBrief, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Submit a DeckBrief and start the generation pipeline in the background.

    Returns immediately with a job_id. Poll GET /api/jobs/{job_id} for status.
    """
    job_id = str(uuid.uuid4())
    _jobs[job_id] = JobRecord(job_id=job_id, status="pending", brief_title=brief.title)
    background_tasks.add_task(_run_pipeline, job_id, brief)
    logger.info("Job %s: queued for '%s'", job_id, brief.title)
    return {"job_id": job_id, "status": "pending"}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> Dict[str, Any]:
    """Return the current status of a generation job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    # Always include these fields so the frontend can render progress.
    payload: Dict[str, Any] = {
        "job_id": job.job_id,
        "status": job.status,
        "brief_title": job.brief_title,
        "slide_count": job.slide_count,
        "execution_logs": job.execution_logs,
        "error": job.error,
    }
    # Add download link and elapsed time only once the deck is ready.
    if job.status == "complete" and job.pptx_filename:
        payload["download_url"] = f"/api/download/{job.pptx_filename}"
        elapsed = (job.finished_at or 0) - (job.started_at or 0)
        payload["elapsed_seconds"] = round(elapsed, 1)

    return payload


@app.get("/api/download/{filename}")
async def download(filename: str) -> FileResponse:
    """Stream the generated .pptx file to the client."""
    s = get_settings()
    file_path = Path(s.output_dir) / filename

    # Prevent path traversal: ensure the resolved path is inside output_dir.
    try:
        file_path.resolve().relative_to(Path(s.output_dir).resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File {filename!r} not found")

    return FileResponse(
        path=str(file_path),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=filename,
    )


@app.get("/api/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}
