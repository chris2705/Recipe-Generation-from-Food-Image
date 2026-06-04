"""
Video Recipe Generator Blueprint
==================================
Routes:
  POST /video/generate              – Start generation job; return job_id JSON
  GET  /video/progress/<job_id>     – SSE stream of progress events
  GET  /video/result/<job_id>       – Result page (video player + download)
  GET  /video/download/<job_id>     – Download the MP4 file
  GET  /video/from-recipe/<id>      – Start video from a saved Recipe Book entry
  POST /video/save-to-recipe/<id>   – Save video path back to SavedRecipe

In-process job store: { job_id: JobState }  — sufficient for single-worker dev.
Replace with Redis/Celery for multi-process production deployments.
"""

import json
import logging
import mimetypes
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
    stream_with_context,
)
from flask_login import current_user

logger = logging.getLogger(__name__)

video_bp = Blueprint("video", __name__, url_prefix="/video")

# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def _new_job(recipe_data: dict, language: str, recipe_id: Optional[int] = None) -> str:
    """Create a new job entry and return its ID."""
    job_id = str(uuid.uuid4())
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "job_id":      job_id,
            "status":      "pending",   # pending | running | done | error
            "stage":       "Initialising...",
            "progress":    0,
            "video_path":  None,
            "error":       None,
            "language":    language,
            "food_name":   recipe_data.get("food_name", "Recipe"),
            "recipe_id":   recipe_id,
            "created_at":  datetime.now(timezone.utc),
            "user_id":     current_user.id if current_user.is_authenticated else None,
        }
    return job_id


def _get_job(job_id: str) -> Optional[dict]:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def _update_job(job_id: str, **kwargs):
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(kwargs)


# ---------------------------------------------------------------------------
# Output directory helpers
# ---------------------------------------------------------------------------
def _output_dir_for(job_id: str) -> str:
    from Foodimg2Ing import app  # noqa: PLC0415
    base = os.path.join(app.root_path, "static", "generated_videos", job_id)
    os.makedirs(base, exist_ok=True)
    return base


def _static_url_for_video(job_id: str) -> Optional[str]:
    """Return the static-relative path for the generated video, or None."""
    from Foodimg2Ing import app  # noqa: PLC0415
    mp4 = os.path.join(
        app.root_path, "static", "generated_videos", job_id, "recipe_video.mp4"
    )
    if os.path.exists(mp4):
        return f"generated_videos/{job_id}/recipe_video.mp4"
    return None


# ---------------------------------------------------------------------------
# Background generation worker
# ---------------------------------------------------------------------------
def _run_generation(job_id: str, recipe_data: dict, language: str):
    """Run in a daemon thread: call VideoRecipeGenerator, update job state."""
    from Foodimg2Ing.services.video_generator import VideoRecipeGenerator  # noqa: PLC0415
    from Foodimg2Ing import app, db  # noqa: PLC0415

    _update_job(job_id, status="running", stage="Starting...", progress=1)

    def progress_cb(stage: str, pct: int):
        _update_job(job_id, stage=stage, progress=pct)
        logger.debug(f"[VIDEO JOB {job_id[:8]}] {pct:3d}% – {stage}")

    try:
        gen = VideoRecipeGenerator()
        output_dir = _output_dir_for(job_id)
        mp4_path = gen.generate_video(
            recipe_data=recipe_data,
            language=language,
            output_dir=output_dir,
            progress_cb=progress_cb,
        )

        _update_job(
            job_id,
            status="done",
            stage="Done!",
            progress=100,
            video_path=mp4_path,
        )

        # If this was generated from a saved recipe, persist the video path
        recipe_id = _get_job(job_id).get("recipe_id")
        if recipe_id:
            try:
                with app.app_context():
                    from Foodimg2Ing.models import SavedRecipe  # noqa: PLC0415
                    rec = SavedRecipe.query.get(recipe_id)
                    if rec:
                        rel = f"generated_videos/{job_id}/recipe_video.mp4"
                        rec.video_path = rel
                        rec.video_language = language
                        rec.video_created_at = datetime.utcnow()
                        db.session.commit()
                        logger.info(f"Saved video path to recipe #{recipe_id}")
            except Exception as exc:
                logger.warning(f"Could not save video path to DB: {exc}")

        logger.info(f"Video job {job_id[:8]} completed: {mp4_path}")

    except Exception as exc:
        err_msg = str(exc)
        logger.error(f"Video job {job_id[:8]} failed: {err_msg}", exc_info=True)
        _update_job(job_id, status="error", stage="Error", progress=0, error=err_msg)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@video_bp.route("/generate", methods=["POST"])
def generate():
    """
    Accept recipe data + language, start a background job.
    Returns JSON: { job_id, redirect_url }
    """
    data = request.get_json(silent=True) or {}

    food_name    = data.get("food_name", "").strip()
    ingredients  = data.get("ingredients", [])
    recipe_steps = data.get("recipe_steps", [])
    language     = data.get("language", "english").lower().strip()
    recipe_id    = data.get("recipe_id")  # optional – from Recipe Book

    if not food_name or not recipe_steps:
        return jsonify({"error": "food_name and recipe_steps are required."}), 400

    recipe_data = {
        "food_name":    food_name,
        "ingredients":  ingredients,
        "recipe_steps": recipe_steps,
    }

    job_id = _new_job(recipe_data, language, recipe_id=recipe_id)

    t = threading.Thread(
        target=_run_generation,
        args=(job_id, recipe_data, language),
        daemon=True,
    )
    t.start()

    return jsonify({
        "job_id":       job_id,
        "redirect_url": url_for("video.processing_page", job_id=job_id),
    }), 202


@video_bp.route("/processing/<job_id>")
def processing_page(job_id: str):
    """Render the animated processing page."""
    job = _get_job(job_id)
    if not job:
        return redirect(url_for("generate"))
    return render_template(
        "video_processing.html",
        job_id=job_id,
        food_name=job.get("food_name", "Recipe"),
        language=job.get("language", "english").capitalize(),
    )


@video_bp.route("/progress/<job_id>")
def progress_stream(job_id: str):
    """
    Server-Sent Events endpoint.  Client polls until status == done|error.
    """
    def _event_generator():
        last_progress = -1
        for _ in range(600):          # max 600 × 0.5s = 5 minutes
            job = _get_job(job_id)
            if not job:
                yield _sse_event({"status": "error", "error": "Job not found."})
                return

            payload = {
                "status":   job["status"],
                "stage":    job["stage"],
                "progress": job["progress"],
                "error":    job.get("error"),
            }

            if job["status"] in ("done", "error") or job["progress"] != last_progress:
                last_progress = job["progress"]
                yield _sse_event(payload)

            if job["status"] in ("done", "error"):
                return

            time.sleep(0.5)

        yield _sse_event({"status": "error", "error": "Timed out waiting for video."})

    return Response(
        stream_with_context(_event_generator()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@video_bp.route("/result/<job_id>")
def result_page(job_id: str):
    """Render the video result page."""
    job = _get_job(job_id)
    if not job:
        return redirect(url_for("generate"))

    if job["status"] == "error":
        return render_template(
            "video_result.html",
            job_id=job_id,
            food_name=job.get("food_name", "Recipe"),
            language=job.get("language", "english").capitalize(),
            error=job.get("error", "Unknown error."),
            video_url=None,
        )

    video_static_url = None
    if job["status"] == "done":
        video_static_url = _static_url_for_video(job_id)

    return render_template(
        "video_result.html",
        job_id=job_id,
        food_name=job.get("food_name", "Recipe"),
        language=job.get("language", "english").capitalize(),
        video_url=video_static_url,
        recipe_id=job.get("recipe_id"),
        error=None,
    )


@video_bp.route("/download/<job_id>")
def download(job_id: str):
    """Serve the MP4 for download."""
    job = _get_job(job_id)
    if not job or job["status"] != "done":
        abort(404)

    from Foodimg2Ing import app  # noqa: PLC0415
    mp4_path = os.path.join(
        app.root_path, "static", "generated_videos", job_id, "recipe_video.mp4"
    )
    if not os.path.exists(mp4_path):
        abort(404)

    safe_name = job.get("food_name", "recipe").replace(" ", "_").lower()
    return send_file(
        mp4_path,
        as_attachment=True,
        download_name=f"{safe_name}_cooking_tutorial.mp4",
        mimetype="video/mp4",
    )


@video_bp.route("/from-recipe/<int:recipe_id>")
def from_recipe(recipe_id: int):
    """
    Start video generation from a Recipe Book entry.
    Renders the language selection page first.
    """
    from flask_login import login_required  # noqa: PLC0415
    from Foodimg2Ing.models import SavedRecipe  # noqa: PLC0415

    if not current_user.is_authenticated:
        from flask import url_for as _uf  # noqa: PLC0415
        return redirect(_uf("auth.login"))

    recipe = SavedRecipe.query.get_or_404(recipe_id)
    if recipe.user_id != current_user.id:
        abort(403)

    return render_template(
        "video_processing.html",
        job_id=None,
        food_name=recipe.food_name,
        language=None,
        recipe_id=recipe_id,
        prefill={
            "food_name":    recipe.food_name,
            "ingredients":  recipe.get_ingredients(),
            "recipe_steps": recipe.get_recipe_steps(),
            "recipe_id":    recipe_id,
        },
    )


# ---------------------------------------------------------------------------
# Cleanup: delete guest videos older than 7 days
# ---------------------------------------------------------------------------
def _cleanup_old_videos():
    """Run in a daemon thread periodically to remove stale guest videos."""
    import shutil  # noqa: PLC0415

    while True:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            with _JOBS_LOCK:
                stale = [
                    jid for jid, j in _JOBS.items()
                    if j.get("user_id") is None
                    and j.get("created_at", datetime.now(timezone.utc)) < cutoff
                ]
            for jid in stale:
                from Foodimg2Ing import app  # noqa: PLC0415
                folder = os.path.join(
                    app.root_path, "static", "generated_videos", jid
                )
                if os.path.isdir(folder):
                    shutil.rmtree(folder, ignore_errors=True)
                    logger.info(f"Cleaned up old guest video job: {jid[:8]}")
                with _JOBS_LOCK:
                    _JOBS.pop(jid, None)
        except Exception as exc:
            logger.warning(f"Cleanup thread error: {exc}")

        time.sleep(6 * 3600)   # check every 6 hours


def start_cleanup_thread():
    t = threading.Thread(target=_cleanup_old_videos, daemon=True, name="video-cleanup")
    t.start()
    logger.info("Video cleanup background thread started.")
