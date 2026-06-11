"""
tools/text_to_speech_tool.py

Google Cloud Text-to-Speech using Chirp 3 HD voice.

Converts agent responses (questions, summaries, confirmations) into
audio bytes that the browser plays back to the founder.

The browser receives audio bytes as a base64-encoded response from
the FastAPI backend, decodes them, and plays them via the Web Audio API.

Chirp 3 HD produces natural, conversational speech — important for
making the interview feel warm rather than robotic.

Environment variables:
    GOOGLE_CLOUD_PROJECT       — your GCP project ID
    GOOGLE_CLOUD_LOCATION      — default: us-central1
    TTS_VOICE_NAME             — default: en-US-Chirp3-HD-Aoede
    TTS_LANGUAGE_CODE          — default: en-US
    GOOGLE_APPLICATION_CREDENTIALS — path to service account key (production)

Available Chirp 3 HD voices (en-US):
    en-US-Chirp3-HD-Aoede      — warm, conversational (recommended)
    en-US-Chirp3-HD-Charon     — clear, professional
    en-US-Chirp3-HD-Fenrir     — deep, authoritative
    en-US-Chirp3-HD-Kore       — bright, energetic
    en-US-Chirp3-HD-Puck       — friendly, approachable

Required pip install:
    pip install google-cloud-texttospeech

Text fallback:
    If TTS fails, the service returns the text only and the browser
    displays it as text instead of playing audio. The conversation
    continues — voice failure never blocks the interview.
"""

import base64
import os
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class TTSResult:
    """Result of one text-to-speech synthesis attempt."""
    success: bool
    audio_bytes: bytes = b""
    audio_base64: str = ""      # base64-encoded audio for JSON API response
    error_message: str = ""
    voice_used: str = ""


# ---------------------------------------------------------------------------
# Core synthesis function
# ---------------------------------------------------------------------------

def synthesise_speech(
    text: str,
    voice_name: str = "",
    language_code: str = "",
    speaking_rate: float = 1.0,
) -> TTSResult:
    """
    Convert text to speech using Chirp 3 HD.

    Args:
        text:          The text to speak. Keep under 5000 characters.
        voice_name:    Override the TTS_VOICE_NAME env var.
        language_code: Override the TTS_LANGUAGE_CODE env var.
        speaking_rate: Speed of speech. 1.0 is normal. Range: 0.25–4.0.

    Returns:
        TTSResult with audio_bytes and audio_base64 set on success.
        The browser uses audio_base64 to play the audio via Web Audio API.
    """
    voice_name    = voice_name    or os.getenv("TTS_VOICE_NAME",    "en-US-Chirp3-HD-Aoede")
    language_code = language_code or os.getenv("TTS_LANGUAGE_CODE", "en-US")
    project       = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    location      = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    if not text.strip():
        return TTSResult(
            success=False,
            error_message="Empty text provided.",
        )

    if not project:
        return TTSResult(
            success=False,
            error_message=(
                "GOOGLE_CLOUD_PROJECT not set. Add it to your .env file.\n"
                "  GOOGLE_CLOUD_PROJECT=your-project-id"
            ),
        )

    try:
        from google.cloud import texttospeech

        client = texttospeech.TextToSpeechClient()

        synthesis_input = texttospeech.SynthesisInput(text=text)

        voice = texttospeech.VoiceSelectionParams(
            language_code=language_code,
            name=voice_name,
        )

        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=speaking_rate,
        )

        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
        )

        audio_bytes  = response.audio_content
        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

        return TTSResult(
            success=True,
            audio_bytes=audio_bytes,
            audio_base64=audio_base64,
            voice_used=voice_name,
        )

    except ImportError:
        return TTSResult(
            success=False,
            error_message=(
                "google-cloud-texttospeech is not installed.\n"
                "  pip install google-cloud-texttospeech"
            ),
        )
    except Exception as e:
        return TTSResult(
            success=False,
            error_message=str(e),
            voice_used=voice_name,
        )


def tts_is_configured() -> bool:
    """Return True only if env var is set AND package is installed."""
    if not os.getenv("GOOGLE_CLOUD_PROJECT"):
        return False
    try:
        from google.cloud import texttospeech  # noqa: F401
        return True
    except ImportError:
        return False


def list_chirp3_voices() -> list[str]:
    """
    Return the list of available Chirp 3 HD voice names.
    Useful for letting the founder pick a voice preference.
    """
    return [
        "en-US-Chirp3-HD-Aoede",    # warm, conversational
        "en-US-Chirp3-HD-Charon",   # clear, professional
        "en-US-Chirp3-HD-Fenrir",   # deep, authoritative
        "en-US-Chirp3-HD-Kore",     # bright, energetic
        "en-US-Chirp3-HD-Puck",     # friendly, approachable
    ]
