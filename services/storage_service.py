"""
services/storage_service.py

Smart storage router — Firestore on Cloud Run, local JSON files for
local development. Routes call these functions instead of calling
model save/load functions directly.

Mode switch:
    USE_FIRESTORE=true   → Firestore is the source of truth
    USE_FIRESTORE=false  → local data/ JSON files only (default)

Design — write-through + hydrate-on-load:
    The untouched run_*.py helpers (context builders, run_images) read
    local file paths. So in Firestore mode:
      - every save writes BOTH the local file and the Firestore document
      - every load reads Firestore first and re-materialises the local
        file ("hydration") so path-based pipeline helpers keep working
        on a fresh Cloud Run container
    The local file acts as a per-container cache; Firestore is the
    durable copy that survives restarts.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from models.message_dna import (
    MessageDNA,
    save_message_dna,
    load_message_dna,
    save_message_dna_firestore,
    load_message_dna_firestore,
)
from models.campaign_brief import (
    CampaignBrief,
    save_campaign_brief,
    load_campaign_brief,
    save_campaign_brief_firestore,
    load_campaign_brief_firestore,
)
from models.transcript_result import (
    TranscriptResult,
    save_transcript_result,
    load_transcript_result,
    save_transcript_firestore,
    load_transcript_firestore,
)
from models.transcript_moment import (
    MomentSelectionResult,
    save_moments,
    load_moments,
    save_moments_firestore,
    load_moments_firestore,
)
from models.post_draft import (
    PostDraftSet,
    save_post_draft_set,
    load_post_draft_set,
    save_post_draft_set_firestore,
    load_post_draft_set_firestore,
)
from models.voice_conversation import (
    VoiceConversationSession,
    save_voice_session,
    load_voice_session,
    save_voice_session_firestore,
    load_voice_session_firestore,
)

USE_FIRESTORE = bool(os.getenv("USE_FIRESTORE", "false").lower() == "true")

# ── Local file paths (single source of truth for filenames) ────────────

DATA_DIR = Path("data")

DNA_PATH        = str(DATA_DIR / "message_dna_output.json")
BRIEF_PATH      = str(DATA_DIR / "campaign_brief.json")
TRANSCRIPT_PATH = str(DATA_DIR / "transcript_output.json")
MOMENTS_PATH    = str(DATA_DIR / "moments_output.json")
POSTS_PATH      = str(DATA_DIR / "posts_output.json")
VOICE_PATH      = os.getenv("VOICE_STATE_PATH", str(DATA_DIR / "voice_conversation_state.json"))

ALL_COLLECTIONS = [
    "message_dna", "campaign_brief", "transcript",
    "moments", "post_drafts", "voice_session",
]


# ── MessageDNA ──────────────────────────────────────────────────────────

def storage_save_message_dna(dna: MessageDNA, founder_id: str = "default_founder") -> None:
    save_message_dna(dna, DNA_PATH)
    if USE_FIRESTORE:
        save_message_dna_firestore(dna, founder_id)


def storage_load_message_dna(founder_id: str = "default_founder") -> MessageDNA | None:
    if USE_FIRESTORE:
        dna = load_message_dna_firestore(founder_id)
        if dna is not None:
            save_message_dna(dna, DNA_PATH)   # hydrate local cache
            return dna
    if Path(DNA_PATH).exists():
        return load_message_dna(DNA_PATH)
    return None


# ── CampaignBrief ───────────────────────────────────────────────────────

def storage_save_campaign_brief(brief: CampaignBrief, founder_id: str = "default_founder") -> None:
    save_campaign_brief(brief, BRIEF_PATH)
    if USE_FIRESTORE:
        save_campaign_brief_firestore(brief, founder_id)


def storage_load_campaign_brief(founder_id: str = "default_founder") -> CampaignBrief | None:
    if USE_FIRESTORE:
        brief = load_campaign_brief_firestore(founder_id)
        if brief is not None:
            save_campaign_brief(brief, BRIEF_PATH)   # hydrate local cache
            return brief
    if Path(BRIEF_PATH).exists():
        return load_campaign_brief(BRIEF_PATH)
    return None


# ── TranscriptResult ────────────────────────────────────────────────────

def storage_save_transcript(result: TranscriptResult, founder_id: str = "default_founder") -> None:
    save_transcript_result(result, TRANSCRIPT_PATH)
    if USE_FIRESTORE:
        save_transcript_firestore(result, founder_id)


def storage_load_transcript(founder_id: str = "default_founder") -> TranscriptResult | None:
    if USE_FIRESTORE:
        result = load_transcript_firestore(founder_id)
        if result is not None:
            save_transcript_result(result, TRANSCRIPT_PATH)   # hydrate
            return result
    if Path(TRANSCRIPT_PATH).exists():
        return load_transcript_result(TRANSCRIPT_PATH)
    return None


# ── Moments ─────────────────────────────────────────────────────────────

def storage_save_moments(result: MomentSelectionResult, founder_id: str = "default_founder") -> None:
    save_moments(result, MOMENTS_PATH)
    if USE_FIRESTORE:
        save_moments_firestore(result, founder_id)


def storage_load_moments(founder_id: str = "default_founder") -> MomentSelectionResult | None:
    if USE_FIRESTORE:
        result = load_moments_firestore(founder_id)
        if result is not None:
            save_moments(result, MOMENTS_PATH)   # hydrate
            return result
    if Path(MOMENTS_PATH).exists():
        return load_moments(MOMENTS_PATH)
    return None


# ── Post drafts ─────────────────────────────────────────────────────────

def storage_save_post_drafts(draft_set: PostDraftSet, founder_id: str = "default_founder") -> None:
    save_post_draft_set(draft_set, POSTS_PATH)
    if USE_FIRESTORE:
        save_post_draft_set_firestore(draft_set, founder_id)


def storage_load_post_drafts(founder_id: str = "default_founder") -> PostDraftSet | None:
    if USE_FIRESTORE:
        draft_set = load_post_draft_set_firestore(founder_id)
        if draft_set is not None:
            save_post_draft_set(draft_set, POSTS_PATH)   # hydrate
            return draft_set
    if Path(POSTS_PATH).exists():
        return load_post_draft_set(POSTS_PATH)
    return None


# ── Voice session ───────────────────────────────────────────────────────

def storage_save_voice_session(session: VoiceConversationSession, founder_id: str = "default_founder") -> None:
    save_voice_session(session, VOICE_PATH)
    if USE_FIRESTORE:
        save_voice_session_firestore(session, founder_id)


def storage_load_voice_session(founder_id: str = "default_founder") -> VoiceConversationSession | None:
    if USE_FIRESTORE:
        session = load_voice_session_firestore(founder_id)
        if session is not None:
            save_voice_session(session, VOICE_PATH)   # hydrate
            return session
    if Path(VOICE_PATH).exists():
        return load_voice_session(VOICE_PATH)
    return None


# ── Reset ───────────────────────────────────────────────────────────────

def storage_reset_session(founder_id: str = "default_founder") -> None:
    """Delete all documents for this founder across all collections,
    plus the local cached files."""
    if USE_FIRESTORE:
        from tools.firestore_tool import delete_document
        for collection in ALL_COLLECTIONS:
            delete_document(collection, founder_id)

    for path in [DNA_PATH, BRIEF_PATH, TRANSCRIPT_PATH, MOMENTS_PATH, POSTS_PATH, VOICE_PATH]:
        p = Path(path)
        if p.exists():
            p.unlink()


# ── Hydration helper for path-based pipeline routes ────────────────────

_HYDRATORS = {
    "message_dna_output.json":         storage_load_message_dna,
    "campaign_brief.json":             storage_load_campaign_brief,
    "transcript_output.json":          storage_load_transcript,
    "moments_output.json":             storage_load_moments,
    "posts_output.json":               storage_load_post_drafts,
    "voice_conversation_state.json":   storage_load_voice_session,
}


def hydrate_local_file(filename: str) -> bool:
    """
    If the named data file is missing locally but exists in Firestore,
    pull it down so path-based pipeline helpers can read it.
    Returns True if the file now exists locally.
    """
    if not USE_FIRESTORE:
        return False
    loader = _HYDRATORS.get(filename)
    if loader is None:
        return False
    try:
        return loader() is not None
    except Exception as exc:
        print(f"[storage] hydration failed for {filename}: {exc}")
        return False
