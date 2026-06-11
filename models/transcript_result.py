"""
models/transcript_result.py

Data model for structured transcript output.

A TranscriptResult holds the structured segments extracted from a
YouTube video. It is the bridge between Phase 3 (transcript fetching)
and Phase 4 (moment selection).

Every segment has:
    - The full text of that section of the video
    - Start and end timestamps (seconds + human-readable)
    - A clip URL that links directly to that moment on YouTube
    - Word count

The transcript_agent populates this and saves it to disk.
The moment_selector_agent (Phase 4) reads it.
"""

import json
from dataclasses import dataclass, field, asdict
from typing import List
from pathlib import Path


@dataclass
class TranscriptSegment:
    """One structured segment of a YouTube transcript (40-120 words)."""
    segment_index: int
    text: str
    start_seconds: float
    end_seconds: float
    start_timestamp: str        # e.g. "04:32" or "1:04:32"
    end_timestamp: str
    word_count: int
    clip_url: str = ""          # https://youtube.com/watch?v=ID&t=Xs


@dataclass
class TranscriptResult:
    """
    The complete structured transcript for one YouTube video.

    Populated by the transcript_agent (Phase 3).
    Read by the moment_selector_agent (Phase 4).
    """
    video_id: str
    video_url: str
    language_code: str = ""
    is_generated: bool = False
    segments: List[TranscriptSegment] = field(default_factory=list)
    total_words: int = 0
    total_segments: int = 0
    duration_seconds: float = 0.0
    is_success: bool = False
    error_type: str = ""
    error_message: str = ""
    fallback_required: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: dict) -> "TranscriptResult":
        segments = [
            TranscriptSegment(**s)
            for s in data.get("segments", [])
        ]
        d = {k: v for k, v in data.items() if k != "segments"}
        result = cls(**d)
        result.segments = segments
        return result

    def summary(self) -> str:
        if self.is_success:
            mins = int(self.duration_seconds // 60)
            secs = int(self.duration_seconds % 60)
            lang = self.language_code + (" (auto-generated)" if self.is_generated else "")
            return (
                f"✅ {self.total_segments} segments · "
                f"{self.total_words} words · "
                f"~{mins}m{secs:02d}s · "
                f"{lang}"
            )
        return f"❌ [{self.error_type}] {self.error_message}"


def save_transcript_result(result: TranscriptResult, filepath: str) -> None:
    """Save a TranscriptResult to JSON."""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(result.to_json())
    print(f"✅ Transcript saved to: {filepath}")


def load_transcript_result(filepath: str) -> TranscriptResult:
    """Load a TranscriptResult from JSON."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = TranscriptResult.from_dict(data)
    print(f"✅ Transcript loaded from: {filepath}")
    return result
