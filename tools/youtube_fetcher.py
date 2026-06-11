"""
utils/youtube_fetcher.py

Low-level YouTube transcript fetching utility.

This module is intentionally NOT an ADK agent — it is a pure Python
utility that the transcript_agent calls as a tool.

Responsibilities:
    - Parse YouTube URLs into video IDs
    - Fetch raw transcript snippets via youtube-transcript-api
    - Group snippets into structured segments (40–120 words each)
    - Convert seconds to HH:MM:SS / MM:SS timestamps
    - Build YouTube timestamp URLs for clip linking
    - Return structured results or clear failure objects

The transcript_agent (agents/transcript_agent.py) uses this module
to do the heavy lifting, then passes the result to the ADK session.

Architecture note:
    Data classes (TranscriptSegment, TranscriptResult) live in
    models/transcript_result.py — single source of truth.
    This module imports and uses them.
"""

import re
import sys
from pathlib import Path
from typing import List, Optional

# Import canonical data classes from models (single source of truth)
sys.path.insert(0, str(Path(__file__).parent.parent))
from models.transcript_result import TranscriptSegment, TranscriptResult


# ── URL parsing ───────────────────────────────────────────────────────

def extract_video_id(url: str) -> Optional[str]:
    """
    Parse a YouTube URL and return the 11-character video ID.

    Supports all common YouTube URL formats:
        https://www.youtube.com/watch?v=XXXXXXXXXXX
        https://youtu.be/XXXXXXXXXXX
        https://youtube.com/shorts/XXXXXXXXXXX
        https://www.youtube.com/embed/XXXXXXXXXXX
        https://www.youtube.com/watch?v=XXXXXXXXXXX&t=120s

    Returns None if the URL is not a recognisable YouTube URL.
    """
    if not url or not isinstance(url, str):
        return None

    url = url.strip()

    # Match any of the known YouTube URL patterns
    pattern = r'(?:v=|youtu\.be/|/embed/|/shorts/)([a-zA-Z0-9_-]{11})'
    match = re.search(pattern, url)
    if match:
        return match.group(1)

    return None


def build_clip_url(video_id: str, start_seconds: float) -> str:
    """
    Build a YouTube URL that starts playback at a specific timestamp.

    Example:
        build_clip_url("abc12345678", 125.5)
        → "https://www.youtube.com/watch?v=abc12345678&t=125s"
    """
    t = int(start_seconds)
    return f"https://www.youtube.com/watch?v={video_id}&t={t}s"


# ── Timestamp formatting ──────────────────────────────────────────────

def seconds_to_timestamp(seconds: float) -> str:
    """
    Convert a float number of seconds to a human-readable timestamp.

    Under 1 hour:  "MM:SS"   e.g. "04:32"
    1 hour or more: "HH:MM:SS"  e.g. "1:04:32"
    """
    s = int(seconds)
    h, remainder = divmod(s, 3600)
    m, sec = divmod(remainder, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


# ── Segmentation ──────────────────────────────────────────────────────

def group_snippets_into_segments(
    snippets: list,
    video_id: str,
    min_words: int = 40,
    max_words: int = 120,
) -> List[TranscriptSegment]:
    """
    Group raw YouTube transcript snippets into structured segments.

    YouTube returns very short snippets (often 1–5 words each).
    This function groups them into meaningful chunks of 40–120 words,
    which is the right size for the moment selector agent to work with.

    Args:
        snippets: List of snippet objects from youtube-transcript-api.
                  Each has .text, .start, .duration attributes.
        video_id: The YouTube video ID (for building clip URLs).
        min_words: Minimum words before flushing a segment (default 40).
        max_words: Maximum words per segment (default 120).

    Returns:
        List of TranscriptSegment objects with timestamps and clip URLs.
    """
    segments: List[TranscriptSegment] = []
    current_texts: List[str] = []
    current_start: Optional[float] = None
    current_word_count: int = 0
    segment_index: int = 0

    for snip in snippets:
        # Handle both object attributes and dict access
        if hasattr(snip, 'text'):
            text = snip.text.strip()
            start = snip.start
            duration = snip.duration
        else:
            text = snip.get('text', '').strip()
            start = snip.get('start', 0.0)
            duration = snip.get('duration', 0.0)

        if not text:
            continue

        words = len(text.split())

        if current_start is None:
            current_start = start

        current_texts.append(text)
        current_word_count += words
        current_end = start + duration

        # Flush when we reach the minimum target size
        if current_word_count >= min_words:
            full_text = " ".join(current_texts)
            segments.append(TranscriptSegment(
                segment_index=segment_index,
                text=full_text,
                start_seconds=current_start,
                end_seconds=current_end,
                start_timestamp=seconds_to_timestamp(current_start),
                end_timestamp=seconds_to_timestamp(current_end),
                word_count=current_word_count,
                clip_url=build_clip_url(video_id, current_start),
            ))
            segment_index += 1
            current_texts = []
            current_start = None
            current_word_count = 0

    # Flush any remaining text as the final segment
    if current_texts:
        last_snip = snippets[-1]
        if hasattr(last_snip, 'start'):
            last_end = last_snip.start + last_snip.duration
        else:
            last_end = last_snip.get('start', 0) + last_snip.get('duration', 0)

        full_text = " ".join(current_texts)
        segments.append(TranscriptSegment(
            segment_index=segment_index,
            text=full_text,
            start_seconds=current_start or 0.0,
            end_seconds=last_end,
            start_timestamp=seconds_to_timestamp(current_start or 0.0),
            end_timestamp=seconds_to_timestamp(last_end),
            word_count=current_word_count,
            clip_url=build_clip_url(video_id, current_start or 0.0),
        ))

    return segments


# ── Main fetch function ───────────────────────────────────────────────

def fetch_transcript(url: str, min_total_words: int = 300) -> TranscriptResult:
    """
    Fetch and structure a YouTube transcript from a video URL.

    This is the main function called by the transcript_agent.

    Flow:
        1. Parse the URL to extract the video ID
        2. Fetch transcript via youtube-transcript-api
        3. Try English first, then any available language
        4. Group raw snippets into structured segments
        5. Validate minimum word count
        6. Return TranscriptResult (success or failure)

    Args:
        url: Any valid YouTube URL.
        min_total_words: Minimum words for a usable transcript (default 300).
            Videos shorter than this likely won't generate enough posts.

    Returns:
        TranscriptResult with either populated segments or a failure reason.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import (
            TranscriptsDisabled,
            NoTranscriptFound,
            VideoUnavailable,
        )
    except ImportError:
        return TranscriptResult(
            video_id="",
            video_url=url,
            is_success=False,
            error_type="fetch_error",
            error_message=(
                "youtube-transcript-api is not installed. "
                "Run: pip install youtube-transcript-api"
            ),
            fallback_required=True,
        )

    # Step 1: Parse URL
    video_id = extract_video_id(url)
    if not video_id:
        return TranscriptResult(
            video_id="",
            video_url=url,
            is_success=False,
            error_type="fetch_error",
            error_message=(
                f"Could not parse a video ID from URL: {url}\n"
                "Accepted formats:\n"
                "  https://www.youtube.com/watch?v=VIDEO_ID\n"
                "  https://youtu.be/VIDEO_ID\n"
                "  https://youtube.com/shorts/VIDEO_ID"
            ),
            fallback_required=True,
        )

    # Step 2: Fetch transcript
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)

        # Try manual English captions first, then auto-generated, then any language
        transcript = None
        language_code = ""
        is_generated = False

        try:
            transcript = transcript_list.find_manually_created_transcript(['en', 'en-US', 'en-GB'])
            language_code = transcript.language_code
            is_generated = False
        except NoTranscriptFound:
            pass

        if transcript is None:
            try:
                transcript = transcript_list.find_generated_transcript(['en', 'en-US'])
                language_code = transcript.language_code
                is_generated = True
            except NoTranscriptFound:
                pass

        if transcript is None:
            # Last resort: take whatever language is available
            try:
                available = list(transcript_list)
                if available:
                    transcript = available[0]
                    language_code = transcript.language_code
                    is_generated = getattr(transcript, 'is_generated', False)
            except Exception:
                pass

        if transcript is None:
            return TranscriptResult(
                video_id=video_id,
                video_url=url,
                is_success=False,
                error_type="no_captions",
                error_message=(
                    "This video does not have any available captions.\n"
                    "You can paste the transcript text manually to continue."
                ),
                fallback_required=True,
            )

        # Step 3: Fetch the actual snippet data
        fetched = transcript.fetch()
        snippets = list(fetched)

        if not snippets:
            return TranscriptResult(
                video_id=video_id,
                video_url=url,
                language_code=language_code,
                is_generated=is_generated,
                is_success=False,
                error_type="no_captions",
                error_message="Captions exist but returned empty content.",
                fallback_required=True,
            )

        # Step 4: Group into segments
        segments = group_snippets_into_segments(snippets, video_id)

        # Step 5: Validate total word count
        total_words = sum(s.word_count for s in segments)
        duration = (
            segments[-1].end_seconds if segments else 0.0
        )

        if total_words < min_total_words:
            return TranscriptResult(
                video_id=video_id,
                video_url=url,
                language_code=language_code,
                is_generated=is_generated,
                segments=segments,
                total_words=total_words,
                total_segments=len(segments),
                duration_seconds=duration,
                is_success=False,
                error_type="too_short",
                error_message=(
                    f"Transcript is too short ({total_words} words). "
                    f"Minimum required: {min_total_words} words. "
                    "This video is probably too short to generate a full campaign. "
                    "Try a video that is at least 5 minutes long."
                ),
                fallback_required=True,
            )

        # Step 6: Success
        return TranscriptResult(
            video_id=video_id,
            video_url=url,
            language_code=language_code,
            is_generated=is_generated,
            segments=segments,
            total_words=total_words,
            total_segments=len(segments),
            duration_seconds=duration,
            is_success=True,
        )

    except VideoUnavailable:
        return TranscriptResult(
            video_id=video_id,
            video_url=url,
            is_success=False,
            error_type="unavailable",
            error_message=(
                "This video is unavailable. It may be private, deleted, or "
                "region-restricted. Please choose a different video."
            ),
            fallback_required=True,
        )

    except TranscriptsDisabled:
        return TranscriptResult(
            video_id=video_id,
            video_url=url,
            is_success=False,
            error_type="no_captions",
            error_message=(
                "Captions are disabled for this video. "
                "You can paste the transcript text manually to continue."
            ),
            fallback_required=True,
        )

    except Exception as e:
        return TranscriptResult(
            video_id=video_id,
            video_url=url,
            is_success=False,
            error_type="fetch_error",
            error_message=str(e),
            fallback_required=True,
        )


# ── Manual transcript parser ──────────────────────────────────────────

def parse_manual_transcript(
    text: str,
    video_url: str,
    min_total_words: int = 300,
) -> TranscriptResult:
    """
    Parse a manually pasted transcript into TranscriptResult format.

    Used when automatic caption fetch fails (fallback_required = True).
    The founder pastes their transcript text and we structure it the
    same way as an automatic fetch — so the rest of the pipeline works
    identically regardless of how the transcript was obtained.

    Args:
        text: Raw transcript text pasted by the founder.
        video_url: The original YouTube URL (for clip URL generation).
        min_total_words: Minimum word count to be usable.

    Returns:
        TranscriptResult with segments (no timestamps — set to 0).
    """
    if not text or not text.strip():
        return TranscriptResult(
            video_id="",
            video_url=video_url,
            is_success=False,
            error_type="fetch_error",
            error_message="No transcript text was provided.",
            fallback_required=True,
        )

    video_id = extract_video_id(video_url) or "manual"
    total_words = len(text.split())

    if total_words < min_total_words:
        return TranscriptResult(
            video_id=video_id,
            video_url=video_url,
            is_success=False,
            error_type="too_short",
            error_message=(
                f"Pasted transcript is too short ({total_words} words). "
                f"Minimum required: {min_total_words} words."
            ),
            fallback_required=True,
        )

    # Split into fake snippets (one sentence each) so segmentation works
    sentences = [s.strip() for s in text.replace('\n', ' ').split('.') if s.strip()]
    fake_snippets = [
        {'text': s + '.', 'start': 0.0, 'duration': 0.0}
        for s in sentences
    ]

    segments = group_snippets_into_segments(fake_snippets, video_id)

    return TranscriptResult(
        video_id=video_id,
        video_url=video_url,
        language_code="manual",
        is_generated=False,
        segments=segments,
        total_words=total_words,
        total_segments=len(segments),
        duration_seconds=0.0,
        is_success=True,
    )
