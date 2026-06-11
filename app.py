"""
app.py — Social Spark Studio

FastAPI application.  Serves Jinja2 HTML templates from templates/
and static files from static/.  All existing run_*.py CLI scripts
are completely untouched.

Start:
    uvicorn app:app --reload --port 8000

Then open: http://localhost:8000
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Windows UTF-8 fix
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import jinja2 as _jinja2
from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# ── Voice routes ──────────────────────────────────────────────────────
# Import lazily so missing GCP credentials never crash startup.
try:
    from routes.voice_routes import router as voice_router
    _voice_available = True
except Exception as _voice_import_err:
    print(f"[voice] Routes not loaded: {_voice_import_err}")
    _voice_available = False

# ── Pipeline routes ───────────────────────────────────────────────────
# Lazy import — won't crash if an optional dependency is missing.
try:
    from routes.pipeline_routes import router as pipeline_router
    _pipeline_available = True
except Exception as _pipeline_import_err:
    print(f"[pipeline] Routes not loaded: {_pipeline_import_err}")
    _pipeline_available = False

# ── App ───────────────────────────────────────────────────────────────

app = FastAPI(title="Social Spark Studio", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Jinja2 setup (direct, no Starlette wrapper) ───────────────────────
# Starlette's Jinja2Templates has a cache-key bug with Python 3.14.
# We render templates directly instead.
_jinja_env = _jinja2.Environment(
    loader=_jinja2.FileSystemLoader("templates"),
    autoescape=_jinja2.select_autoescape(["html", "xml"]),
)


def render(template_name: str, **ctx) -> HTMLResponse:
    """Render a Jinja2 template and return an HTMLResponse."""
    t = _jinja_env.get_template(template_name)
    return HTMLResponse(t.render(**ctx))


# Custom Jinja2 filter: extract the filename from a path string.
# Used in posts.html to turn "data/generated_images/post_003.png"
# into "post_003.png" so we can build the /api/data/images/ URL.
import os.path as _ospath
_jinja_env.filters["basename"] = lambda p: _ospath.basename(p) if p else ""


if _voice_available:
    app.include_router(voice_router, prefix="/voice")
    print("[voice] Routes registered at /voice")

if _pipeline_available:
    app.include_router(pipeline_router, prefix="/api")
    print("[pipeline] Routes registered at /api")

# ── Data helpers ──────────────────────────────────────────────────────
# Storage strategy:
#   Local/demo: all generated outputs (JSON, images, CSVs) live in data/.
#   TODO(cloud): Cloud Run's filesystem is ephemeral — outputs are lost on
#   restart/scale. For persistence, upload generated images and CSVs to the
#   GCS bucket in GCS_BUCKET_NAME and serve public URLs instead of local paths.

DATA_DIR = Path("data")


def _load_json(filename: str):
    """
    Load a JSON file from data/. Returns None if missing.

    For message_dna_output.json: also checks test_message_dna_output.json
    as a fallback (the CLI uses that name during development).
    """
    path = DATA_DIR / filename
    if not path.exists():
        # Fallback: test_ prefix variant (used by CLI runners during dev)
        alt = DATA_DIR / ("test_" + filename)
        if alt.exists():
            path = alt
        else:
            return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _compute_workflow_steps(posts_data=None):
    """
    Build the workflow step list with real statuses from data/ files.

    Returns list of dicts:
        key, label, icon, status, href, desc
    Status values: 'complete' | 'ready' | 'in_progress' | 'needs_attention' | 'todo'
    """
    dna_ok        = (DATA_DIR / "message_dna_output.json").exists() or \
                    (DATA_DIR / "test_message_dna_output.json").exists()
    brief_ok      = (DATA_DIR / "campaign_brief.json").exists()
    transcript_ok = (DATA_DIR / "transcript_output.json").exists()
    moments_ok    = (DATA_DIR / "moments_output.json").exists()
    posts_ok      = (DATA_DIR / "posts_output.json").exists()
    csv_ok        = bool(list(DATA_DIR.glob("social-spark-*.csv")))

    # Image status from posts
    image_status = "todo"
    if posts_data:
        posts     = posts_data.get("posts", [])
        img_posts = [p for p in posts if p.get("content_type") == "image_post"]
        if img_posts:
            gen     = sum(1 for p in img_posts if p.get("asset_status") == "image_generated")
            failed  = sum(1 for p in img_posts if p.get("asset_status") == "image_generation_failed")
            pending = sum(1 for p in img_posts if p.get("asset_status") == "pending_image")
            if failed > 0:
                image_status = "needs_attention"
            elif pending == 0 and gen > 0:
                image_status = "complete"
            elif gen > 0 or pending > 0:
                image_status = "in_progress"
            else:
                image_status = "todo"

    return [
        {
            "key":    "dna",
            "label":  "MessageDNA",
            "icon":   "dna",
            "status": "complete" if dna_ok else "todo",
            "href":   "/voice",
            "desc":   "Your founder voice, beliefs, offers, and CTA style.",
        },
        {
            "key":    "brief",
            "label":  "Campaign Brief",
            "icon":   "clipboard-list",
            "status": "complete" if brief_ok else ("ready" if dna_ok else "todo"),
            "href":   "/campaign",
            "desc":   "Campaign theme & narrative arc.",
        },
        {
            "key":    "transcript",
            "label":  "YouTube Transcript",
            "icon":   "youtube",
            "status": "complete" if transcript_ok else "todo",
            "href":   "/youtube",
            "desc":   "Extracted from your YouTube source.",
        },
        {
            "key":    "moments",
            "label":  "Moments",
            "icon":   "sparkles",
            "status": "complete" if moments_ok else "todo",
            "href":   "/moments",
            "desc":   "Strongest segments selected.",
        },
        {
            "key":    "drafts",
            "label":  "Post Drafts",
            "icon":   "file-text",
            "status": "complete" if posts_ok else "todo",
            "href":   "/posts",
            "desc":   f"{len(posts_data.get('posts', [])) if posts_data else 0} posts ready for review.",
        },
        {
            "key":    "images",
            "label":  "Images",
            "icon":   "image",
            "status": image_status,
            "href":   "/images",
            "desc":   "Image generation for image_post content.",
        },
        {
            "key":    "csv",
            "label":  "CSV Export",
            "icon":   "download",
            "status": "complete" if csv_ok else ("ready" if posts_ok else "todo"),
            "href":   "/export",
            "desc":   "Scheduler-ready file for Buffer / Hypefury.",
        },
    ]


# ── Routes ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return render("landing.html", year=datetime.now().year)


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return render("login.html", login_error="", demo_gated=bool(os.getenv("DEMO_PASSWORD")))


@app.post("/login")
async def login_post(
    request: Request,
    email: str = Form(default=""),
    password: str = Form(default=""),
):
    # Demo gate — if DEMO_PASSWORD is set, the submitted password must match.
    # If unset (local dev), login stays open. No real auth, users, or sessions.
    demo_password = os.getenv("DEMO_PASSWORD", "")
    if demo_password and password != demo_password:
        return render(
            "login.html",
            login_error="That password didn't match. Ask the team for the demo password.",
            demo_gated=True,
        )
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    posts_data = _load_json("posts_output.json")
    workflow   = _compute_workflow_steps(posts_data)
    completed  = sum(1 for s in workflow if s["status"] == "complete")
    pct        = round(completed / len(workflow) * 100) if workflow else 0

    # Extra context for the footer note
    post_count     = len(posts_data.get("posts", [])) if posts_data else 0
    failed_images  = 0
    if posts_data:
        failed_images = sum(
            1 for p in posts_data.get("posts", [])
            if p.get("asset_status") == "image_generation_failed"
        )

    return render("dashboard.html",
        active_page="dashboard",
        workflow=workflow, completed=completed,
        total=len(workflow), pct=pct,
        post_count=post_count, failed_images=failed_images,
    )


@app.get("/voice", response_class=HTMLResponse)
async def voice_studio(request: Request):
    # Check voice availability
    voice_status = {
        "voice_ready":   False,
        "stt_available": False,
        "tts_available": False,
        "fallback_mode": True,
        "message":       "Voice unavailable. Text fallback active.",
    }
    if _voice_available:
        try:
            from tools.speech_to_text_tool import stt_is_configured
            from tools.text_to_speech_tool  import tts_is_configured
            stt = stt_is_configured()
            tts = tts_is_configured()
            voice_status = {
                "voice_ready":   stt and tts,
                "stt_available": stt,
                "tts_available": tts,
                "fallback_mode": not (stt and tts),
                "message": "Voice ready." if (stt and tts) else "Text fallback active.",
            }
        except Exception:
            pass

    return render("voice.html", active_page="voice", voice_status=voice_status)


@app.get("/messagedna", response_class=HTMLResponse)
async def messagedna(request: Request):
    dna = _load_json("message_dna_output.json")
    return render("messagedna.html", active_page="messagedna", dna=dna)


@app.get("/campaign", response_class=HTMLResponse)
async def campaign(request: Request):
    brief = _load_json("campaign_brief.json")
    if brief is None:
        # Fresh container — pull the brief down from Firestore if present
        try:
            from services.storage_service import hydrate_local_file
            if hydrate_local_file("campaign_brief.json"):
                brief = _load_json("campaign_brief.json")
        except Exception:
            pass
    return render("campaign.html", active_page="campaign", brief=brief)


@app.get("/posts", response_class=HTMLResponse)
async def posts(request: Request):
    posts_data = _load_json("posts_output.json")
    if posts_data:
        all_posts   = posts_data.get("posts", [])
        video_count = sum(1 for p in all_posts if p.get("content_type") == "video_clip")
        image_count = sum(1 for p in all_posts if p.get("content_type") == "image_post")
        text_count  = sum(1 for p in all_posts if p.get("content_type") == "text_quote")
        gen_count   = sum(1 for p in all_posts if p.get("asset_status") == "image_generated")
        fail_count  = sum(1 for p in all_posts if p.get("asset_status") == "image_generation_failed")
        pend_count  = sum(1 for p in all_posts if p.get("asset_status") == "pending_image")
    else:
        all_posts = video_count = image_count = text_count = gen_count = fail_count = pend_count = 0

    return render("posts.html",
        active_page="posts",
        posts=all_posts if posts_data else [],
        video_count=video_count, image_count=image_count,
        text_count=text_count,  gen_count=gen_count,
        fail_count=fail_count,  pend_count=pend_count,
    )


@app.get("/images", response_class=HTMLResponse)
async def images_page(request: Request):
    # Redirect to posts for now — images are shown inline
    return RedirectResponse(url="/posts", status_code=302)


@app.get("/youtube", response_class=HTMLResponse)
async def youtube_page(request: Request):
    transcript = _load_json("transcript_output.json")
    brief      = _load_json("campaign_brief.json")
    brief_url  = (brief or {}).get("youtube_url", "")
    return render("youtube.html", active_page="youtube",
                  transcript=transcript, brief_url=brief_url)


@app.get("/moments", response_class=HTMLResponse)
async def moments_page(request: Request):
    moments_data = _load_json("moments_output.json")
    return render("moments.html", active_page="moments", moments=moments_data)


@app.get("/export", response_class=HTMLResponse)
async def export_page(request: Request):
    posts_data  = _load_json("posts_output.json")
    csv_paths   = sorted(DATA_DIR.glob("social-spark-*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)

    csv_files = []
    for p in csv_paths:
        stat = p.stat()
        csv_files.append({
            "name":     p.name,
            "size":     round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })

    post_summary = None
    if posts_data:
        all_posts = posts_data.get("posts", [])
        post_summary = {
            "total":        len(all_posts),
            "video_clips":  sum(1 for p in all_posts if p.get("content_type") == "video_clip"),
            "image_posts":  sum(1 for p in all_posts if p.get("content_type") == "image_post"),
            "text_quotes":  sum(1 for p in all_posts if p.get("content_type") == "text_quote"),
            "images_ready": sum(1 for p in all_posts if p.get("asset_status") == "image_generated"),
            "images_failed":sum(1 for p in all_posts if p.get("asset_status") == "image_generation_failed"),
        }

    return render("export.html",
        active_page="export", csv_files=csv_files, post_summary=post_summary,
    )


@app.get("/download-csv")
async def download_csv(file: str = ""):
    """Serve a CSV file as a download."""
    if not file:
        # Find the most recent CSV
        csv_paths = sorted(DATA_DIR.glob("social-spark-*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not csv_paths:
            return HTMLResponse("<p>No CSV file found. Run <code>python run_export.py</code> first.</p>", status_code=404)
        path = csv_paths[0]
    else:
        # Safety: strip any path separators so callers can't escape DATA_DIR
        safe_name = Path(file).name
        path = DATA_DIR / safe_name

    if not path.exists() or not path.suffix == ".csv":
        return HTMLResponse("<p>File not found.</p>", status_code=404)

    return FileResponse(
        path=str(path),
        media_type="text/csv",
        filename=path.name,
    )


# ── Stub pages (CLI-driven phases) ─────────────────────────────────────
# These phases are still run from the CLI. These routes just show the
# current data file contents so the UI stays navigable.

@app.get("/help", response_class=HTMLResponse)
async def help_page(request: Request):
    return render("help.html", active_page="help")


@app.get("/signup", response_class=HTMLResponse)
async def signup(request: Request):
    # Demo — same as login
    return RedirectResponse(url="/login", status_code=302)


# ── API endpoints (JSON) ───────────────────────────────────────────────
# Used by the voice JS and future frontend enhancements.

@app.get("/health")
async def health():
    """
    Health check for Cloud Run / load balancers.
    Always returns 200 if the process is alive — optional credentials
    (GCP voice, pipeline deps) only affect the feature flags, never the status.
    """
    return {
        "status": "ok",
        "app": "Social Spark Studio",
        "pipeline_routes": _pipeline_available,
        "voice_routes": _voice_available,
    }


@app.get("/api/status")
async def api_status():
    """Quick health check — confirms the server is running."""
    posts_data = _load_json("posts_output.json")
    return {
        "status":     "ok",
        "data_files": {
            "message_dna":  (DATA_DIR / "message_dna_output.json").exists()
                             or (DATA_DIR / "test_message_dna_output.json").exists(),
            "campaign":     (DATA_DIR / "campaign_brief.json").exists(),
            "transcript":   (DATA_DIR / "transcript_output.json").exists(),
            "moments":      (DATA_DIR / "moments_output.json").exists(),
            "posts":        (DATA_DIR / "posts_output.json").exists(),
            "post_count":   len(posts_data.get("posts", [])) if posts_data else 0,
        },
        "voice_routes": _voice_available,
    }


# ── Entry point ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
