"""
utils/interview_state.py

Tracks where we are in the MessageDNA interview so the agent
can pick up from where it left off and the runner knows when
the interview is complete.

The interview has 5 sections, each with a set of questions.
We track which section we're in and which questions are done.
"""

from dataclasses import dataclass, field
from typing import List, Optional


# ── Section definitions ──────────────────────────────────────────────
# Each section has a name and the list of fields it populates in MessageDNA.
# The agent uses these to know what to ask about in each section.

INTERVIEW_SECTIONS = [
    {
        "id": "founder_identity",
        "name": "Founder Identity",
        "description": "Who you are and what you want to be known for",
        "fields": ["founder_name", "brand_name", "known_for"],
    },
    {
        "id": "audience_profile",
        "name": "Audience and Pain",
        "description": "Who you serve and what problem you solve for them",
        "fields": ["ideal_audience", "audience_worldview", "core_problem_solved", "audience_misconceptions"],
    },
    {
        "id": "founder_positioning",
        "name": "Beliefs and Positioning",
        "description": "What you believe, your origin story, and your vision",
        "fields": ["signature_beliefs", "contrarian_belief", "origin_story", "future_vision"],
    },
    {
        "id": "voice_profile",
        "name": "Voice and Language",
        "description": "How you speak, write, and what phrases sound like you",
        "fields": ["brand_voice_words", "phrases_to_use", "phrases_to_avoid", "tone_rules", "teaching_style", "cta_style"],
    },
    {
        "id": "content_direction",
        "name": "Content and Visual Direction",
        "description": "Your content pillars, visual style, and proof points",
        "fields": ["content_pillars", "visual_metaphors", "visual_avoid_list", "credibility_markers"],
    },
]


@dataclass
class InterviewState:
    """
    Tracks progress through the MessageDNA interview.

    The agent reads this at the start of each turn to understand
    where the conversation is and what to do next.
    """
    current_section_index: int = 0          # Which of the 5 sections we're on (0-4)
    current_section_confirmed: bool = False  # Has the founder confirmed this section's summary?
    completed_sections: List[str] = field(default_factory=list)  # IDs of confirmed sections
    interview_complete: bool = False         # True when all 5 sections are confirmed
    awaiting_confirmation: bool = False      # True when agent just read back a summary
    last_section_summary: str = ""           # The summary the agent read back

    @property
    def current_section(self) -> Optional[dict]:
        """Return the current section definition, or None if all done."""
        if self.current_section_index < len(INTERVIEW_SECTIONS):
            return INTERVIEW_SECTIONS[self.current_section_index]
        return None

    @property
    def sections_remaining(self) -> int:
        """How many sections still need to be confirmed."""
        return len(INTERVIEW_SECTIONS) - len(self.completed_sections)

    def confirm_current_section(self) -> None:
        """Mark the current section as confirmed and advance to the next one."""
        if self.current_section:
            section_id = self.current_section["id"]
            if section_id not in self.completed_sections:
                self.completed_sections.append(section_id)
        self.current_section_index += 1
        self.current_section_confirmed = False
        self.awaiting_confirmation = False
        self.last_section_summary = ""
        # Check if we just finished the last section
        if self.current_section_index >= len(INTERVIEW_SECTIONS):
            self.interview_complete = True

    def to_dict(self) -> dict:
        """Convert to a plain dict so it can be stored in ADK session state."""
        return {
            "current_section_index": self.current_section_index,
            "current_section_confirmed": self.current_section_confirmed,
            "completed_sections": self.completed_sections,
            "interview_complete": self.interview_complete,
            "awaiting_confirmation": self.awaiting_confirmation,
            "last_section_summary": self.last_section_summary,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "InterviewState":
        """Rebuild an InterviewState from a plain dict."""
        return cls(
            current_section_index=data.get("current_section_index", 0),
            current_section_confirmed=data.get("current_section_confirmed", False),
            completed_sections=data.get("completed_sections", []),
            interview_complete=data.get("interview_complete", False),
            awaiting_confirmation=data.get("awaiting_confirmation", False),
            last_section_summary=data.get("last_section_summary", ""),
        )
