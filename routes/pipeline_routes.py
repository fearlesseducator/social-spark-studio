"""
routes/pipeline_routes.py

FastAPI routes that trigger the existing pipeline phases from the UI.

Rules:
  - Never calls main() from any run_*.py
  - Never calls check_prerequisites() (uses sys.exit — replaced with HTTPException)
  - Never calls any function that has input() — those gates are skipped here
  - Long-running phases (moments, captions, images) run in BackgroundTasks
    and return a job_id immediately; poll GET /api/jobs/{job_id} for status
  - Fast phases (transcript fetch, CSV export) respond synchronously

Routes:
    POST /api/run/transcript          — fetch YouTube transcript
    POST /api/run/export              — write CSV export file
    GET  /api/data/images/{filename}  — serve a generated image PNG
    POST /api/run/moments             — select transcript moments  [background]
    POST /api/run/captions            — generate post drafts       [background]
    POST /api/run/images              — generate images            [background]
    GET  /api/jobs/{job_id}           — poll a background job

Wire up in app.py:
    from routes.pipeline_routes import router as pipeline_router
    app.include_router(pipeline_router, prefix="/api")
"""

import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# ── Constants ──────────────────────────────────────────────────────────

# TODO(cloud): data/ is ephemeral on Cloud Run — generated images and CSVs
# vanish on restart. Intended fix: upload to GCS_BUCKET_NAME and put public
# URLs in posts_output.json / the exported CSV instead of local paths.
DATA_DIR       = Path("data")
IMAGES_DIR     = DATA_DIR / "generated_images"
APP_NAME       = "social_spark_studio"
USER_ID        = "local_founder"
MIN_WORDS      = 300

router = APIRouter()

# ── In-memory job store ────────────────────────────────────────────────
# { job_id: { id, status, phase, result, error, started_at, finished_at } }
# "status" values: "pending" | "running" | "done" | "failed"

_jobs: dict[str, dict] = {}


def _new_job(phase: str) -> str:
    jid = uuid.uuid4().hex[:8]
    _jobs[jid] = {
        "id":          jid,
        "phase":       phase,
        "status":      "pending",
        "result":      None,
        "error":       None,
        "started_at":  datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
    }
    return jid


def _finish_job(jid: str, result: dict) -> None:
    _jobs[jid]["status"]      = "done"
    _jobs[jid]["result"]      = result
    _jobs[jid]["finished_at"] = datetime.now(timezone.utc).isoformat()


def _fail_job(jid: str, error: str) -> None:
    _jobs[jid]["status"]      = "failed"
    _jobs[jid]["error"]       = error
    _jobs[jid]["finished_at"] = datetime.now(timezone.utc).isoformat()


# ── Prerequisite helpers ───────────────────────────────────────────────
# These raise HTTPException (not sys.exit) so FastAPI handles them cleanly.

def _hydrate_if_missing(path: Path) -> None:
    """
    When USE_FIRESTORE is on and a local data file is missing (fresh
    Cloud Run container), pull it down from Firestore so the path-based
    pipeline helpers can read it. No-op in local file mode.
    """
    if path.exists():
        return
    try:
        from services.storage_service import hydrate_local_file
        hydrate_local_file(path.name)
    except Exception as exc:
        print(f"[pipeline] hydration skipped for {path.name}: {exc}")


def _require_file(path: Path, hint: str) -> None:
    """Raise 422 if a required data file is missing (after trying Firestore)."""
    _hydrate_if_missing(path)
    if not path.exists():
        raise HTTPException(status_code=422, detail=hint)


def _dna_path() -> Path:
    """Return the MessageDNA path, preferring test_ variant when real file missing."""
    real = DATA_DIR / "message_dna_output.json"
    if real.exists():
        return real
    test = DATA_DIR / "test_message_dna_output.json"
    if test.exists():
        return test
    _hydrate_if_missing(real)
    if real.exists():
        return real
    raise HTTPException(
        status_code=422,
        detail="MessageDNA not found. Complete the Voice Interview first."
    )


# ── ADK helper ─────────────────────────────────────────────────────────

def _collect_agent_text(events) -> str:
    """Drain an ADK event generator and return all text parts concatenated."""
    full_text = ""
    for event in events:
        if hasattr(event, "content") and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    full_text += part.text
    return full_text


_RETRY_ATTEMPTS = 3
_RETRY_WAIT = 15  # seconds between retries on 503


def _run_agent_single_turn(agent, context: str) -> str:
    """
    Create a fresh InMemoryRunner, send one message, return the full text.
    Retries up to _RETRY_ATTEMPTS times on 503 / model-overloaded errors.
    Raises RuntimeError on persistent failure (never calls sys.exit).
    """
    import time as _time
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    last_err = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            session_id = f"pipe_{uuid.uuid4().hex[:8]}"
            runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
            runner.session_service.create_session_sync(
                app_name=APP_NAME,
                user_id=USER_ID,
                session_id=session_id,
            )
            events = runner.run(
                user_id=USER_ID,
                session_id=session_id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part(text=context)],
                ),
            )
            result = _collect_agent_text(events)
            if not result and attempt < _RETRY_ATTEMPTS:
                # Empty response may be a silent 503 — retry
                print(f"[pipeline] attempt {attempt}: empty response, retrying in {_RETRY_WAIT}s")
                _time.sleep(_RETRY_WAIT)
                continue
            return result
        except Exception as exc:
            last_err = exc
            exc_str = str(exc)
            if "503" in exc_str or "UNAVAILABLE" in exc_str or "overload" in exc_str.lower():
                if attempt < _RETRY_ATTEMPTS:
                    print(f"[pipeline] attempt {attempt}: 503 overloaded, retrying in {_RETRY_WAIT}s")
                    _time.sleep(_RETRY_WAIT)
                    continue
                raise RuntimeError(
                    f"Google model overloaded after {_RETRY_ATTEMPTS} attempts. "
                    "Please retry in a few minutes."
                ) from exc
            raise
    raise RuntimeError(
        f"Agent returned empty response after {_RETRY_ATTEMPTS} attempts. "
        "Model may be overloaded — please retry."
    ) from last_err


# ══════════════════════════════════════════════════════════════════════
# 1. POST /api/run/transcript
# ══════════════════════════════════════════════════════════════════════

class TranscriptRequest(BaseModel):
    youtube_url: str


@router.post("/run/transcript")
async def run_transcript_route(request: TranscriptRequest):
    """
    Fetch and save a YouTube transcript.

    Checks MessageDNA + CampaignBrief prerequisites, calls
    fetch_transcript() from tools.youtube_fetcher directly (no ADK),
    saves transcript_output.json, and updates campaign_brief.json
    with the confirmed youtube_url.

    Returns immediately — youtube-transcript-api is fast (~2-4 s).
    """
    # Prerequisites
    dna = _dna_path()                         # raises 422 if missing
    brief_path = DATA_DIR / "campaign_brief.json"
    _require_file(brief_path, "Campaign brief not found. Run the campaign brief phase first.")

    url = request.youtube_url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="youtube_url is required.")

    from tools.youtube_fetcher import fetch_transcript
    from services.storage_service import storage_save_transcript, storage_save_campaign_brief

    result = fetch_transcript(url, min_total_words=MIN_WORDS)

    if not result.is_success:
        return JSONResponse(
            status_code=422,
            content={
                "success":       False,
                "error_type":    result.error_type,
                "error_message": result.error_message,
            },
        )

    storage_save_transcript(result)

    # Write the confirmed youtube_url back into the campaign brief
    try:
        from models.campaign_brief import load_campaign_brief
        brief_obj = load_campaign_brief(str(brief_path))
        brief_obj.youtube_url = url
        storage_save_campaign_brief(brief_obj)
    except Exception:
        pass  # Non-fatal — transcript was saved, brief update is cosmetic

    return JSONResponse(content={
        "success":          True,
        "video_id":         result.video_id,
        "video_url":        result.video_url,
        "total_segments":   result.total_segments,
        "total_words":      result.total_words,
        "duration_seconds": result.duration_seconds,
        "language_code":    result.language_code,
        "output_file":      "transcript_output.json",
    })


# ══════════════════════════════════════════════════════════════════════
# 1a. Founder video upload (main transcript path)
# ══════════════════════════════════════════════════════════════════════

class SignUploadRequest(BaseModel):
    filename: str
    content_type: str = "video/mp4"


@router.post("/upload/video/sign")
async def sign_video_upload(request: SignUploadRequest):
    """
    Issue a signed PUT URL so the browser uploads the founder MP4
    directly to Cloud Storage (Cloud Run caps request bodies at 32 MB,
    so large videos can't be proxied through the backend).

    Returns upload_url (PUT target), gcs_uri, and public_url.
    """
    import os as _os
    import uuid as _uuid
    from datetime import timedelta

    bucket_name = _os.getenv("GCS_BUCKET_NAME", "")
    project     = _os.getenv("GOOGLE_CLOUD_PROJECT", "")
    if not bucket_name or not project:
        raise HTTPException(status_code=500, detail="GCS_BUCKET_NAME / GOOGLE_CLOUD_PROJECT not configured.")

    ext = Path(request.filename).suffix.lower() or ".mp4"
    if ext not in {".mp4", ".m4a", ".mp3", ".wav", ".webm"}:
        raise HTTPException(status_code=422, detail="Please upload an MP4 video (or MP3/M4A/WAV audio as backup).")

    object_name = f"founder_videos/{_uuid.uuid4().hex}{ext}"

    try:
        from google.cloud import storage as gcs_storage
        client = gcs_storage.Client(project=project)
        blob   = client.bucket(bucket_name).blob(object_name)

        try:
            # Local dev: service-account key can sign directly
            upload_url = blob.generate_signed_url(
                version="v4", method="PUT", expiration=timedelta(minutes=30),
                content_type=request.content_type,
            )
        except Exception:
            # Cloud Run: no key file — sign via IAM with the attached SA
            import google.auth
            from google.auth.transport import requests as ga_requests
            credentials, _ = google.auth.default()
            credentials.refresh(ga_requests.Request())
            upload_url = blob.generate_signed_url(
                version="v4", method="PUT", expiration=timedelta(minutes=30),
                content_type=request.content_type,
                service_account_email=credentials.service_account_email,
                access_token=credentials.token,
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not create upload URL: {exc}")

    return JSONResponse(content={
        "success":     True,
        "upload_url":  upload_url,
        "gcs_uri":     f"gs://{bucket_name}/{object_name}",
        "public_url":  f"https://storage.googleapis.com/{bucket_name}/{object_name}",
        "content_type": request.content_type,
    })


class VideoTranscriptRequest(BaseModel):
    gcs_uri: str
    public_url: str = ""
    title: str = ""
    duration_seconds: int = 0     # optional hint for timing fallback


def _bg_video_transcript(job_id: str, gcs_uri: str, public_url: str,
                         title: str, duration_seconds: int) -> None:
    """
    Background worker for founder video uploads.

    MP4 containers can't be decoded by Speech-to-Text batch, so:
      video upload → download from GCS → ffmpeg audio extraction →
      batch STT (proven MP3 path) → TranscriptResult with the MP4's
      public URL preserved as video_url for future clip generation.
    Audio uploads (mp3/m4a/wav) skip extraction and transcribe in place.
    """
    _jobs[job_id]["status"] = "running"
    try:
        from tools.speech_to_text_tool import transcribe_long_audio, extract_audio_from_video
        from tools.rss_fetcher import build_transcript_result
        from services.storage_service import storage_save_transcript

        is_video = gcs_uri.lower().endswith((".mp4", ".mov"))

        if is_video:
            import os as _os
            from google.cloud import storage as gcs_storage
            print(f"[video:{job_id}] downloading {gcs_uri} for audio extraction")
            bucket_name, _, object_name = gcs_uri.removeprefix("gs://").partition("/")
            client = gcs_storage.Client(project=_os.getenv("GOOGLE_CLOUD_PROJECT"))
            video_bytes = client.bucket(bucket_name).blob(object_name).download_as_bytes()
            print(f"[video:{job_id}] extracting audio from {len(video_bytes)} bytes")
            audio_bytes = extract_audio_from_video(video_bytes)
            print(f"[video:{job_id}] audio extracted: {len(audio_bytes)} bytes — transcribing")
            if not duration_seconds:
                from tools.speech_to_text_tool import probe_media_duration
                duration_seconds = int(probe_media_duration(audio_bytes))
                print(f"[video:{job_id}] probed duration: {duration_seconds}s")
            stt = transcribe_long_audio(audio_bytes=audio_bytes, content_type="audio/mpeg",
                                        timeout_seconds=1800)
        else:
            print(f"[video:{job_id}] transcribing audio in place: {gcs_uri}")
            stt = transcribe_long_audio(gcs_uri=gcs_uri, timeout_seconds=1800)

        if not stt.success:
            _fail_job(job_id, f"Transcription failed: {stt.error_message}")
            return

        result = build_transcript_result(
            chunks=stt.chunks,
            media_url=public_url or gcs_uri,
            episode_title=title,
            episode_link=public_url,
            language_code=stt.language_code or "en-US",
            fallback_duration=float(duration_seconds or stt.duration_seconds or 0),
        )
        # Preserve the uploaded MP4 as the canonical source for clips
        result.video_id  = "upload_" + result.video_id.removeprefix("rss_")
        result.video_url = public_url or gcs_uri

        if result.total_words < MIN_WORDS:
            _fail_job(
                job_id,
                f"Transcript too short ({result.total_words} words; minimum {MIN_WORDS}). "
                "Make sure the video has clear spoken audio.",
            )
            return

        storage_save_transcript(result)

        _finish_job(job_id, {
            "title":            title,
            "video_url":        result.video_url,
            "total_segments":   result.total_segments,
            "total_words":      result.total_words,
            "duration_seconds": result.duration_seconds,
            "output_file":      "transcript_output.json",
        })

    except (Exception, SystemExit) as exc:
        _fail_job(job_id, f"{type(exc).__name__}: {exc}")


@router.post("/run/transcript/video")
async def run_video_transcript_route(
    request: VideoTranscriptRequest,
    background_tasks: BackgroundTasks,
):
    """
    Transcribe an uploaded founder video (already in GCS) in a
    background task. Poll GET /api/jobs/{job_id} for status.
    """
    dna = _dna_path()
    brief_path = DATA_DIR / "campaign_brief.json"
    _require_file(brief_path, "Campaign brief not found. Complete the campaign brief first.")

    if not request.gcs_uri.strip().startswith("gs://"):
        raise HTTPException(status_code=422, detail="gcs_uri must be a gs:// URI from /api/upload/video/sign.")

    job_id = _new_job("video_transcript")
    background_tasks.add_task(
        _bg_video_transcript,
        job_id,
        request.gcs_uri.strip(),
        request.public_url,
        request.title,
        request.duration_seconds,
    )
    return JSONResponse(content={
        "success":     True,
        "job_id":      job_id,
        "poll_url":    f"/api/jobs/{job_id}",
        "message":     "Video transcription started. Poll poll_url for status.",
        "eta_seconds": 180,
    })


# ══════════════════════════════════════════════════════════════════════
# 1b. RSS transcript import (secondary path)
# ══════════════════════════════════════════════════════════════════════

@router.get("/rss/episodes")
async def rss_episodes(feed_url: str = ""):
    """
    Fetch an RSS feed and list its episodes with selectability flags.

    Selectable: audio/* enclosure within the 5-20 minute product range
    (or unknown duration, flagged with a warning).
    Disabled: no enclosure, video-only, too long, too short.
    """
    from tools.rss_fetcher import fetch_feed_episodes

    result = fetch_feed_episodes(feed_url)
    status = 200 if result.get("success") else 422
    return JSONResponse(status_code=status, content=result)


class RssTranscriptRequest(BaseModel):
    media_url: str
    episode_title: str = ""
    episode_link: str = ""
    media_type: str = "audio/mpeg"
    duration_seconds: int = 0     # from the feed — timing fallback


def _bg_rss_transcript(job_id: str, media_url: str, episode_title: str,
                       episode_link: str, media_type: str,
                       duration_seconds: int = 0) -> None:
    """
    Background worker: download episode audio → batch STT (chirp_3 via
    GCS) → build TranscriptResult → save through the storage router.
    Produces the exact transcript_output.json the Moments agent expects.
    """
    _jobs[job_id]["status"] = "running"
    try:
        from tools.rss_fetcher import download_enclosure, build_transcript_result
        from tools.speech_to_text_tool import transcribe_long_audio
        from services.storage_service import storage_save_transcript

        print(f"[rss:{job_id}] downloading {media_url[:90]}")
        audio_bytes = download_enclosure(media_url)
        print(f"[rss:{job_id}] downloaded {len(audio_bytes)} bytes — transcribing")

        stt = transcribe_long_audio(audio_bytes, content_type=media_type or "audio/mpeg")
        if not stt.success:
            _fail_job(job_id, f"Transcription failed: {stt.error_message}")
            return

        result = build_transcript_result(
            chunks=stt.chunks,
            media_url=media_url,
            episode_title=episode_title,
            episode_link=episode_link,
            language_code=stt.language_code or "en-US",
            fallback_duration=float(duration_seconds or stt.duration_seconds or 0),
        )

        if result.total_words < MIN_WORDS:
            _fail_job(
                job_id,
                f"Episode transcript too short ({result.total_words} words; "
                f"minimum {MIN_WORDS}). Choose a longer episode.",
            )
            return

        storage_save_transcript(result)

        _finish_job(job_id, {
            "episode_title":    episode_title,
            "total_segments":   result.total_segments,
            "total_words":      result.total_words,
            "duration_seconds": result.duration_seconds,
            "output_file":      "transcript_output.json",
        })

    except (Exception, SystemExit) as exc:
        _fail_job(job_id, f"{type(exc).__name__}: {exc}")


@router.post("/run/transcript/rss")
async def run_rss_transcript_route(
    request: RssTranscriptRequest,
    background_tasks: BackgroundTasks,
):
    """
    Transcribe one RSS episode in a background task (~1-3 minutes for a
    5-20 minute episode). Poll GET /api/jobs/{job_id} for status.
    """
    dna = _dna_path()                       # 422 if MessageDNA missing
    brief_path = DATA_DIR / "campaign_brief.json"
    _require_file(brief_path, "Campaign brief not found. Complete the campaign brief first.")

    if not request.media_url.strip():
        raise HTTPException(status_code=422, detail="media_url is required.")

    job_id = _new_job("rss_transcript")
    background_tasks.add_task(
        _bg_rss_transcript,
        job_id,
        request.media_url.strip(),
        request.episode_title,
        request.episode_link,
        request.media_type,
        request.duration_seconds,
    )
    return JSONResponse(content={
        "success":     True,
        "job_id":      job_id,
        "poll_url":    f"/api/jobs/{job_id}",
        "message":     "Episode transcription started. Poll poll_url for status.",
        "eta_seconds": 90,
    })


class ManualTranscriptRequest(BaseModel):
    text: str
    source_url: str = ""


@router.post("/run/transcript/manual")
async def run_manual_transcript_route(request: ManualTranscriptRequest):
    """
    Manual transcript paste fallback. Parses pasted text into the same
    TranscriptResult structure and saves it. Synchronous — fast.
    """
    dna = _dna_path()
    brief_path = DATA_DIR / "campaign_brief.json"
    _require_file(brief_path, "Campaign brief not found. Complete the campaign brief first.")

    from tools.youtube_fetcher import parse_manual_transcript
    from services.storage_service import storage_save_transcript

    result = parse_manual_transcript(request.text, request.source_url or "")
    if not result.is_success:
        return JSONResponse(status_code=422, content={
            "success":       False,
            "error_type":    result.error_type,
            "error_message": result.error_message,
        })

    storage_save_transcript(result)
    return JSONResponse(content={
        "success":        True,
        "total_segments": result.total_segments,
        "total_words":    result.total_words,
        "output_file":    "transcript_output.json",
    })


# ══════════════════════════════════════════════════════════════════════
# 2. POST /api/run/export
# ══════════════════════════════════════════════════════════════════════

class ExportRequest(BaseModel):
    start_date: str = ""      # "YYYY-MM-DD" — campaign start
    posting_time: str = ""    # "HH:MM" (24h, from <input type=time>)


@router.post("/run/export")
async def run_export_route(request: ExportRequest = None):
    """
    Write a scheduler-ready CSV from posts_output.json.

    Imports build_row() and build_output_path() from run_export.py
    (pure functions, no side effects, no sys.exit).

    Web-flow adjustments applied per row:
      - videoUrls is blank: no real hosted MP4 clips exist yet, and
        schedulers can't use source-timestamp links. Timestamps stay
        in posts_output.json for future clip generation.
      - postAtSpecificTime is filled from start_date + posting_time:
        one post per day, "YYYY-MM-DD HH:mm:ss".
    """
    posts_path = DATA_DIR / "posts_output.json"
    _require_file(
        posts_path,
        "posts_output.json not found. Run the captions phase first."
    )

    # Optional schedule inputs
    schedule_start = None
    if request and request.start_date and request.posting_time:
        from datetime import datetime as _dt
        try:
            schedule_start = _dt.strptime(
                f"{request.start_date} {request.posting_time}", "%Y-%m-%d %H:%M"
            )
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Invalid schedule. Use start_date YYYY-MM-DD and posting_time HH:MM.",
            )

    # Import only the pure helper functions — never main() or check_prerequisites()
    from run_export import build_row, build_output_path, CSV_COLUMNS
    from services.storage_service import storage_load_post_drafts

    draft_set = storage_load_post_drafts()
    if draft_set is None:
        raise HTTPException(status_code=422, detail="No post drafts found. Run the captions phase first.")
    posts = draft_set.posts

    if not posts:
        raise HTTPException(status_code=422, detail="No posts found in posts_output.json.")

    output_path = Path(
        build_output_path(str(DATA_DIR), draft_set.campaign_id)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    from datetime import timedelta as _td
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for i, post in enumerate(posts):
            row = build_row(post)
            # No finished MP4 clips yet — never export timestamp links
            row["videoUrls"] = ""
            # One post per day at the chosen time
            if schedule_start is not None:
                row["postAtSpecificTime"] = (
                    (schedule_start + _td(days=i)).strftime("%Y-%m-%d %H:%M:%S")
                )
            writer.writerow(row)

    failed_images = [
        p.post_number
        for p in posts
        if p.asset_status == "image_generation_failed"
    ]

    return JSONResponse(content={
        "success":        True,
        "posts_exported": len(posts),
        "scheduled":      schedule_start is not None,
        "schedule_start": schedule_start.strftime("%Y-%m-%d %H:%M:%S") if schedule_start else None,
        "output_file":    output_path.name,
        "download_url":   f"/download-csv?file={output_path.name}",
        "failed_images":  failed_images,
        "warning": (
            f"{len(failed_images)} post(s) have missing images (imageUrls will be blank)."
            if failed_images else None
        ),
    })


# ══════════════════════════════════════════════════════════════════════
# 3. GET /api/data/images/{filename}
# ══════════════════════════════════════════════════════════════════════

@router.get("/data/images/{filename}")
async def serve_generated_image(filename: str):
    """
    Serve a generated PNG from data/generated_images/.

    Security: strips any path separators so callers can't escape the
    images directory. Only .png files are served.
    """
    safe_name = Path(filename).name           # strip any directory traversal
    if not safe_name.endswith(".png"):
        raise HTTPException(status_code=400, detail="Only .png files are served here.")

    image_path = IMAGES_DIR / safe_name
    if not image_path.exists():
        raise HTTPException(status_code=404, detail=f"Image not found: {safe_name}")

    return FileResponse(
        path=str(image_path),
        media_type="image/png",
        filename=safe_name,
    )


# ══════════════════════════════════════════════════════════════════════
# 4. POST /api/run/moments  [background task]
# ══════════════════════════════════════════════════════════════════════

def _bg_moments(job_id: str, dna_path: str, brief_path: str,
                transcript_path: str, output_path: str) -> None:
    """
    Background worker for the moments phase.

    Replicates run_moments.run_moments() without the input() gate:
      1. Calls build_agent_context() from run_moments
      2. Runs moment_selector_agent via _run_agent_single_turn()
      3. Calls extract_moments_from_response() from run_moments
      4. Saves moments via save_moments() from models
    """
    _jobs[job_id]["status"] = "running"
    try:
        # Import internal helpers — never main() or check_prerequisites()
        from run_moments import (
            build_agent_context,
            extract_moments_from_response,
        )
        from agents.moment_selector_agent import create_moment_selector_agent
        from models.transcript_result import load_transcript_result
        from services.storage_service import storage_save_moments

        context, video_id, video_url = build_agent_context(
            dna_path, brief_path, transcript_path
        )
        transcript    = load_transcript_result(transcript_path)
        total_segments = transcript.total_segments

        agent         = create_moment_selector_agent()
        response_text = _run_agent_single_turn(agent, context)

        result = extract_moments_from_response(
            response_text, total_segments, video_id, video_url
        )

        if result is None:
            _fail_job(job_id, "Agent did not return a parseable moments block.")
            return

        if result.total_moments == 0:
            notes = result.selection_notes or "(no notes)"
            _fail_job(job_id, f"Agent returned 0 moments. Notes: {notes}")
            return

        storage_save_moments(result)

        _finish_job(job_id, {
            "total_moments":    result.total_moments,
            "segments_reviewed": total_segments,
            "selection_notes":  result.selection_notes,
            "output_file":      "moments_output.json",
        })

    except (Exception, SystemExit) as exc:
        _fail_job(job_id, f"{type(exc).__name__}: {exc}")


@router.post("/run/moments")
async def run_moments_route(background_tasks: BackgroundTasks):
    """
    Kick off the moments-selection phase in a background task.

    Returns a job_id immediately. Poll GET /api/jobs/{job_id} for status.
    Typical duration: 15–40 seconds (full transcript analysis).

    Prerequisites: MessageDNA + CampaignBrief + transcript_output.json
    """
    dna          = _dna_path()
    brief_path   = DATA_DIR / "campaign_brief.json"
    transcript_p = DATA_DIR / "transcript_output.json"

    _require_file(brief_path,   "Campaign brief not found. Run the campaign brief phase first.")
    _require_file(transcript_p, "transcript_output.json not found. Run the transcript phase first.")

    job_id = _new_job("moments")
    background_tasks.add_task(
        _bg_moments,
        job_id,
        str(dna),
        str(brief_path),
        str(transcript_p),
        str(DATA_DIR / "moments_output.json"),
    )

    return JSONResponse(content={
        "success":    True,
        "job_id":     job_id,
        "poll_url":   f"/api/jobs/{job_id}",
        "message":    "Moments phase started. Poll poll_url for status.",
        "eta_seconds": 30,
    })


# ══════════════════════════════════════════════════════════════════════
# 5. POST /api/run/captions  [background task]
# ══════════════════════════════════════════════════════════════════════

class CaptionsRequest(BaseModel):
    batch: Optional[str] = None    # None | "video_clip" | "image_post" | "text_quote"


def _bg_captions(job_id: str, dna_path: str, brief_path: str,
                 moments_path: str, output_path: str,
                 batch: Optional[str]) -> None:
    """
    Background worker for the captions + hashtags phase.

    Replicates run_captions.run_captions() without the input() gate:
      1. Calls build_caption_context / build_batch_context from run_captions
      2. Runs caption_agent via run_agent_once() from run_captions
      3. Runs hashtag_agent via run_agent_once() from run_captions
      4. Merges hashtags and saves via save_post_drafts() from models
    """
    _jobs[job_id]["status"] = "running"
    try:
        from run_captions import (
            build_caption_context,
            build_batch_context,
            build_hashtag_context,
            extract_post_drafts,
            extract_hashtags,
            merge_hashtags,
        )
        from agents.caption_agent  import create_caption_agent
        from agents.hashtag_agent  import create_hashtag_agent
        from services.storage_service import storage_save_post_drafts

        # Step 1: Build context
        if batch:
            context, campaign_id, primary_cta = build_batch_context(
                dna_path, brief_path, moments_path, batch
            )
        else:
            context, campaign_id, primary_cta = build_caption_context(
                dna_path, brief_path, moments_path
            )

        # Step 2: Caption agent — use pipeline's own runner (avoids sys.exit)
        print(f"[captions:{job_id}] context length={len(context)} chars")
        caption_agent    = create_caption_agent()
        caption_response = _run_agent_single_turn(caption_agent, context)
        print(f"[captions:{job_id}] response length={len(caption_response)} chars")

        draft_set = extract_post_drafts(caption_response, campaign_id, primary_cta)

        if draft_set is None:
            snippet = caption_response[:500] if caption_response else "(empty)"
            _fail_job(job_id, f"Caption agent did not return a parseable posts block. Response snippet: {snippet}")
            return

        if draft_set.total_posts == 0:
            notes = draft_set.generation_notes or "(no notes)"
            _fail_job(job_id, f"Caption agent returned 0 posts. Notes: {notes}")
            return

        # Step 3: Hashtag agent
        hashtag_agent   = create_hashtag_agent()
        hashtag_context = build_hashtag_context(draft_set, dna_path, brief_path)
        hashtag_response = _run_agent_single_turn(hashtag_agent, hashtag_context)

        hashtag_map = extract_hashtags(hashtag_response)
        if hashtag_map:
            merge_hashtags(draft_set, hashtag_map)

        # Step 4: Merge batch into existing file or overwrite
        if batch and Path(output_path).exists():
            try:
                existing_data = json.loads(Path(output_path).read_text(encoding="utf-8"))
                if "posts" in existing_data:
                    from models.post_draft import PostDraftSet as _PDS
                    existing_set    = _PDS.from_dict(existing_data)
                    existing_nums   = {p.post_number for p in existing_set.posts}
                    next_num        = max(existing_nums, default=0) + 1
                    for p in draft_set.posts:
                        p.post_number = next_num
                        next_num += 1
                    existing_set.posts.extend(draft_set.posts)
                    existing_set.total_posts        = len(existing_set.posts)
                    existing_set.video_clip_count   = sum(1 for p in existing_set.posts if p.content_type == "video_clip")
                    existing_set.image_post_count   = sum(1 for p in existing_set.posts if p.content_type == "image_post")
                    existing_set.text_quote_count   = sum(1 for p in existing_set.posts if p.content_type == "text_quote")
                    draft_set = existing_set
            except Exception:
                pass  # Fall back to overwriting

        storage_save_post_drafts(draft_set)

        _posts = draft_set.posts if hasattr(draft_set, "posts") and draft_set.posts else []
        _finish_job(job_id, {
            "total_posts":      len(_posts),
            "video_clip_count": sum(1 for p in _posts if getattr(p, "content_type", "") == "video_clip"),
            "image_post_count": sum(1 for p in _posts if getattr(p, "content_type", "") == "image_post"),
            "text_quote_count": sum(1 for p in _posts if getattr(p, "content_type", "") == "text_quote"),
            "hashtags_assigned": len(hashtag_map) if hashtag_map else 0,
            "batch":            batch,
            "output_file":      "posts_output.json",
        })

    except (Exception, SystemExit) as exc:
        _fail_job(job_id, f"{type(exc).__name__}: {exc}")


@router.post("/run/captions")
async def run_captions_route(
    request: CaptionsRequest,
    background_tasks: BackgroundTasks,
):
    """
    Kick off the post-drafts + hashtags phase in a background task.

    Returns a job_id immediately. Poll GET /api/jobs/{job_id} for status.
    Typical duration: 30–90 seconds.

    Optional body field:
        batch: "video_clip" | "image_post" | "text_quote"
        Omit to generate all 15 posts in one run.

    Prerequisites: MessageDNA + CampaignBrief + moments_output.json
    """
    valid_batches = {None, "video_clip", "image_post", "text_quote"}
    if request.batch not in valid_batches:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid batch value '{request.batch}'. "
                   f"Must be one of: video_clip, image_post, text_quote (or omit for all)."
        )

    dna       = _dna_path()
    brief_p   = DATA_DIR / "campaign_brief.json"
    moments_p = DATA_DIR / "moments_output.json"

    _require_file(brief_p,   "Campaign brief not found. Run the campaign brief phase first.")
    _require_file(moments_p, "moments_output.json not found. Run the moments phase first.")

    job_id = _new_job("captions")
    background_tasks.add_task(
        _bg_captions,
        job_id,
        str(dna),
        str(brief_p),
        str(moments_p),
        str(DATA_DIR / "posts_output.json"),
        request.batch,
    )

    return JSONResponse(content={
        "success":     True,
        "job_id":      job_id,
        "poll_url":    f"/api/jobs/{job_id}",
        "batch":       request.batch,
        "message":     "Captions phase started. Poll poll_url for status.",
        "eta_seconds": 60,
    })


# ══════════════════════════════════════════════════════════════════════
# 6. POST /api/run/images  [background task]
# ══════════════════════════════════════════════════════════════════════

class ImagesRequest(BaseModel):
    post_number: Optional[int] = None    # None = all pending; int = regenerate one post
    model: Optional[str]       = None    # override Imagen model


def _bg_images(job_id: str, posts_path: str, images_dir: str,
               model: str, target_post: Optional[int]) -> None:
    """
    Background worker for the image-generation phase.

    run_images.run_images() has no input() gates — safe to call directly.
    We still wrap in try/except to catch sys.exit from check_prerequisites
    (which won't fire if we've already checked posts_output.json exists,
    but defensive wrapping costs nothing).
    """
    _jobs[job_id]["status"] = "running"
    try:
        from run_images import run_images
        from models.post_draft import load_post_draft_set
        from services.storage_service import storage_save_post_drafts, USE_FIRESTORE

        run_images(
            posts_path  = posts_path,
            images_dir  = images_dir,
            model       = model,
            dry_run     = False,
            target_post = target_post,
        )

        # Read the updated posts file to build a useful result summary
        draft_set = load_post_draft_set(posts_path)
        posts     = draft_set.posts

        # Upload freshly generated images to Cloud Storage so image_url
        # is a usable hosted URL (not an ephemeral local path). Upload
        # failure keeps the local path — never fails the job.
        from tools.imagen_tool import upload_image_to_gcs
        uploaded = 0
        for p in posts:
            if (p.asset_status == "image_generated"
                    and p.image_url
                    and not p.image_url.startswith("http")):
                gcs_url = upload_image_to_gcs(p.image_url)
                if gcs_url:
                    p.image_url = gcs_url
                    p.image_storage_status = "cloud_storage"
                    uploaded += 1

        # Persist updated URLs/statuses (local file + Firestore)
        storage_save_post_drafts(draft_set)

        if target_post is not None:
            scope = [p for p in posts if p.post_number == target_post]
        else:
            scope = posts

        generated = [p.post_number for p in scope if p.asset_status == "image_generated"]
        failed    = [p.post_number for p in scope if p.asset_status == "image_generation_failed"]

        _finish_job(job_id, {
            "generated":       generated,
            "failed":          failed,
            "generated_count": len(generated),
            "failed_count":    len(failed),
            "uploaded_to_gcs": uploaded,
            "target_post":     target_post,
            "model":           model,
            "images_dir":      images_dir,
        })

    except (Exception, SystemExit) as exc:
        _fail_job(job_id, f"{type(exc).__name__}: {exc}")


@router.post("/run/images")
async def run_images_route(
    request: ImagesRequest,
    background_tasks: BackgroundTasks,
):
    """
    Kick off the image-generation phase in a background task.

    Returns a job_id immediately. Poll GET /api/jobs/{job_id} for status.
    Typical duration: 10–30 seconds per image.

    Optional body fields:
        post_number: int   — regenerate a single post; omit for all pending posts
        model: str         — override Imagen model (default: imagen-4.0-generate-001)

    Prerequisites: posts_output.json
    """
    posts_path = DATA_DIR / "posts_output.json"
    _require_file(
        posts_path,
        "posts_output.json not found. Run the captions phase first."
    )

    import os
    model = (
        request.model
        or os.getenv("IMAGEN_MODEL", "imagen-4.0-generate-001")
    )

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    job_id = _new_job("images")
    background_tasks.add_task(
        _bg_images,
        job_id,
        str(posts_path),
        str(IMAGES_DIR),
        model,
        request.post_number,
    )

    scope_msg = (
        f"Regenerating post {request.post_number}."
        if request.post_number is not None
        else "Generating all pending_image posts."
    )

    return JSONResponse(content={
        "success":     True,
        "job_id":      job_id,
        "poll_url":    f"/api/jobs/{job_id}",
        "message":     f"{scope_msg} Poll poll_url for status.",
        "model":       model,
        "eta_seconds": 20,
    })


# ══════════════════════════════════════════════════════════════════════
# Job polling
# ══════════════════════════════════════════════════════════════════════

@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """
    Poll a background job.

    Response fields:
        id          str
        phase       str    — "moments" | "captions" | "images"
        status      str    — "pending" | "running" | "done" | "failed"
        result      dict   — populated when status == "done"
        error       str    — populated when status == "failed"
        started_at  str    — ISO 8601 UTC
        finished_at str    — ISO 8601 UTC or null
    """
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return JSONResponse(content=job)


@router.get("/jobs")
async def list_jobs():
    """
    List all jobs in the current server session (most recent first).
    Useful for the dashboard to show pipeline run history.
    """
    sorted_jobs = sorted(
        _jobs.values(),
        key=lambda j: j.get("started_at", ""),
        reverse=True,
    )
    return JSONResponse(content={"jobs": sorted_jobs, "count": len(sorted_jobs)})
