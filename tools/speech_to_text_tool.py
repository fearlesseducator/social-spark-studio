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
    # chirp_3 lives in multi-regions ("us", "eu") — NOT "global" or
    # "us-central1". Deliberately independent of GOOGLE_CLOUD_LOCATION,
    # which Vertex/Imagen need set to "global".
    location    = os.getenv("STT_LOCATION", "us")
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
        from google.api_core.client_options import ClientOptions

        # Regional recognizers (chirp_3 lives in regions like us-central1,
        # not "global") must use the matching regional API endpoint.
        if location and location != "global":
            client = SpeechClient(client_options=ClientOptions(
                api_endpoint=f"{location}-speech.googleapis.com"
            ))
        else:
            client = SpeechClient()

        # Chirp 3 uses the v2 API with a recognizer config
        recognizer_name = (
            f"projects/{project}/locations/{location}/recognizers/_"
        )

        # The encoding parameter is intentionally ignored: speech_v2 has no
        # RecognitionConfig.AudioEncoding (that's the v1 API). AutoDetect
        # handles browser WEBM_OPUS, WAV, MP3, FLAC, and OGG automatically.
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


def transcribe_long_audio(
    audio_bytes: bytes = b"",
    content_type: str = "audio/mpeg",
    language_code: str = "en-US",
    timeout_seconds: int = 900,
    gcs_uri: str = "",
) -> "LongTranscriptionResult":
    """
    Transcribe long-form audio/video (5-20 minute episodes) using
    Speech-to-Text v2 batch_recognize.

    Two input modes:
        audio_bytes — uploaded to a temp GCS object (deleted after)
        gcs_uri     — media already in GCS (e.g. uploaded founder MP4);
                      batch_recognize reads it in place, nothing deleted

    AutoDetectDecodingConfig handles MP3/WAV/FLAC/OGG/WEBM and the AAC
    audio track inside MP4 uploads.

    Returns LongTranscriptionResult with per-chunk text + end offsets so
    callers can build timestamped segments.
    """
    import uuid as _uuid

    model    = os.getenv("SPEECH_TO_TEXT_MODEL", "chirp_3")
    project  = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    location = os.getenv("STT_LOCATION", "us")
    language_code = os.getenv("STT_LANGUAGE_CODE", language_code)
    bucket_name   = os.getenv("GCS_BUCKET_NAME", "")

    if not project:
        return LongTranscriptionResult(success=False, error_message="GOOGLE_CLOUD_PROJECT not set.")
    if not gcs_uri and not bucket_name:
        return LongTranscriptionResult(success=False, error_message="GCS_BUCKET_NAME not set — needed for long audio transcription.")
    if not gcs_uri and not audio_bytes:
        return LongTranscriptionResult(success=False, error_message="No audio provided (audio_bytes or gcs_uri required).")

    blob = None
    try:
        from google.cloud import storage as gcs_storage
        from google.cloud.speech_v2 import SpeechClient
        from google.cloud.speech_v2.types import cloud_speech
        from google.api_core.client_options import ClientOptions

        # 1. Resolve the GCS URI (upload temp object only for bytes mode)
        if not gcs_uri:
            storage_client = gcs_storage.Client(project=project)
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(f"rss_audio/{_uuid.uuid4().hex}")
            blob.upload_from_string(audio_bytes, content_type=content_type)
            gcs_uri = f"gs://{bucket_name}/{blob.name}"

        # 2. Batch recognize
        if location and location != "global":
            client = SpeechClient(client_options=ClientOptions(
                api_endpoint=f"{location}-speech.googleapis.com"
            ))
        else:
            client = SpeechClient()

        recognizer = f"projects/{project}/locations/{location}/recognizers/_"
        # NOTE: MP4 containers are NOT supported by AutoDetect (and
        # explicit MP4_AAC fails with chirp_3). Callers must extract the
        # audio track first — see extract_audio_from_video().
        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=[language_code],
            model=model,
        )
        request = cloud_speech.BatchRecognizeRequest(
            recognizer=recognizer,
            config=config,
            files=[cloud_speech.BatchRecognizeFileMetadata(uri=gcs_uri)],
            recognition_output_config=cloud_speech.RecognitionOutputConfig(
                inline_response_config=cloud_speech.InlineOutputConfig(),
            ),
        )
        operation = client.batch_recognize(request=request)
        response  = operation.result(timeout=timeout_seconds)

        file_result = response.results[gcs_uri]
        if file_result.error and file_result.error.message:
            return LongTranscriptionResult(
                success=False,
                error_message=f"Transcription failed: {file_result.error.message}",
                model_used=model,
            )

        # 3. Collect chunks: each result has a transcript + end offset
        chunks = []
        for res in file_result.transcript.results:
            if not res.alternatives:
                continue
            text = res.alternatives[0].transcript.strip()
            if not text:
                continue
            end_offset = res.result_end_offset.total_seconds() if res.result_end_offset else 0.0
            chunks.append({"text": text, "end_seconds": end_offset})

        if not chunks:
            return LongTranscriptionResult(
                success=False,
                error_message="No speech detected in the episode audio.",
                model_used=model,
            )

        return LongTranscriptionResult(
            success=True,
            chunks=chunks,
            duration_seconds=chunks[-1]["end_seconds"],
            model_used=model,
            language_code=language_code,
        )

    except Exception as e:
        return LongTranscriptionResult(success=False, error_message=str(e), model_used=model)
    finally:
        # 4. Always clean up the temp GCS object
        if blob is not None:
            try:
                blob.delete()
            except Exception:
                pass


@dataclass
class LongTranscriptionResult:
    """Result of one long-form (batch) transcription."""
    success: bool
    chunks: list = None              # [{"text": str, "end_seconds": float}]
    duration_seconds: float = 0.0
    language_code: str = ""
    error_message: str = ""
    model_used: str = ""

    def __post_init__(self):
        if self.chunks is None:
            self.chunks = []

    @property
    def full_text(self) -> str:
        return " ".join(c["text"] for c in self.chunks)


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


def extract_audio_from_video(video_bytes: bytes) -> bytes:
    """
    Extract the audio track from an MP4/MOV video as MP3 using ffmpeg.

    Speech-to-Text v2 batch cannot decode MP4 containers (AutoDetect
    rejects them; explicit MP4_AAC fails with chirp_3), so founder video
    uploads are converted to MP3 before transcription.

    Requires the ffmpeg binary (installed in the Dockerfile for Cloud
    Run). Raises RuntimeError with a clear message when unavailable
    or when extraction fails (e.g. video has no audio track).
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path as _Path

    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg is not installed on this server — cannot extract audio "
            "from video. Upload an MP3/M4A audio file instead."
        )

    with tempfile.TemporaryDirectory() as tmp:
        src = _Path(tmp) / "input.mp4"
        dst = _Path(tmp) / "audio.mp3"
        src.write_bytes(video_bytes)
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-vn",
             "-acodec", "libmp3lame", "-b:a", "96k", "-ac", "1", str(dst)],
            capture_output=True, timeout=600,
        )
        if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
            stderr_tail = proc.stderr.decode("utf-8", errors="replace")[-300:]
            raise RuntimeError(
                "Could not extract audio from the video. Make sure the MP4 "
                f"has an audio track. ffmpeg said: {stderr_tail}"
            )
        return dst.read_bytes()


def probe_media_duration(media_bytes: bytes, suffix: str = ".mp3") -> float:
    """
    Return the duration in seconds of a media file using ffprobe.
    Returns 0.0 when ffprobe is unavailable or the probe fails.
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path as _Path

    if shutil.which("ffprobe") is None:
        return 0.0
    try:
        with tempfile.TemporaryDirectory() as tmp:
            f = _Path(tmp) / f"media{suffix}"
            f.write_bytes(media_bytes)
            proc = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(f)],
                capture_output=True, timeout=120,
            )
            return float(proc.stdout.decode().strip())
    except Exception:
        return 0.0


def stt_is_configured() -> bool:
    """Return True only if env var is set AND package is installed."""
    if not os.getenv("GOOGLE_CLOUD_PROJECT"):
        return False
    try:
        from google.cloud.speech_v2 import SpeechClient  # noqa: F401
        return True
    except ImportError:
        return False
