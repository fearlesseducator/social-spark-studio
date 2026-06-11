"""
models/voice_conversation.py

Data models for the Voice Conversation Studio.

Tracks state across the 4-block voice interview that captures
MessageDNA before any YouTube video is analyzed.

These models are deliberately separate from MessageDNA itself.
The voice conversation produces raw, confirmed founder language.
The existing message_dna_agent (or direct mapping) then converts
that confirmed language into the structured MessageDNA JSON that
feeds the rest of the engine.

Block structure maps to the product guide:
    Block 1 — Audience and Pain
    Block 2 — Offer and Transformation
    Block 3 — Campaign Goal and Voice
    Block 4 — Founder Positioning

Each block has exchanges (question + answer pairs) and a confirmed
summary. Blocks are saved to disk as they are confirmed so the
session can resume if interrupted.
"""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional
from datetime import datetime


# ---------------------------------------------------------------------------
# A single Q&A exchange within a block
# ---------------------------------------------------------------------------

@dataclass
class VoiceExchange:
    """
    One question-answer pair within a conversation block.

    agent_question:  What the agent asked (text, spoken via TTS).
    founder_response: What the founder said (transcribed by STT).
    timestamp:        When this exchange happened (ISO format).
    was_clarifying:   True if this was a follow-up clarification question.
    input_mode:       "voice" or "text" (fallback mode).
    """
    agent_question: str = ""
    founder_response: str = ""
    timestamp: str = ""
    was_clarifying: bool = False
    input_mode: str = "voice"   # "voice" | "text"


# ---------------------------------------------------------------------------
# One conversation block
# ---------------------------------------------------------------------------

@dataclass
class ConversationBlock:
    """
    One of the four voice conversation blocks.

    A block is complete when:
    1. All questions have been answered
    2. The agent has read back a summary
    3. The founder has confirmed the summary (confirmed = True)

    extracted_fields holds the structured data extracted from the
    confirmed conversation. This is what feeds into MessageDNA.
    """
    block_number: int = 0           # 1-4
    block_name: str = ""            # e.g. "Audience and Pain"
    exchanges: List[VoiceExchange] = field(default_factory=list)
    summary_read_back: str = ""     # The summary the agent spoke
    confirmed: bool = False         # True after founder confirms summary
    confirmed_at: str = ""          # ISO timestamp of confirmation
    extracted_fields: dict = field(default_factory=dict)
    # Structured fields extracted from this block's confirmed conversation.
    # Keys match MessageDNA field names so they can be mapped directly.

    def add_exchange(
        self,
        agent_question: str,
        founder_response: str,
        was_clarifying: bool = False,
        input_mode: str = "voice",
    ) -> None:
        """Add a Q&A exchange to this block."""
        self.exchanges.append(VoiceExchange(
            agent_question=agent_question,
            founder_response=founder_response,
            timestamp=datetime.utcnow().isoformat(),
            was_clarifying=was_clarifying,
            input_mode=input_mode,
        ))

    def confirm(self, extracted_fields: dict) -> None:
        """Mark this block as confirmed with extracted structured fields."""
        self.confirmed = True
        self.confirmed_at = datetime.utcnow().isoformat()
        self.extracted_fields = extracted_fields

    @property
    def full_transcript(self) -> str:
        """Return all exchanges as a readable transcript string."""
        lines = []
        for ex in self.exchanges:
            lines.append(f"Agent: {ex.agent_question}")
            lines.append(f"Founder: {ex.founder_response}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Full voice conversation session
# ---------------------------------------------------------------------------

@dataclass
class VoiceConversationSession:
    """
    The complete state of one voice conversation session.

    Persisted to data/voice_conversation_state.json after every
    block confirmation so the session can resume if interrupted.

    After all 4 blocks are confirmed, to_message_dna_dict() produces
    a dict compatible with the existing MessageDNA JSON format that
    load_message_dna() can read directly.
    """
    session_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    input_mode: str = "voice"       # "voice" | "text" (fallback)
    blocks: List[ConversationBlock] = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat()
        if not self.blocks:
            self.blocks = [
                ConversationBlock(block_number=1, block_name="Audience and Pain"),
                ConversationBlock(block_number=2, block_name="Offer and Transformation"),
                ConversationBlock(block_number=3, block_name="Campaign Goal and Voice"),
                ConversationBlock(block_number=4, block_name="Founder Positioning"),
            ]

    def current_block(self) -> Optional[ConversationBlock]:
        """Return the first unconfirmed block, or None if all done."""
        for block in self.blocks:
            if not block.confirmed:
                return block
        return None

    @property
    def is_complete(self) -> bool:
        """True when all 4 blocks are confirmed."""
        return all(b.confirmed for b in self.blocks)

    @property
    def confirmed_block_count(self) -> int:
        return sum(1 for b in self.blocks if b.confirmed)

    def get_block(self, block_number: int) -> Optional[ConversationBlock]:
        for block in self.blocks:
            if block.block_number == block_number:
                return block
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["updated_at"] = datetime.utcnow().isoformat()
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "VoiceConversationSession":
        """Load a session from a saved dict. Tolerates missing fields."""
        session = cls(
            session_id=data.get("session_id", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            input_mode=data.get("input_mode", "voice"),
        )
        session.blocks = []
        for b in data.get("blocks", []):
            exchanges = [VoiceExchange(**e) for e in b.get("exchanges", [])]
            block = ConversationBlock(
                block_number=b.get("block_number", 0),
                block_name=b.get("block_name", ""),
                exchanges=exchanges,
                summary_read_back=b.get("summary_read_back", ""),
                confirmed=b.get("confirmed", False),
                confirmed_at=b.get("confirmed_at", ""),
                extracted_fields=b.get("extracted_fields", {}),
            )
            session.blocks.append(block)
        return session

    def to_message_dna_dict(self) -> dict:
        """
        Convert confirmed session data into a MessageDNA-compatible dict.

        The returned dict has the same structure as message_dna_output.json
        so it can be loaded directly by load_message_dna() from
        models/message_dna.py.

        Fields are drawn from extracted_fields in each confirmed block.
        Voice-sensitive fields preserve the founder's exact language.
        """
        # Collect all extracted fields from all confirmed blocks
        all_fields: dict = {}
        for block in self.blocks:
            if block.confirmed:
                all_fields.update(block.extracted_fields)

        def get(key, default=""):
            return all_fields.get(key, default)

        def get_list(key):
            v = all_fields.get(key, [])
            return v if isinstance(v, list) else [v] if v else []

        return {
            "founder_identity": {
                "founder_name":  get("founder_name"),
                "brand_name":    get("brand_name"),
                "known_for":     get("known_for"),
            },
            "audience_profile": {
                "ideal_audience":          get("ideal_audience"),
                "audience_worldview":      get("audience_worldview"),
                "core_problem_solved":     get("core_problem_solved"),
                "audience_misconceptions": get("audience_misconceptions"),
            },
            "founder_positioning": {
                "signature_beliefs":    get_list("signature_beliefs"),
                "contrarian_belief":    get("contrarian_belief"),
                "origin_story":         get("origin_story"),
                "future_vision":        get("future_vision"),
            },
            "voice_profile": {
                "brand_voice_words":  get_list("brand_voice_words"),
                "phrases_to_use":     get_list("phrases_to_use"),
                "phrases_to_avoid":   get_list("phrases_to_avoid"),
                "tone_rules":         get("tone_rules"),
                "teaching_style":     get("teaching_style"),
                "cta_style":          get("cta_style"),
            },
            "content_direction": {
                "content_pillars":    get_list("content_pillars"),
                "visual_metaphors":   get_list("visual_metaphors"),
                "visual_avoid_list":  get_list("visual_avoid_list"),
                "credibility_markers": get_list("credibility_markers"),
            },
        }


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------

def save_voice_session(session: VoiceConversationSession, filepath: str) -> None:
    """Save session state to JSON."""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(session.to_json())


def load_voice_session(filepath: str) -> VoiceConversationSession:
    """Load session state from JSON. Returns a fresh session if file missing."""
    if not Path(filepath).exists():
        return VoiceConversationSession()
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return VoiceConversationSession.from_dict(data)


# ── Firestore persistence ───────────────────────────────────────────────

def save_voice_session_firestore(session: VoiceConversationSession, founder_id: str = "default_founder") -> None:
    """Save VoiceConversationSession to Firestore (collection: voice_session)."""
    from tools.firestore_tool import save_document
    save_document("voice_session", founder_id, session.to_dict())


def load_voice_session_firestore(founder_id: str = "default_founder") -> VoiceConversationSession | None:
    """Load VoiceConversationSession from Firestore. Returns None if not found."""
    from tools.firestore_tool import load_document
    data = load_document("voice_session", founder_id)
    if data is None:
        return None
    return VoiceConversationSession.from_dict(data)
