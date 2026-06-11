"""
models/campaign_brief.py

The CampaignBrief data model.

CampaignBrief belongs to ONE campaign.
It changes every time the founder starts a new campaign.

It captures what this specific campaign is trying to achieve —
the goal, the offer, the CTA, the platforms, the video, and timing.

It never overwrites MessageDNA. It uses MessageDNA.
The two objects work together: MessageDNA provides the voice,
CampaignBrief provides the mission for this campaign.

Usage:
    brief = CampaignBrief()
    brief.campaign_goal = "Drive free trial signups for my SaaS product"
    save_campaign_brief(brief, "data/campaign_brief.json")
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional
import json
from pathlib import Path


@dataclass
class CampaignBrief:
    """
    Campaign-specific context for one content campaign.

    This object is created fresh for every campaign.
    It is combined with MessageDNA during content generation —
    MessageDNA provides voice, CampaignBrief provides direction.
    """

    # What this campaign is trying to do
    campaign_goal: str = ""
    # Example: "Drive free trial signups", "Grow email list", "Launch new course"

    # Which product or service this campaign promotes
    selected_offer: str = ""
    # Example: "Onboarding Clarity Sprint — $497 consulting engagement"

    # The one action the founder wants people to take
    primary_cta: str = ""
    # Example: "Book a free 30-min onboarding audit at clarityos.com/audit"

    # Which platforms to publish on
    target_platforms: List[str] = field(default_factory=list)
    # Example: ["LinkedIn", "Instagram"]

    # The main angle or theme connecting this campaign's posts
    campaign_theme: str = ""
    # Example: "Why your churn problem is actually an onboarding clarity problem"

    # Specific audience segment this campaign targets (optional)
    specific_audience_segment: str = ""
    # Example: "Warm leads who already follow me but haven't bought yet"

    # Any time-sensitive context: launches, promotions, deadlines
    timely_context: str = ""
    # Example: "Launching a new cohort on July 1 — 20 spots only"

    # Promotion or discount details if applicable
    promotion_details: str = ""
    # Example: "Early bird pricing of $297 until June 30"

    # How the founder will know this campaign worked
    success_definition: str = ""
    # Example: "10 booked discovery calls from this content series"

    # Default: up to 15 posts per selected video.
    # Scheduling is handled in the CSV export phase — not during CampaignBrief.
    # Only populated if the founder explicitly requests a specific cadence.
    posts_per_week: Optional[int] = None
    campaign_duration_weeks: Optional[int] = None

    # YouTube video URL (filled after CampaignBrief interview, before transcript phase)
    youtube_url: str = ""

    # Campaign ID for file naming and tracking
    campaign_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def is_complete(self) -> bool:
        """Minimum required fields for a usable CampaignBrief."""
        return all([
            self.campaign_goal,
            self.selected_offer,
            self.primary_cta,
            len(self.target_platforms) > 0,
            self.campaign_theme,
        ])

    def summary(self) -> str:
        lines = []
        if self.campaign_goal:
            lines.append(f"Goal: {self.campaign_goal}")
        if self.selected_offer:
            lines.append(f"Offer: {self.selected_offer}")
        if self.primary_cta:
            lines.append(f"CTA: {self.primary_cta}")
        if self.target_platforms:
            lines.append(f"Platforms: {', '.join(self.target_platforms)}")
        if self.campaign_theme:
            lines.append(f"Theme: {self.campaign_theme}")
        if self.timely_context:
            lines.append(f"Timely context: {self.timely_context}")
        lines.append(f"Cadence: {self.posts_per_week} posts/week for {self.campaign_duration_weeks} weeks")
        return "\n".join(lines) if lines else "CampaignBrief is empty."


def generate_campaign_id() -> str:
    """Generate a unique campaign ID based on the current timestamp."""
    from datetime import datetime
    return f"campaign_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def save_campaign_brief(brief: CampaignBrief, filepath: str) -> None:
    """Save a CampaignBrief to JSON."""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(brief.to_json())
    print(f"✅ CampaignBrief saved to: {filepath}")


def load_campaign_brief(filepath: str) -> CampaignBrief:
    """Load a CampaignBrief from JSON."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    brief = CampaignBrief(**data)
    print(f"✅ CampaignBrief loaded from: {filepath}")
    return brief
