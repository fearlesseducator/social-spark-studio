"""
models/message_dna.py

Defines the MessageDNA data model.

MessageDNA is the founder's long-term voice and positioning profile.
It belongs to the FOUNDER, not to any single campaign.
It should be created ONCE during onboarding and reused across all campaigns.

It is NEVER overwritten by campaign-specific details unless the
founder explicitly chooses to update it.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional
import json


@dataclass
class FounderIdentity:
    """Who the founder is and what they want to be known for."""
    founder_name: str = ""
    brand_name: str = ""
    known_for: str = ""           # What they want to be known for in their industry


@dataclass
class AudienceProfile:
    """Long-term picture of the founder's ideal audience."""
    ideal_audience: str = ""       # Who they serve (reusable across campaigns)
    audience_worldview: str = ""   # What the audience already believes
    core_problem_solved: str = ""  # The deep problem the founder solves
    audience_misconceptions: str = ""  # What the audience misunderstands


@dataclass
class FounderPositioning:
    """Beliefs, origin story, and market stance."""
    signature_beliefs: List[str] = field(default_factory=list)  # Core things the founder believes
    contrarian_belief: str = ""    # The one thing they believe that most people disagree with
    origin_story: str = ""         # Why the founder does this work
    future_vision: str = ""        # Where the founder wants to take their audience/industry


@dataclass
class VoiceProfile:
    """How the founder speaks, writes, and teaches."""
    brand_voice_words: List[str] = field(default_factory=list)  # 3-5 words that describe their voice
    phrases_to_use: List[str] = field(default_factory=list)      # Phrases that sound like them
    phrases_to_avoid: List[str] = field(default_factory=list)    # Phrases that sound wrong
    tone_rules: str = ""           # How they want to come across (e.g. direct but warm)
    teaching_style: str = ""       # How they explain complex ideas
    cta_style: str = ""            # How they invite action (soft nudge vs. direct ask)


@dataclass
class ContentDirection:
    """Content pillars and visual identity guidance."""
    content_pillars: List[str] = field(default_factory=list)     # 3-4 recurring content themes
    visual_metaphors: List[str] = field(default_factory=list)    # Images/metaphors that fit the brand
    visual_avoid_list: List[str] = field(default_factory=list)   # Images/styles to avoid
    credibility_markers: List[str] = field(default_factory=list) # Proof points, credentials, results


@dataclass
class MessageDNA:
    """
    The founder's complete long-term voice and positioning profile.

    This is the master object that flows into every content generation task.
    It ensures every post sounds like the founder — not like generic AI content.

    Usage:
        dna = MessageDNA()
        dna.founder_identity.founder_name = "Jane Smith"
        save_message_dna(dna, "founders/jane_smith_dna.json")
    """
    founder_identity: FounderIdentity = field(default_factory=FounderIdentity)
    audience_profile: AudienceProfile = field(default_factory=AudienceProfile)
    founder_positioning: FounderPositioning = field(default_factory=FounderPositioning)
    voice_profile: VoiceProfile = field(default_factory=VoiceProfile)
    content_direction: ContentDirection = field(default_factory=ContentDirection)

    def to_dict(self) -> dict:
        """Convert MessageDNA to a plain dictionary."""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Convert MessageDNA to a formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def is_complete(self) -> bool:
        """
        Returns True if the minimum required fields are filled.
        A complete MessageDNA has at least: founder name, ideal audience,
        one content pillar, and voice words.
        """
        has_identity = bool(self.founder_identity.founder_name)
        has_audience = bool(self.audience_profile.ideal_audience)
        has_pillars = len(self.content_direction.content_pillars) > 0
        has_voice = len(self.voice_profile.brand_voice_words) > 0
        return has_identity and has_audience and has_pillars and has_voice

    def summary(self) -> str:
        """Return a short human-readable summary of what has been captured."""
        lines = []
        fi = self.founder_identity
        if fi.founder_name:
            lines.append(f"Founder: {fi.founder_name}" + (f" ({fi.brand_name})" if fi.brand_name else ""))
        if fi.known_for:
            lines.append(f"Known for: {fi.known_for}")
        ap = self.audience_profile
        if ap.ideal_audience:
            lines.append(f"Audience: {ap.ideal_audience}")
        fp = self.founder_positioning
        if fp.contrarian_belief:
            lines.append(f"Contrarian belief: {fp.contrarian_belief}")
        vp = self.voice_profile
        if vp.brand_voice_words:
            lines.append(f"Voice: {', '.join(vp.brand_voice_words)}")
        cd = self.content_direction
        if cd.content_pillars:
            lines.append(f"Content pillars: {', '.join(cd.content_pillars)}")
        return "\n".join(lines) if lines else "MessageDNA is empty."


def save_message_dna(dna: MessageDNA, filepath: str) -> None:
    """Save a MessageDNA object to a JSON file."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(dna.to_json())
    print(f"✅ MessageDNA saved to: {filepath}")


def message_dna_from_dict(data: dict) -> MessageDNA:
    """Build a MessageDNA object from a plain dict (file or Firestore)."""
    dna = MessageDNA()
    dna.founder_identity    = FounderIdentity(**data.get("founder_identity", {}))
    dna.audience_profile    = AudienceProfile(**data.get("audience_profile", {}))
    dna.founder_positioning = FounderPositioning(**data.get("founder_positioning", {}))
    dna.voice_profile       = VoiceProfile(**data.get("voice_profile", {}))
    dna.content_direction   = ContentDirection(**data.get("content_direction", {}))
    return dna


def load_message_dna(filepath: str) -> MessageDNA:
    """Load a MessageDNA object from a JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    dna = message_dna_from_dict(data)
    print(f"✅ MessageDNA loaded from: {filepath}")
    return dna


# ── Firestore persistence ───────────────────────────────────────────────

def save_message_dna_firestore(dna: MessageDNA, founder_id: str = "default_founder") -> None:
    """Save MessageDNA to Firestore (collection: message_dna)."""
    from tools.firestore_tool import save_document
    save_document("message_dna", founder_id, dna.to_dict())
    print(f"✅ MessageDNA saved to Firestore: message_dna/{founder_id}")


def load_message_dna_firestore(founder_id: str = "default_founder") -> MessageDNA | None:
    """Load MessageDNA from Firestore. Returns None if not found."""
    from tools.firestore_tool import load_document
    data = load_document("message_dna", founder_id)
    if data is None:
        return None
    print(f"✅ MessageDNA loaded from Firestore: message_dna/{founder_id}")
    return message_dna_from_dict(data)
