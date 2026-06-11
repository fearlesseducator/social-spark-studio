"""
run_campaign.py

Phase 2 runner — CampaignBrief interview.

This script:
1. Loads the existing MessageDNA from disk (must exist — run run_interview.py first)
2. Shows the founder a summary of their MessageDNA so they know it's loaded
3. Runs the CampaignBrief interview (6 focused questions)
4. Saves the CampaignBrief as a new JSON file

IMPORTANT: This script reads MessageDNA. It never writes to it.
Every campaign gets its own campaign_brief file. MessageDNA stays untouched.

Usage:
    python run_campaign.py

    # Custom paths:
    python run_campaign.py --dna data/my_dna.json --output data/my_campaign.json

    # Use a better model for more natural conversation:
    CAMPAIGN_BRIEF_MODEL=gemini-2.5-flash python run_campaign.py
"""

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Fix emoji output on Windows console
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from google.adk.runners import InMemoryRunner
from google.genai import types

sys.path.insert(0, str(Path(__file__).parent))

from agents.campaign_brief_agent import create_campaign_brief_agent
from models.message_dna import load_message_dna
from models.campaign_brief import CampaignBrief, save_campaign_brief


# ── Constants ─────────────────────────────────────────────────────────

APP_NAME = "social_spark_studio"
DEFAULT_DNA_PATH = "data/message_dna_output.json"
DEFAULT_BRIEF_OUTPUT = "data/campaign_brief.json"
USER_ID = "local_founder"

COMPLETION_MARKER_START = "<campaign_brief_complete>"
COMPLETION_MARKER_END = "</campaign_brief_complete>"


# ── Helpers ───────────────────────────────────────────────────────────

def build_dna_context_summary(dna_path: str) -> tuple[str, str]:
    """
    Load MessageDNA and build a plain-text summary for the agent's context.

    Returns:
        (founder_name, summary_text)
        founder_name is used to personalize the welcome message.
        summary_text is injected into the agent instruction.
    """
    dna = load_message_dna(dna_path)

    fi = dna.founder_identity
    ap = dna.audience_profile
    fp = dna.founder_positioning
    vp = dna.voice_profile
    cd = dna.content_direction

    lines = [
        f"Founder: {fi.founder_name}" + (f" | Brand: {fi.brand_name}" if fi.brand_name else ""),
        f"Known for: {fi.known_for}" if fi.known_for else "",
        f"Ideal audience: {ap.ideal_audience}" if ap.ideal_audience else "",
        f"Core problem solved: {ap.core_problem_solved}" if ap.core_problem_solved else "",
        f"Contrarian belief: {fp.contrarian_belief}" if fp.contrarian_belief else "",
        f"Brand voice: {', '.join(vp.brand_voice_words)}" if vp.brand_voice_words else "",
        f"Content pillars: {', '.join(cd.content_pillars)}" if cd.content_pillars else "",
        f"Credibility markers: {'; '.join(cd.credibility_markers[:2])}" if cd.credibility_markers else "",
    ]

    summary = "\n".join(line for line in lines if line)
    return fi.founder_name or "Founder", summary


def extract_brief_from_response(text: str) -> dict | None:
    """Extract the JSON from the agent's <campaign_brief_complete> block."""
    pattern = re.compile(
        r"<campaign_brief_complete>\s*(.*?)\s*</campaign_brief_complete>",
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(1).strip())
    except json.JSONDecodeError as e:
        print(f"\n⚠️  JSON parse error: {e}")
        return None


def get_agent_text(events) -> str:
    """Collect text from ADK event stream."""
    full_text = ""
    for event in events:
        if hasattr(event, "content") and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    full_text += part.text
    return full_text


def print_agent(text: str) -> None:
    """Print agent response, hiding raw JSON blocks."""
    if COMPLETION_MARKER_START in text:
        before = text.split(COMPLETION_MARKER_START)[0].strip()
        after = text.split(COMPLETION_MARKER_END)[-1].strip()
        if before:
            print(f"\n🤖 Agent: {before}")
        if after:
            print(f"\n🤖 Agent: {after}")
    else:
        print(f"\n🤖 Agent: {text}")


# ── Main ──────────────────────────────────────────────────────────────

def run_campaign_brief(dna_path: str, output_path: str) -> None:
    """Run the CampaignBrief interview."""

    print("\n" + "=" * 60)
    print("  Social Spark Studio — Campaign Brief")
    print("=" * 60)

    # ── Step 1: Load MessageDNA ────────────────────────────────────
    if not Path(dna_path).exists():
        print(f"\n❌  MessageDNA not found at: {dna_path}")
        print("\nYou need to complete your MessageDNA interview first.")
        print("Run:  python run_interview.py")
        sys.exit(1)

    print(f"\nLoading your MessageDNA from: {dna_path}")
    founder_name, dna_summary = build_dna_context_summary(dna_path)

    print(f"\n✅ MessageDNA loaded for: {founder_name}")
    print("\nYour MessageDNA context:")
    print("-" * 40)
    print(dna_summary)
    print("-" * 40)
    print("\nThis context will be used to generate content in your voice.")
    print("It will NOT be changed during this campaign brief.\n")

    # ── Step 2: Set up ADK runner ──────────────────────────────────
    agent = create_campaign_brief_agent(message_dna_summary=dna_summary)

    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)

    session_id = f"campaign_{uuid.uuid4().hex[:8]}"
    runner.session_service.create_session_sync(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )

    # ── Step 3: Opening message ────────────────────────────────────
    print("Starting campaign brief interview...")
    print("Type your answers and press Enter. Type 'quit' to exit.\n")

    opening = types.Content(
        role="user",
        parts=[types.Part(text="I'm ready to set up my campaign brief.")],
    )
    opening_events = runner.run(
        user_id=USER_ID,
        session_id=session_id,
        new_message=opening,
    )
    print_agent(get_agent_text(opening_events))

    # ── Step 4: Conversation loop ──────────────────────────────────
    while True:
        try:
            print()
            user_input = input("You: ").strip()

            if user_input.lower() in ("quit", "exit", "q"):
                print("\nCampaign brief paused. Run again to start a new one.")
                break

            if not user_input:
                print("(Please type something.)")
                continue

            response_events = runner.run(
                user_id=USER_ID,
                session_id=session_id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part(text=user_input)],
                ),
            )
            response_text = get_agent_text(response_events)
            print_agent(response_text)

            # Check for completion
            if COMPLETION_MARKER_START in response_text:
                brief_data = extract_brief_from_response(response_text)
                if brief_data:
                    # Inject a campaign ID
                    brief_data["campaign_id"] = f"campaign_{uuid.uuid4().hex[:8]}"
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump(brief_data, f, indent=2)
                    print(f"\n✅ CampaignBrief saved to: {output_path}")
                    print("\n" + "=" * 60)
                    print("  Phase 2 complete!")
                    print(f"  MessageDNA:     {dna_path}  (unchanged)")
                    print(f"  CampaignBrief:  {output_path}  (new)")
                    print("=" * 60)
                    print("\nNext step (Phase 3): Run the YouTube transcript agent.")
                    print("  python run_transcript.py --url YOUR_YOUTUBE_URL")
                break

        except KeyboardInterrupt:
            print("\n\nInterrupted. Run again to start a new campaign brief.")
            break


def main():
    parser = argparse.ArgumentParser(
        description="Run the Social Spark Studio CampaignBrief interview."
    )
    parser.add_argument(
        "--dna",
        default=DEFAULT_DNA_PATH,
        help=f"Path to MessageDNA JSON file (default: {DEFAULT_DNA_PATH})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_BRIEF_OUTPUT,
        help=f"Path to save CampaignBrief JSON (default: {DEFAULT_BRIEF_OUTPUT})",
    )
    args = parser.parse_args()

    if not os.getenv("GOOGLE_API_KEY") and not os.getenv("GOOGLE_CLOUD_PROJECT"):
        print("\n❌  No Google credentials found.")
        print("  export GOOGLE_API_KEY=your_key_here")
        sys.exit(1)

    run_campaign_brief(dna_path=args.dna, output_path=args.output)


if __name__ == "__main__":
    main()
