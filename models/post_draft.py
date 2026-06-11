"""
models/post_draft.py

PostDraft and PostDraftSet — the Phase 5 output model.

A PostDraft is one structured social media post prepared for assets,
review, and CSV export.

PostDraftSet holds all post drafts for one campaign.

Field names, content types, and asset status values in this file
match the alignment doc exactly. These are the field names the
Phase 5 caption agent writes into posts_output.json.

Content types:
    video_clip   -- grounded in a transcript moment with a timestamp URL
    image_post   -- requires an image to be generated (Phase 6A)
    text_quote   -- text only, no asset required

Asset status values:
    pending_image          -- image_post waiting for Phase 6A
    image_generated        -- Phase 6A completed successfully
    image_generation_failed -- Phase 6A failed, needs manual image
    timestamp_url_ready    -- video_clip with youtube_timestamp_url set
    no_asset_needed        -- text_quote, no asset required

Asset status rules (set by Phase 5):
    video_clip  -> timestamp_url_ready
    image_post  -> pending_image
    text_quote  -> no_asset_needed
"""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Content type and asset status constants
# ---------------------------------------------------------------------------

class ContentType:
    VIDEO_CLIP  = "video_clip"
    IMAGE_POST  = "image_post"
    TEXT_QUOTE  = "text_quote"


class AssetStatus:
    PENDING_IMAGE           = "pending_image"
    IMAGE_GENERATED         = "image_generated"
    IMAGE_GENERATION_FAILED = "image_generation_failed"
    TIMESTAMP_URL_READY     = "timestamp_url_ready"
    NO_ASSET_NEEDED         = "no_asset_needed"


# ---------------------------------------------------------------------------
# PostDraft
# ---------------------------------------------------------------------------

@dataclass
class PostDraft:
    """
    One structured social media post draft.

    Produced by Phase 5 (caption agent / post draft builder).
    Updated by Phase 6A (image generation).
    Updated by Phase 6B (video clip URLs).
    Read by Phase 7 (CSV export).

    CSV column mapping:
        postAtSpecificTime  <- (blank until founder sets schedule)
        content +Hashtags   <- caption + hashtags combined
        link (OGmetaUrl)    <- call_to_action link
        imageUrls           <- image_url
        gifUrl              <- always blank
        videoUrls           <- video_url
    """

    # ── Identity ──────────────────────────────────────────────────
    post_number: int = 0
    # 1-based post number (Post 1, Post 2, etc.)

    content_type: str = ""
    # "video_clip" | "image_post" | "text_quote"

    # ── Content ───────────────────────────────────────────────────
    platform: str = ""
    # e.g. "LinkedIn", "Instagram", "Twitter/X"

    content_pillar: str = ""
    # Exact pillar name from MessageDNA

    caption: str = ""
    # Full post caption in the founder's voice.
    # Does not include hashtags.

    hashtags: str = ""
    # Flat space-separated hashtag string — kept for backward compat.
    # Prefer hashtags_tier1/2/3 when present; this field is only used
    # when those three are all empty (e.g. legacy records).

    hashtags_tier1: List[str] = field(default_factory=list)
    # Niche tags: under 100K posts. 2-3 tags. Written by hashtag_agent.

    hashtags_tier2: List[str] = field(default_factory=list)
    # Mid-range tags: 100K-1M posts. 2-3 tags.

    hashtags_tier3: List[str] = field(default_factory=list)
    # Broad tags: over 1M posts. Max 2 tags.

    call_to_action: str = ""
    # The CTA line for this post, from CampaignBrief.

    # ── Source grounding ──────────────────────────────────────────
    transcript_quote: str = ""
    # Verbatim quote from the source transcript moment.

    youtube_timestamp_url: str = ""
    # YouTube URL with timestamp e.g. https://youtube.com/watch?v=ID&t=125s
    # Present for video_clip and text_quote posts where applicable.

    # ── Asset fields ──────────────────────────────────────────────
    image_prompt: str = ""
    # Imagen prompt for this post.
    # Present for image_post content type.
    # Written by Phase 5. Used by Phase 6A.

    image_url: str = ""
    # Local file path after Phase 6A: data/generated_images/post_002.png
    # Cloud Storage URL after production upload.
    # Blank until Phase 6A runs.

    image_storage_status: str = ""
    # "local_only" after Phase 6A local save.
    # "cloud_storage" after production upload.
    # Blank until Phase 6A runs.

    video_url: str = ""
    # Currently same as youtube_timestamp_url for prototype.
    # Will be a hosted MP4 URL after Phase 6B.

    asset_status: str = ""
    # See AssetStatus constants above.

    # ── Quality and alignment ─────────────────────────────────────
    source_alignment_note: str = ""
    # Why this post was selected and how it connects to MessageDNA.

    quality_notes: str = ""
    # Any warnings, issues, or notes about this post's quality.

    def all_hashtags(self) -> str:
        """
        Return all hashtags as a single space-separated string.

        Prefers tier fields (written by hashtag_agent in Phase 5).
        Falls back to flat hashtags string for backward compat.
        """
        tier_tags = self.hashtags_tier1 + self.hashtags_tier2 + self.hashtags_tier3
        if tier_tags:
            return " ".join(
                t if t.startswith("#") else f"#{t}"
                for t in tier_tags
            )
        return self.hashtags or ""

    def content_and_hashtags(self) -> str:
        """Combined caption + hashtags for the CSV content +Hashtags column."""
        tags = self.all_hashtags()
        if tags:
            return f"{self.caption}\n\n{tags}"
        return self.caption

    def is_pending_image(self) -> bool:
        return self.asset_status == AssetStatus.PENDING_IMAGE

    def summary_line(self) -> str:
        q = self.transcript_quote[:50] + "..." if len(self.transcript_quote) > 50 else self.transcript_quote
        return (
            f"Post {self.post_number} | {self.content_type} | "
            f"{self.platform} | {self.content_pillar[:40]}"
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# PostDraftSet
# ---------------------------------------------------------------------------

@dataclass
class PostDraftSet:
    """
    All post drafts for one campaign.

    This is the top-level container written to posts_output.json.
    """

    campaign_id: str = ""
    total_posts: int = 0
    generation_notes: str = ""
    posts: List[PostDraft] = field(default_factory=list)

    def pending_image_posts(self) -> List[PostDraft]:
        """Return only posts where asset_status == pending_image."""
        return [p for p in self.posts if p.is_pending_image()]

    def to_dict(self) -> dict:
        return {
            "campaign_id": self.campaign_id,
            "total_posts": len(self.posts),
            "generation_notes": self.generation_notes,
            "posts": [p.to_dict() for p in self.posts],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "PostDraftSet":
        """
        Load a PostDraftSet from a plain dict.

        Tolerant of missing fields so it can load posts_output.json
        written by any version of the Phase 5 agent.
        """
        posts = []
        for p in data.get("posts", []):
            # Build with only the keys that exist in the dataclass
            known = {f for f in PostDraft.__dataclass_fields__}
            filtered = {k: v for k, v in p.items() if k in known}
            posts.append(PostDraft(**filtered))

        result = cls(
            campaign_id=data.get("campaign_id", ""),
            total_posts=data.get("total_posts", len(posts)),
            generation_notes=data.get("generation_notes", ""),
        )
        result.posts = posts
        return result

    def summary(self) -> str:
        if not self.posts:
            return "No posts."
        type_counts: dict = {}
        for p in self.posts:
            type_counts[p.content_type] = type_counts.get(p.content_type, 0) + 1
        type_str = ", ".join(f"{k} ({v})" for k, v in type_counts.items())
        pending = len(self.pending_image_posts())
        return (
            f"{len(self.posts)} posts | {type_str} | "
            f"{pending} pending image generation"
        )


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------

def save_post_draft_set(draft_set: PostDraftSet, filepath: str) -> None:
    """Save a PostDraftSet to JSON."""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(draft_set.to_json())
    print(f"✅ Posts saved to: {filepath}")


# Aliases used by run_captions.py (Phase 5 runner)
save_post_drafts = save_post_draft_set


def load_post_draft_set(filepath: str) -> PostDraftSet:
    """Load a PostDraftSet from JSON."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = PostDraftSet.from_dict(data)
    print(f"✅ Posts loaded from: {filepath} ({len(result.posts)} posts)")
    return result
