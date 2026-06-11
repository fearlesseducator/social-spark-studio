"""
run_moments.py

Phase 4 runner — Transcript Moment Selector.

This script:
    1. Loads MessageDNA, CampaignBrief, and Transcript (must all exist)
    2. Builds a rich context block combining all three
    3. Sends the full context to moment_selector_agent in one message
    4. Extracts the <moments_complete> JSON block from the response
    5. Displays a preview of selected moments for the founder
    6. Saves moments to data/moments_output.json

Architecture rule: MESSAGE BEFORE MEDIA.
    This script hard-stops if MessageDNA, CampaignBrief, or Transcript
    are missing. All three must exist before moment selection runs.

Usage:
    python run_moments.py
    python run_moments.py --transcript data/transcript_output.json
    python run_moments.py --dna data/my_dna.json --brief data/my_brief.json
    MOMENT_SELECTOR_MODEL=gemini-2.5-flash python run_moments.py
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

from agents.moment_selector_agent import create_moment_selector_agent
from models.transcript_moment import (
    TranscriptMoment,
    MomentSelectionResult,
    save_moments,
    load_moments,
)
from models.transcript_result import load_transcript_result

# ── Constants ─────────────────────────────────────────────────────────

APP_NAME = "social_spark_studio"
USER_ID = "local_founder"
DEFAULT_DNA_PATH = "data/message_dna_output.json"
DEFAULT_BRIEF_PATH = "data/campaign_brief.json"
DEFAULT_TRANSCRIPT_PATH = "data/transcript_output.json"
DEFAULT_MOMENTS_OUTPUT = "data/moments_output.json"

COMPLETION_MARKER_START = "<moments_complete>"
COMPLETION_MARKER_END = "</moments_complete>"


# ── Prerequisites ─────────────────────────────────────────────────────

def check_prerequisites(dna_path: str, brief_path: str, transcript_path: str) -> None:
    """Hard-stop if any required file is missing."""
    missing = []
    if not Path(dna_path).exists():
        missing.append(
            f"  MessageDNA not found:    {dna_path}\n"
            f"  → Run: python run_interview.py"
        )
    if not Path(brief_path).exists():
        missing.append(
            f"  CampaignBrief not found: {brief_path}\n"
            f"  → Run: python run_campaign.py"
        )
    if not Path(transcript_path).exists():
        missing.append(
            f"  Transcript not found:    {transcript_path}\n"
            f"  → Run: python run_transcript.py"
        )
    if missing:
        print("\n❌  Prerequisites missing — cannot proceed.\n")
        for m in missing:
            print(m)
        sys.exit(1)


# ── Context builder ───────────────────────────────────────────────────

def build_agent_context(
    dna_path: str,
    brief_path: str,
    transcript_path: str,
) -> tuple[str, str, str]:
    """
    Load all three data files and build the combined context string
    that gets sent to the moment_selector_agent.

    Returns:
        (context_text, video_id, video_url)
    """
    # Load MessageDNA
    with open(dna_path, "r", encoding="utf-8") as f:
        dna = json.load(f)

    # Load CampaignBrief
    with open(brief_path, "r", encoding="utf-8") as f:
        brief = json.load(f)

    # Load TranscriptResult
    transcript = load_transcript_result(transcript_path)
    video_id = transcript.video_id
    video_url = transcript.video_url

    # ── MessageDNA summary ────────────────────────────────────────
    fi = dna.get("founder_identity", {})
    ap = dna.get("audience_profile", {})
    fp = dna.get("founder_positioning", {})
    vp = dna.get("voice_profile", {})
    cd = dna.get("content_direction", {})

    pillars = cd.get("content_pillars", [])
    pillar_text = "\n".join(f"  - {p}" for p in pillars) if pillars else "  (none set)"

    beliefs = fp.get("signature_beliefs", [])
    belief_text = "\n".join(f"  - {b}" for b in beliefs) if beliefs else "  (none set)"

    avoid_phrases = vp.get("phrases_to_avoid", [])
    avoid_text = ", ".join(avoid_phrases) if avoid_phrases else "(none)"

    dna_block = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FOUNDER MESSAGEDNA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Founder: {fi.get('founder_name', '')} | Brand: {fi.get('brand_name', '')}
Known for: {fi.get('known_for', '')}
Ideal audience: {ap.get('ideal_audience', '')}
Core problem solved: {ap.get('core_problem_solved', '')}

Contrarian belief:
  {fp.get('contrarian_belief', '')}

Origin story (summary):
  {fp.get('origin_story', '')[:300]}...

Signature beliefs:
{belief_text}

Content pillars (USE THESE EXACT NAMES when assigning pillars):
{pillar_text}

Brand voice words: {', '.join(vp.get('brand_voice_words', []))}
Phrases to AVOID in hook ideas: {avoid_text}
"""

    # ── CampaignBrief summary ─────────────────────────────────────
    platforms = brief.get("target_platforms", [])
    platform_text = ", ".join(platforms) if platforms else "(none set)"

    brief_block = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CAMPAIGN BRIEF
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Campaign goal: {brief.get('campaign_goal', '')}
Selected offer: {brief.get('selected_offer', '')}
Primary CTA: {brief.get('primary_cta', '')}
Target platforms: {platform_text}
Campaign theme: {brief.get('campaign_theme', '')}
Timely context: {brief.get('timely_context', '') or '(none)'}
"""

    # ── Transcript segments ───────────────────────────────────────
    seg_lines = []
    for seg in transcript.segments:
        ts = (
            f"[{seg.start_timestamp} → {seg.end_timestamp}]"
            if seg.start_timestamp and seg.start_timestamp != "manual"
            else "[manual — no timestamps]"
        )
        seg_lines.append(
            f"\nSEGMENT {seg.segment_index} {ts} "
            f"({seg.word_count} words) "
            f"clip: {seg.clip_url or 'N/A'}\n"
            f"{seg.text}"
        )

    transcript_block = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TRANSCRIPT — {transcript.total_segments} segments · {transcript.total_words} words
Video ID: {video_id}
Video URL: {video_url}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{''.join(seg_lines)}
"""

    # ── Final combined prompt ─────────────────────────────────────
    context = (
        "Please analyse the following transcript and select the best moments "
        "for this founder's social media campaign. "
        "Use the MessageDNA and CampaignBrief to guide your selection.\n\n"
        + dna_block
        + brief_block
        + transcript_block
    )

    return context, video_id, video_url


# ── Response extraction ───────────────────────────────────────────────

def extract_moments_from_response(
    text: str,
    total_segments: int,
    video_id: str,
    video_url: str,
) -> MomentSelectionResult | None:
    """
    Find the <moments_complete>...</moments_complete> block in the
    agent's response and parse it into a MomentSelectionResult.
    """
    pattern = re.compile(
        r"<moments_complete>\s*(.*?)\s*</moments_complete>",
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return None

    try:
        data = json.loads(match.group(1).strip())
    except json.JSONDecodeError as e:
        print(f"\n⚠️  JSON parse error in moments response: {e}")
        print("Raw snippet:")
        print(match.group(1)[:400])
        return None

    # Inject total_segments if agent left it as 0
    if not data.get("total_segments_reviewed"):
        data["total_segments_reviewed"] = total_segments

    result = MomentSelectionResult.from_dict(data)

    # Reindex moments to be sequential just in case
    for i, moment in enumerate(result.moments):
        moment.moment_index = i
        # Fill word_count if agent left it as 0
        if not moment.word_count and moment.quote:
            moment.word_count = len(moment.quote.split())

    return result


# ── ADK helpers ───────────────────────────────────────────────────────

def get_agent_text(events) -> str:
    """Collect full text from ADK event stream."""
    full_text = ""
    for event in events:
        if hasattr(event, "content") and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    full_text += part.text
    return full_text


# ── Main ──────────────────────────────────────────────────────────────

def run_moments(
    dna_path: str,
    brief_path: str,
    transcript_path: str,
    output_path: str,
) -> None:
    """Full Phase 4 runner."""

    print("\n" + "=" * 60)
    print("  Social Spark Studio — Phase 4: Moment Selection")
    print("=" * 60)

    # Step 1: Prerequisites
    check_prerequisites(dna_path, brief_path, transcript_path)
    print(f"\n✅ MessageDNA:    {dna_path}")
    print(f"✅ CampaignBrief: {brief_path}")
    print(f"✅ Transcript:    {transcript_path}")

    # Step 2: Build context
    print("\nBuilding context from MessageDNA + CampaignBrief + Transcript...")
    context, video_id, video_url = build_agent_context(
        dna_path, brief_path, transcript_path
    )

    # Load transcript just to get segment count for the result
    transcript = load_transcript_result(transcript_path)
    total_segments = transcript.total_segments

    print(f"   {total_segments} segments · {transcript.total_words} words to analyse")

    # Step 3: Set up ADK runner
    agent = create_moment_selector_agent()
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    session_id = f"moments_{uuid.uuid4().hex[:8]}"
    runner.session_service.create_session_sync(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )

    # Step 4: Send context and get response
    print(f"\nRunning moment_selector_agent ({agent.model})...")
    print("This analyses the full transcript — may take 15-30 seconds.\n")

    events = runner.run(
        user_id=USER_ID,
        session_id=session_id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text=context)],
        ),
    )

    response_text = get_agent_text(events)

    # Step 5: Extract and validate
    result = extract_moments_from_response(
        response_text, total_segments, video_id, video_url
    )

    if result is None:
        print("\n❌  Could not extract moments from agent response.")
        print("    The agent may have returned an unexpected format.")
        print("\nRaw response (first 800 chars):")
        print(response_text[:800])
        sys.exit(1)

    if result.total_moments == 0:
        print("\n⚠️  Agent returned 0 moments.")
        if result.selection_notes:
            print(f"    Agent notes: {result.selection_notes}")
        print("    This may mean the transcript has no social-worthy content.")
        sys.exit(1)

    # Step 6: Display preview
    print("\n" + "-" * 60)
    print(f"MOMENTS SELECTED — {result.total_moments} of {total_segments} segments")
    print("-" * 60)

    if result.selection_notes:
        print(f"\nAgent notes: {result.selection_notes}\n")

    for moment in result.moments:
        print(f"\n{moment.summary_line()}")
        print(f"  Why: {moment.why_social_worthy}")
        print(f"  Hook direction: {moment.hook_idea}")

    print()
    confirm = input(
        f"Save these {result.total_moments} moments and continue? (yes/no): "
    ).strip().lower()

    if confirm not in ("yes", "y"):
        print("\nMoments not saved. Run again to retry.")
        return

    # Step 7: Save
    save_moments(result, output_path)

    print("\n" + "=" * 60)
    print("  Phase 4 complete!")
    print(f"  MessageDNA:    {dna_path}  (unchanged)")
    print(f"  CampaignBrief: {brief_path}  (unchanged)")
    print(f"  Transcript:    {transcript_path}  (unchanged)")
    print(f"  Moments:       {output_path}  (new)")
    print(f"\n  {result.summary()}")
    print("=" * 60)
    print("\nNext step (Phase 5): Run the caption and hashtag writer.")
    print("  python run_captions.py")


# ── Entry point ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Phase 4: Select social-worthy moments from the transcript."
    )
    parser.add_argument("--dna", default=DEFAULT_DNA_PATH,
                        help=f"MessageDNA JSON path (default: {DEFAULT_DNA_PATH})")
    parser.add_argument("--brief", default=DEFAULT_BRIEF_PATH,
                        help=f"CampaignBrief JSON path (default: {DEFAULT_BRIEF_PATH})")
    parser.add_argument("--transcript", default=DEFAULT_TRANSCRIPT_PATH,
                        help=f"Transcript JSON path (default: {DEFAULT_TRANSCRIPT_PATH})")
    parser.add_argument("--output", default=DEFAULT_MOMENTS_OUTPUT,
                        help=f"Output path (default: {DEFAULT_MOMENTS_OUTPUT})")
    args = parser.parse_args()

    if not os.getenv("GOOGLE_API_KEY") and not os.getenv("GOOGLE_CLOUD_PROJECT"):
        print("\n❌  No Google credentials found.")
        print("  export GOOGLE_API_KEY=your_key_here")
        sys.exit(1)

    run_moments(
        dna_path=args.dna,
        brief_path=args.brief,
        transcript_path=args.transcript,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
