"""
services/voice_conversation_service.py

Orchestrates the full voice conversation loop.

This service sits between the FastAPI routes and the three tools
(STT, ADK agent, TTS). It handles:

    1. Receiving audio bytes or text from the frontend
    2. Transcribing audio via speech_to_text_tool (or using text directly)
    3. Sending the transcript to voice_conversation_agent via ADK runner
    4. Detecting block completion tags in the agent response
    5. Updating and saving VoiceConversationSession state
    6. Synthesising the agent's response via text_to_speech_tool
    7. Returning a structured response the frontend can act on
    8. On interview completion: writing message_dna_output.json

The frontend only needs to:
    - Send audio bytes (or text) + session_id
    - Receive agent_text + audio_base64 + session_state
    - Play the audio and display the text
    - Show confirmation UI when awaiting_confirmation is True

Fallback behaviour:
    - If STT fails: service sets input_mode="text", frontend shows
      text input instead of microphone
    - If TTS fails: service returns agent_text only, frontend
      displays it as text — interview continues unblocked

Storage paths:
    data/voice_conversation_state.json  — session state (resumed on restart)
    data/message_dna_output.json        — written when interview completes
"""

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

# ADK imports
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

# Local imports
from agents.voice_conversation_agent import create_voice_conversation_agent
from models.voice_conversation import VoiceConversationSession
from services.storage_service import (
    storage_save_voice_session,
    storage_load_voice_session,
    USE_FIRESTORE,
)
from tools.speech_to_text_tool import transcribe_audio, stt_is_configured
from tools.text_to_speech_tool import synthesise_speech, tts_is_configured


def _load_session() -> VoiceConversationSession:
    """Load the voice session via the storage router; fresh session if none."""
    return storage_load_voice_session() or VoiceConversationSession()


def _save_session(session: VoiceConversationSession) -> None:
    """Save the voice session via the storage router (local + Firestore)."""
    storage_save_voice_session(session)

# ---------------------------------------------------------------------------
# Storage paths
# ---------------------------------------------------------------------------

VOICE_STATE_PATH = os.getenv("VOICE_STATE_PATH", "data/voice_conversation_state.json")
MESSAGE_DNA_PATH = os.getenv("MESSAGE_DNA_OUTPUT_PATH", "data/message_dna_output.json")
APP_NAME         = "social_spark_studio_voice"
USER_ID          = "local_founder"

# ---------------------------------------------------------------------------
# Response type returned to FastAPI routes
# ---------------------------------------------------------------------------

@dataclass
class VoiceConversationResponse:
    """
    Complete response from one conversation turn.
    The FastAPI route serialises this to JSON and returns it to the frontend.
    """
    success: bool

    # Text content
    agent_text: str = ""            # Agent's response — always present
    transcript: str = ""            # What the founder said (STT output)

    # Audio content
    audio_base64: str = ""          # Base64-encoded MP3 (empty if TTS failed)
    audio_available: bool = False   # True when audio_base64 is populated

    # Session state
    session_id: str = ""
    current_block: int = 0          # 1-4
    current_block_name: str = ""
    blocks_confirmed: int = 0
    interview_complete: bool = False
    awaiting_confirmation: bool = False  # True when agent just read a summary

    # Input mode
    input_mode: str = "voice"       # "voice" | "text"

    # Error
    error_message: str = ""


# ---------------------------------------------------------------------------
# Tag extraction helpers
# ---------------------------------------------------------------------------

def _extract_block_complete(text: str) -> Optional[dict]:
    """
    Find and parse a <block_N_complete>...</block_N_complete> tag
    in the agent response. Returns the parsed dict or None.
    """
    pattern = re.compile(
        r"<block_(\d)_complete>\s*(.*?)\s*</block_\d_complete>",
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(2).strip())
    except json.JSONDecodeError:
        return None


def _extract_interview_complete(text: str) -> bool:
    """Return True if the agent output a <voice_interview_complete> tag."""
    return "<voice_interview_complete>" in text


def _strip_tags(text: str) -> str:
    """Remove JSON tags from agent text before sending to TTS."""
    # Remove all <tag>...</tag> blocks
    text = re.sub(r"<[^>]+>.*?</[^>]+>", "", text, flags=re.DOTALL)
    return text.strip()


def _get_agent_text(events) -> str:
    """Collect full text from ADK event stream."""
    full_text = ""
    for event in events:
        if hasattr(event, "content") and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    full_text += part.text
    return full_text


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------

class VoiceConversationService:
    """
    Manages one voice conversation session.

    Instantiate once per session. The session_id ties together
    the ADK runner state and the saved VoiceConversationSession.

    Usage in FastAPI:
        service = VoiceConversationService(session_id=session_id)
        response = await service.process_turn(audio_bytes=bytes_from_browser)
        # or for text fallback:
        response = await service.process_turn(text_input="founder typed this")
    """

    def __init__(self, session_id: str = ""):
        self.session_id   = session_id or str(uuid.uuid4())
        self.agent        = create_voice_conversation_agent()
        self.runner       = InMemoryRunner(agent=self.agent, app_name=APP_NAME)
        self._adk_session = None
        self._session_created = False

    def _ensure_adk_session(self) -> None:
        """Create the ADK session on first use."""
        if not self._session_created:
            self.runner.session_service.create_session_sync(
                app_name=APP_NAME,
                user_id=USER_ID,
                session_id=self.session_id,
            )
            self._session_created = True

    def _send_to_agent(self, text: str) -> str:
        """Send text to the ADK agent and return the full response."""
        self._ensure_adk_session()
        events = self.runner.run(
            user_id=USER_ID,
            session_id=self.session_id,
            new_message=genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=text)],
            ),
        )
        return _get_agent_text(events)

    def start(self) -> VoiceConversationResponse:
        """
        Start a new conversation or resume an existing one.
        Call this when the frontend loads the Voice Conversation Studio.
        """
        # Load or create session state
        voice_session = _load_session()

        if not voice_session.session_id:
            voice_session.session_id = self.session_id
            _save_session(voice_session)

        # If already complete, return completion state
        if voice_session.is_complete:
            return VoiceConversationResponse(
                success=True,
                session_id=self.session_id,
                agent_text="Your MessageDNA is already complete. You can start a campaign anytime.",
                blocks_confirmed=4,
                interview_complete=True,
                input_mode=voice_session.input_mode,
            )

        # Send opening trigger to agent
        trigger = "Hello, I'm ready to start my MessageDNA interview."
        if voice_session.confirmed_block_count > 0:
            trigger = (
                f"I'm resuming my interview. "
                f"I've completed blocks {list(range(1, voice_session.confirmed_block_count + 1))}. "
                f"Please continue from block {voice_session.confirmed_block_count + 1}."
            )

        agent_text = self._send_to_agent(trigger)

        # Build TTS audio
        spoken_text = _strip_tags(agent_text)
        tts_result  = synthesise_speech(spoken_text)

        current = voice_session.current_block()

        return VoiceConversationResponse(
            success=True,
            session_id=self.session_id,
            agent_text=spoken_text,
            audio_base64=tts_result.audio_base64 if tts_result.success else "",
            audio_available=tts_result.success,
            current_block=current.block_number if current else 0,
            current_block_name=current.block_name if current else "",
            blocks_confirmed=voice_session.confirmed_block_count,
            input_mode=voice_session.input_mode,
        )

    def process_turn(
        self,
        audio_bytes: bytes = b"",
        text_input: str = "",
        sample_rate_hz: int = 16000,
        audio_encoding: str = "WEBM_OPUS",
    ) -> VoiceConversationResponse:
        """
        Process one conversation turn.

        Pass either audio_bytes (voice mode) or text_input (text fallback).
        Never both. If both provided, audio takes priority.

        Returns a VoiceConversationResponse the FastAPI route serialises
        and returns to the frontend.
        """
        voice_session = _load_session()
        input_mode    = voice_session.input_mode

        # ── Step 1: Transcribe audio or use text directly ──────────
        transcript = ""
        if audio_bytes:
            if not stt_is_configured():
                # Fall back to text mode gracefully
                input_mode = "text"
                voice_session.input_mode = "text"
                _save_session(voice_session)
                return VoiceConversationResponse(
                    success=False,
                    session_id=self.session_id,
                    input_mode="text",
                    error_message=(
                        "Voice unavailable: GOOGLE_CLOUD_PROJECT not configured. "
                        "Please type your answer instead."
                    ),
                )

            stt_result = transcribe_audio(
                audio_bytes=audio_bytes,
                sample_rate_hz=sample_rate_hz,
                encoding=audio_encoding,
            )
            if not stt_result.success:
                return VoiceConversationResponse(
                    success=False,
                    session_id=self.session_id,
                    input_mode=input_mode,
                    error_message=f"Transcription failed: {stt_result.error_message}",
                )
            transcript = stt_result.transcript

        elif text_input:
            transcript = text_input.strip()
            input_mode = "text"
        else:
            return VoiceConversationResponse(
                success=False,
                session_id=self.session_id,
                error_message="No audio or text input provided.",
            )

        if not transcript:
            return VoiceConversationResponse(
                success=False,
                session_id=self.session_id,
                input_mode=input_mode,
                error_message="No speech detected. Please try again.",
            )

        # ── Step 2: Send transcript to agent ──────────────────────
        agent_response = self._send_to_agent(transcript)

        # ── Step 3: Detect block completion ───────────────────────
        block_data = _extract_block_complete(agent_response)
        if block_data:
            block_num        = block_data.get("block_number", 0)
            extracted_fields = block_data.get("extracted_fields", {})
            summary          = block_data.get("summary", "")
            block_obj        = voice_session.get_block(block_num)
            if block_obj:
                block_obj.summary_read_back = summary
                block_obj.confirm(extracted_fields)
                _save_session(voice_session)

        # ── Step 4: Detect interview completion ───────────────────
        interview_done = _extract_interview_complete(agent_response)
        if interview_done:
            self._save_message_dna(voice_session)
            self._save_campaign_brief(voice_session)

        # ── Step 5: Log this exchange ──────────────────────────────
        current_block = voice_session.current_block()
        if current_block and not interview_done:
            current_block.add_exchange(
                agent_question="",   # agent's question was in previous turn
                founder_response=transcript,
                input_mode=input_mode,
            )
            _save_session(voice_session)

        # ── Step 6: Synthesise speech ──────────────────────────────
        spoken_text = _strip_tags(agent_response)
        tts_result  = synthesise_speech(spoken_text)

        # ── Step 7: Build response ─────────────────────────────────
        current = voice_session.current_block()
        awaiting = (
            block_data is not None or
            "does that sound right" in agent_response.lower() or
            "anything you'd like to adjust" in agent_response.lower()
        )

        return VoiceConversationResponse(
            success=True,
            agent_text=spoken_text,
            transcript=transcript,
            audio_base64=tts_result.audio_base64 if tts_result.success else "",
            audio_available=tts_result.success,
            session_id=self.session_id,
            current_block=current.block_number if current else 0,
            current_block_name=current.block_name if current else "",
            blocks_confirmed=voice_session.confirmed_block_count,
            interview_complete=interview_done,
            awaiting_confirmation=awaiting,
            input_mode=input_mode,
        )

    def _save_message_dna(self, voice_session: VoiceConversationSession) -> None:
        """
        Convert confirmed session data to MessageDNA-compatible JSON
        and save to data/message_dna_output.json.

        This output is immediately usable by the existing engine:
            load_message_dna("data/message_dna_output.json")
        """
        dna_dict = voice_session.to_message_dna_dict()
        Path(MESSAGE_DNA_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(MESSAGE_DNA_PATH, "w", encoding="utf-8") as f:
            json.dump(dna_dict, f, indent=2)
        print(f"MessageDNA saved to: {MESSAGE_DNA_PATH}")

        # Push to Firestore so MessageDNA survives container restarts
        if USE_FIRESTORE:
            try:
                from tools.firestore_tool import save_document
                save_document("message_dna", "default_founder", dna_dict)
                print("MessageDNA saved to Firestore: message_dna/default_founder")
            except Exception as exc:
                print(f"[voice] Firestore MessageDNA save failed (local copy OK): {exc}")

    def _save_campaign_brief(self, voice_session: VoiceConversationSession) -> None:
        """
        Build a starter CampaignBrief from the confirmed interview blocks
        so /campaign is populated right after the interview completes.

        Never overwrites an existing brief — if one exists (from the CLI
        run_campaign.py phase or a previous interview), it is kept.
        Failure here never blocks the interview completion.
        """
        try:
            from models.campaign_brief import CampaignBrief, generate_campaign_id
            from services.storage_service import (
                storage_load_campaign_brief,
                storage_save_campaign_brief,
            )

            existing = storage_load_campaign_brief()
            if existing is not None and existing.is_complete():
                print("[voice] Campaign brief already exists — not overwriting.")
                return

            # Union of all confirmed blocks' extracted fields
            fields: dict = {}
            for block in voice_session.blocks:
                if block.confirmed:
                    fields.update(block.extracted_fields)

            def get(key, default=""):
                v = fields.get(key, default)
                return v if v else default

            def get_list(key):
                v = fields.get(key, [])
                return v if isinstance(v, list) else [v] if v else []

            audience       = get("ideal_audience")
            core_problem   = get("core_problem_solved")
            transformation = get("transformation")
            offer          = get("offer") or transformation
            contrarian     = get("contrarian_belief")
            cta            = get("primary_cta") or get("cta_style")
            voice_words    = get_list("brand_voice_words")
            tone_rules     = get("tone_rules")
            cta_style      = get("cta_style")

            # Narrative arc: audience + problem → belief shift → transformation → CTA
            arc_parts = []
            if audience and core_problem:
                arc_parts.append(f"Speak to {audience} wrestling with {core_problem}.")
            if contrarian:
                arc_parts.append(f"Shift their belief: {contrarian}")
            if transformation:
                arc_parts.append(f"Show the transformation: {transformation}")
            if cta:
                arc_parts.append(f"Invite action: {cta}")

            voice_notes = []
            if voice_words:
                voice_notes.append(f"Voice: {', '.join(voice_words)}")
            if tone_rules:
                voice_notes.append(f"Tone: {tone_rules}")
            if cta_style:
                voice_notes.append(f"CTA style: {cta_style}")

            brief = CampaignBrief(
                campaign_goal             = get("campaign_goal"),
                selected_offer            = offer,
                primary_cta               = cta,
                target_platforms          = get_list("platforms"),
                campaign_theme            = contrarian or core_problem,
                campaign_narrative        = " ".join(arc_parts),
                specific_audience_segment = audience,
                success_definition        = transformation,
                timely_context            = " | ".join(voice_notes),
                campaign_id               = generate_campaign_id(),
            )
            storage_save_campaign_brief(brief)
            print("[voice] Starter campaign brief created from interview data.")

        except Exception as exc:
            print(f"[voice] Campaign brief auto-create failed (interview unaffected): {exc}")
