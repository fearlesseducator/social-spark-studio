"""
tools/speech_to_text_tool.py

Google Cloud Speech-to-Text using Chirp 3.

Transcribes audio bytes sent from the browser into text that the
voice_conversation_agent can process.

Two functions:
    transcribe_audio()     — transcribes raw audio bytes (from browser mic)
    transcribe_file()      — transcribes a local audio file (for testing)

Chirp 3 is Google's latest speech model. It handles:
    - Conversational speech with natural pauses
    - Founder-specific terminology and brand names
    - Multiple English accents

Environment variables:
    GOOGLE_CLOUD_PROJECT      — your GCP project ID
    GOOGLE_CLOUD_LOCATION     — default: us-central1
    SPEECH_TO_TEXT_MODEL      — default: chirp_3
    GOOGLE_APPLICATION_CREDENTIALS — path to service account key (production)

Required pip install:
    pip install google-cloud-speech

Text fallback:
    If STT is unavailable or fails, the caller should fall back to
    accepting typed text input directly. This tool does not implement
    the fallback itself — that is handled in voice_conversation_service.py.
"""

import os
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class TranscriptionResult:
    """Result of one transcription attempt."""
    success: bool
    transcript: str = ""
    confidence: float = 0.0     # 0.0 to 1.0
    error_message: str = ""
    model_used: str = ""


# ---------------------------------------------------------------------------
# Core transcription function
# ---------------------------------------------------------------------------

def transcribe_audio(
    audio_bytes: bytes,
    sample_rate_hz: int = 16000,
    language_code: str = "en-US",
    encoding: str = "WEBM_OPUS",   # browser MediaRecorder default
) -> TranscriptionResult:
    """
    Transcribe audio bytes from the browser microphone.

    Args:
        audio_bytes:    Raw audio bytes captured by browser MediaRecorder.
        sample_rate_hz: Sample rate. Browser default is 16000 or 48000.
        language_code:  BCP-47 language code. Default en-US.
        encoding:       Audio encoding. Browser MediaRecorder produces
                        WEBM_OPUS by default. Use LINEAR16 for WAV files.

    Returns:
        TranscriptionResult with transcript and confidence.
    """
    model       = os.getenv("SPEECH_TO_TEXT_MODEL", "chirp_3")
    project     = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    location    = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    language_code = os.getenv("STT_LANGUAGE_CODE", language_code)

    if not project:
        return TranscriptionResult(
            success=False,
            error_message=(
                "GOOGLE_CLOUD_PROJECT not set. Add it to your .env file.\n"
                "  GOOGLE_CLOUD_PROJECT=your-project-id"
            ),
        )

    try:
        from google.cloud.speech_v2 import SpeechClient
        from google.cloud.speech_v2.types import cloud_speech

        client = SpeechClient()

        # Chirp 3 uses the v2 API with a recognizer config
        recognizer_name = (
            f"projects/{project}/locations/{location}/recognizers/_"
        )

        # Map encoding string to enum
        encoding_map = {
            "WEBM_OPUS":  cloud_speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
            "LINEAR16":   cloud_speech.RecognitionConfig.AudioEncoding.LINEAR16,
            "FLAC":       cloud_speech.RecognitionConfig.AudioEncoding.FLAC,
            "MP3":        cloud_speech.RecognitionConfig.AudioEncoding.MP3,
            "OGG_OPUS":   cloud_speech.RecognitionConfig.AudioEncoding.OGG_OPUS,
        }
        audio_encoding = encoding_map.get(
            encoding.upper(),
            cloud_speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
        )

        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=[language_code],
            model=model,
        )

        request = cloud_speech.RecognizeRequest(
            recognizer=recognizer_name,
            config=config,
            content=audio_bytes,
        )

        response = client.recognize(request=request)

        if not response.results:
            return TranscriptionResult(
                success=False,
                error_message="No speech detected in audio.",
                model_used=model,
            )

        # Take the highest-confidence result
        best_result = response.results[0]
        alternative = best_result.alternatives[0]

        return TranscriptionResult(
            success=True,
            transcript=alternative.transcript.strip(),
            confidence=alternative.confidence,
            model_used=model,
        )

    except ImportError:
        return TranscriptionResult(
            success=False,
            error_message=(
                "google-cloud-speech is not installed.\n"
                "  pip install google-cloud-speech"
            ),
        )
    except Exception as e:
        return TranscriptionResult(
            success=False,
            error_message=str(e),
            model_used=model,
        )


def transcribe_file(
    filepath: str,
    language_code: str = "en-US",
) -> TranscriptionResult:
    """
    Transcribe a local audio file. Used for testing without a browser.

    Supports: WAV, FLAC, MP3, OGG
    """
    path = Path(filepath)
    if not path.exists():
        return TranscriptionResult(
            success=False,
            error_message=f"File not found: {filepath}",
        )

    # Determine encoding from file extension
    ext_to_encoding = {
        ".wav":  "LINEAR16",
        ".flac": "FLAC",
        ".mp3":  "MP3",
        ".ogg":  "OGG_OPUS",
        ".webm": "WEBM_OPUS",
    }
    encoding = ext_to_encoding.get(path.suffix.lower(), "LINEAR16")

    with open(filepath, "rb") as f:
        audio_bytes = f.read()

    return transcribe_audio(audio_bytes, encoding=encoding, language_code=language_code)


def stt_is_configured() -> bool:
    """Return True only if env var is set AND package is installed."""
    if not os.getenv("GOOGLE_CLOUD_PROJECT"):
        return False
    try:
        from google.cloud.speech_v2 import SpeechClient  # noqa: F401
        return True
    except ImportError:
        return False
