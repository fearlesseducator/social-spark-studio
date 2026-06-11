"""
routes/voice_routes.py

Suggested FastAPI routes for the Voice Conversation Studio.

These are standalone route definitions. Do not assume a specific
app.py structure. Add them to your existing FastAPI app like this:

    from routes.voice_routes import router as voice_router
    app.include_router(voice_router, prefix="/api/voice")

Or copy individual routes directly into your app.py if you prefer.

All routes return JSON. The frontend uses these to drive the
Voice Conversation Studio UI.

Routes:
    POST /api/voice/start                  — start or resume a session
    POST /api/voice/turn                   — send audio or text, get response
    GET  /api/voice/session/{session_id}   — get current session state
    POST /api/voice/confirm/{session_id}   — force-confirm current block (debug)
    GET  /api/voice/status                 — check STT/TTS availability

CORS:
    Voice routes require CORS if your frontend is on a different origin.
    Add to your FastAPI app:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)
"""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import uuid

from services.voice_conversation_service import VoiceConversationService
from models.voice_conversation import VoiceConversationSession
from services.storage_service import (
    storage_load_voice_session,
    storage_reset_session,
)
from tools.speech_to_text_tool import stt_is_configured
from tools.text_to_speech_tool import tts_is_configured

router = APIRouter()

# In-memory session store.
# For production: replace with Redis or Firestore-backed session management.
_sessions: dict[str, VoiceConversationService] = {}


def _get_or_create_service(session_id: str) -> VoiceConversationService:
    """Get an existing service instance or create a new one."""
    if session_id not in _sessions:
        _sessions[session_id] = VoiceConversationService(session_id=session_id)
    return _sessions[session_id]


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class StartRequest(BaseModel):
    session_id: Optional[str] = None  # provide to resume an existing session


class TextTurnRequest(BaseModel):
    session_id: str
    text: str                          # founder's typed response (text fallback)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/start")
async def start_conversation(request: StartRequest):
    """
    Start a new voice conversation or resume an existing one.

    Returns the agent's opening message and audio.
    The frontend should play audio_base64 immediately on load.

    Response fields:
        success           bool
        session_id        str    — use this for all subsequent calls
        agent_text        str    — what the agent said
        audio_base64      str    — MP3 audio, base64-encoded (may be empty)
        audio_available   bool   — true if audio_base64 is populated
        current_block     int    — 1-4
        current_block_name str
        blocks_confirmed  int    — 0-4
        interview_complete bool
        input_mode        str    — "voice" or "text"
    """
    session_id = request.session_id or str(uuid.uuid4())
    service    = _get_or_create_service(session_id)
    response   = service.start()
    return JSONResponse(content=response.__dict__)


@router.post("/turn/audio")
async def voice_turn(
    session_id: str = Form(...),
    audio: UploadFile = File(...),
    sample_rate: int = Form(default=16000),
    encoding: str = Form(default="WEBM_OPUS"),
):
    """
    Process one voice turn.

    The browser sends the audio file recorded by MediaRecorder.
    The service transcribes it, sends to the agent, and returns
    the agent's response with audio.

    Frontend usage:
        const formData = new FormData()
        formData.append("session_id", sessionId)
        formData.append("audio", audioBlob, "recording.webm")
        const response = await fetch("/api/voice/turn/audio", {
            method: "POST", body: formData
        })
        const data = await response.json()
        if (data.audio_available) playAudio(data.audio_base64)
        else displayText(data.agent_text)

    Response fields: same as /start plus:
        transcript        str    — what the founder said (STT output)
        awaiting_confirmation bool — true when agent just read a summary
    """
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found. Call /start first.")

    audio_bytes = await audio.read()
    service     = _sessions[session_id]
    response    = service.process_turn(
        audio_bytes=audio_bytes,
        sample_rate_hz=sample_rate,
        audio_encoding=encoding,
    )
    return JSONResponse(content=response.__dict__)


@router.post("/turn/text")
async def text_turn(request: TextTurnRequest):
    """
    Process one text turn (fallback mode).

    Used when:
    - Microphone permission denied
    - Browser doesn't support Web Audio API
    - STT is not configured
    - Founder prefers typing

    Same response structure as /turn/audio.
    """
    if request.session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found. Call /start first.")

    service  = _sessions[request.session_id]
    response = service.process_turn(text_input=request.text)
    return JSONResponse(content=response.__dict__)


@router.get("/session/{session_id}")
async def get_session_state(session_id: str):
    """
    Get the current state of a session.

    Useful for the frontend to:
    - Restore UI after a page refresh
    - Show block completion indicators
    - Check if interview is complete

    Response fields:
        session_id          str
        input_mode          str
        blocks_confirmed    int
        interview_complete  bool
        current_block       int
        current_block_name  str
        blocks              list — each block's confirmation status
    """
    voice_session = storage_load_voice_session() or VoiceConversationSession()

    blocks_summary = [
        {
            "block_number":    b.block_number,
            "block_name":      b.block_name,
            "confirmed":       b.confirmed,
            "exchange_count":  len(b.exchanges),
        }
        for b in voice_session.blocks
    ]

    current = voice_session.current_block()

    return JSONResponse(content={
        "session_id":        voice_session.session_id or session_id,
        "input_mode":        voice_session.input_mode,
        "blocks_confirmed":  voice_session.confirmed_block_count,
        "interview_complete": voice_session.is_complete,
        "current_block":     current.block_number if current else 0,
        "current_block_name": current.block_name if current else "",
        "blocks":            blocks_summary,
    })


@router.post("/reset")
async def reset_session():
    """
    Reset the founder's session — deletes all stored documents
    (Firestore mode) and local data files, plus in-memory services.
    The next /start begins a fresh interview.
    """
    storage_reset_session()
    _sessions.clear()
    return JSONResponse(content={
        "success": True,
        "message": "Session reset. All stored interview and campaign data deleted.",
    })


@router.get("/status")
async def voice_status():
    """
    Check whether STT and TTS APIs are configured and available.

    The frontend calls this on load to decide whether to show
    the microphone UI or fall back to text input immediately.

    Response:
        stt_available   bool   — Speech-to-Text configured
        tts_available   bool   — Text-to-Speech configured
        voice_ready     bool   — both STT and TTS available
        fallback_mode   bool   — true when voice is not available
        message         str    — human-readable status
    """
    stt = stt_is_configured()
    tts = tts_is_configured()
    voice_ready = stt and tts

    return JSONResponse(content={
        "stt_available":  stt,
        "tts_available":  tts,
        "voice_ready":    voice_ready,
        "fallback_mode":  not voice_ready,
        "message": (
            "Voice conversation ready."
            if voice_ready
            else "Voice unavailable. Text fallback active."
        ),
    })
