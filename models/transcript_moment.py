"""
models/transcript_moment.py

Defines the TranscriptMoment data model.

A TranscriptMoment is one transcript segment that has been identified
as worth turning into a social media post.

Phase 3 (transcript_agent) produces TranscriptSegments — raw structured
chunks of the video with no editorial judgment applied.

Phase 4 (moment_selector_agent) reads those segments, filters them
through MessageDNA and CampaignBrief, and produces TranscriptMoments —
a smaller, curated set with social context added to each one.

Phase 5 (caption_agent) reads TranscriptMoments and writes the actual
post captions. It never goes back to the raw TranscriptSegments.

Key rule:
    The quote in every TranscriptMoment must be verbatim from the
    source TranscriptSegment. No paraphrasing. No summarising.
    The caption agent in Phase 5 builds around the real words —
    not a cleaned-up version of them.
"""

import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional
from pathlib import Path


@dataclass
class TranscriptMoment:
    """
    One curated moment from a transcript, ready for caption writing.

    Each moment carries:
    - The verbatim quote from the video
    - Timestamps and clip URL from the source segment
    - Editorial context added by the moment_selector_agent:
        which content pillar it belongs to, why it is social-worthy,
        which platform it suits best, and how it connects to the
        founder's positioning
    """

    # ── Source tracing ─────────────────────────────────────────────
    moment_index: int = 0
    # Position in the curated list (0-based)

    source_segment_index: int = 0
    # Which TranscriptSegment this came from — for audit trail

    # ── The quote ─────────────────────────────────────────────────
    quote: str = ""
    # VERBATIM text from the source TranscriptSegment.
    # May be the full segment or a meaningful sub-section of it.
    # Never paraphrased. Never cleaned up.

    # ── Timestamps and clip ───────────────────────────────────────
    start_seconds: float = 0.0
    end_seconds: float = 0.0
    start_timestamp: str = ""   # e.g. "04:32"
    end_timestamp: str = ""
    clip_url: str = ""          # https://youtube.com/watch?v=ID&t=Xs

    # ── Editorial context (added by moment_selector_agent) ────────
    content_pillar: str = ""
    # Which of the founder's content pillars this moment belongs to.
    # Must exactly match one of the pillars in MessageDNA.content_pillars.

    why_social_worthy: str = ""
    # One sentence: why a non-follower would stop scrolling for this.
    # e.g. "Counterintuitive claim with a specific proof point attached."

    platform_recommendation: str = ""
    # Which platform this moment is best suited for.
    # e.g. "LinkedIn", "Instagram", "Twitter/X"

    positioning_angle: str = ""
    # How this moment connects to the founder's MessageDNA positioning.
    # e.g. "Reinforces contrarian belief: churn is a clarity problem."

    hook_idea: str = ""
    # A starting direction for the caption hook — NOT the final caption.
    # The caption_agent writes the actual hook. This is just a pointer.
    # e.g. "Open with the stat: 38% churn dropping to 9% in 60 days."

    word_count: int = 0
    # Word count of the quote field

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def summary_line(self) -> str:
        """One-line summary for display in the terminal."""
        ts = f"[{self.start_timestamp}]" if self.start_timestamp else "[manual]"
        q_preview = self.quote[:60] + "..." if len(self.quote) > 60 else self.quote
        return (
            f"Moment {self.moment_index + 1} {ts} "
            f"| {self.content_pillar} "
            f"| {self.platform_recommendation} "
            f"| \"{q_preview}\""
        )


@dataclass
class MomentSelectionResult:
    """
    The complete output of the moment_selector_agent for one campaign.

    Contains the curated list of moments plus metadata about the
    selection process — useful for debugging and for the caption agent.
    """

    video_id: str = ""
    video_url: str = ""
    total_segments_reviewed: int = 0
    moments: List[TranscriptMoment] = field(default_factory=list)
    selection_notes: str = ""
    # Free-text notes from the agent about what it found and why
    # e.g. "Strong contrarian belief segment at 04:32. Weak on CTA content."

    @property
    def total_moments(self) -> int:
        return len(self.moments)

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "video_url": self.video_url,
            "total_segments_reviewed": self.total_segments_reviewed,
            "total_moments": self.total_moments,
            "selection_notes": self.selection_notes,
            "moments": [m.to_dict() for m in self.moments],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "MomentSelectionResult":
        moments = [TranscriptMoment(**m) for m in data.get("moments", [])]
        result = cls(
            video_id=data.get("video_id", ""),
            video_url=data.get("video_url", ""),
            total_segments_reviewed=data.get("total_segments_reviewed", 0),
            selection_notes=data.get("selection_notes", ""),
        )
        result.moments = moments
        return result

    def summary(self) -> str:
        if not self.moments:
            return "No moments selected."
        pillar_counts: dict = {}
        for m in self.moments:
            pillar_counts[m.content_pillar] = pillar_counts.get(m.content_pillar, 0) + 1
        pillar_summary = ", ".join(f"{p} ({n})" for p, n in pillar_counts.items())
        return (
            f"✅ {self.total_moments} moments selected "
            f"from {self.total_segments_reviewed} segments | "
            f"Pillars: {pillar_summary}"
        )


def save_moments(result: MomentSelectionResult, filepath: str) -> None:
    """Save a MomentSelectionResult to JSON."""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(result.to_json())
    print(f"✅ Moments saved to: {filepath}")


def load_moments(filepath: str) -> MomentSelectionResult:
    """Load a MomentSelectionResult from JSON."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = MomentSelectionResult.from_dict(data)
    print(f"✅ Moments loaded from: {filepath}")
    return result


# ── Firestore persistence ───────────────────────────────────────────────

def save_moments_firestore(result: MomentSelectionResult, founder_id: str = "default_founder") -> None:
    """Save MomentSelectionResult to Firestore (collection: moments)."""
    from tools.firestore_tool import save_document
    save_document("moments", founder_id, result.to_dict())
    print(f"✅ Moments saved to Firestore: moments/{founder_id}")


def load_moments_firestore(founder_id: str = "default_founder") -> MomentSelectionResult | None:
    """Load MomentSelectionResult from Firestore. Returns None if not found."""
    from tools.firestore_tool import load_document
    data = load_document("moments", founder_id)
    if data is None:
        return None
    print(f"✅ Moments loaded from Firestore: moments/{founder_id}")
    return MomentSelectionResult.from_dict(data)
