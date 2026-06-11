"""
run_transcript.py

Phase 3 runner — YouTube transcript fetch.

This script:
    1. Loads the existing CampaignBrief (must exist — run run_campaign.py first)
    2. Accepts a YouTube URL (from brief, argument, or prompt)
    3. Fetches the transcript using youtube-transcript-api
    4. If fetch succeeds: saves structured transcript JSON
    5. If fetch fails: runs the manual paste fallback
    6. Updates the CampaignBrief with the confirmed youtube_url
    7. Saves transcript to data/transcript_output.json

Architecture rule:
    MESSAGE BEFORE MEDIA.
    This script checks that BOTH MessageDNA and CampaignBrief exist
    before touching any YouTube content.

Usage:
    python run_transcript.py
    python run_transcript.py --url https://www.youtube.com/watch?v=VIDEO_ID
    python run_transcript.py --brief data/campaign_brief.json
    python run_transcript.py --manual   # skip auto-fetch, go straight to paste
"""

import argparse
import json
import os
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

from agents.transcript_agent import create_transcript_agent
from models.transcript_result import (
    TranscriptResult,
    save_transcript_result,
    load_transcript_result,
)
from tools.youtube_fetcher import fetch_transcript, parse_manual_transcript

# ── Constants ─────────────────────────────────────────────────────────

APP_NAME = "social_spark_studio"
DEFAULT_DNA_PATH = "data/message_dna_output.json"
DEFAULT_BRIEF_PATH = "data/campaign_brief.json"
DEFAULT_TRANSCRIPT_OUTPUT = "data/transcript_output.json"
USER_ID = "local_founder"
MIN_WORDS = 300


# ── Prerequisite checks ───────────────────────────────────────────────

def check_prerequisites(dna_path: str, brief_path: str) -> None:
    """
    Enforce MESSAGE BEFORE MEDIA.
    Hard-stop if MessageDNA or CampaignBrief are missing.
    """
    missing = []
    if not Path(dna_path).exists():
        missing.append(f"  MessageDNA not found:      {dna_path}\n  → Run: python run_interview.py")
    if not Path(brief_path).exists():
        missing.append(f"  CampaignBrief not found:   {brief_path}\n  → Run: python run_campaign.py")

    if missing:
        print("\n❌  Prerequisites missing — cannot proceed.\n")
        for m in missing:
            print(m)
        print()
        sys.exit(1)


def load_brief_url(brief_path: str) -> str:
    """Load the youtube_url from an existing CampaignBrief if present."""
    try:
        with open(brief_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("youtube_url", "")
    except Exception:
        return ""


def update_brief_url(brief_path: str, youtube_url: str) -> None:
    """Write the confirmed youtube_url back into the CampaignBrief JSON."""
    try:
        with open(brief_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["youtube_url"] = youtube_url
        with open(brief_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"✅ CampaignBrief updated with YouTube URL: {youtube_url}")
    except Exception as e:
        print(f"⚠️  Could not update CampaignBrief: {e}")


# ── ADK conversation helpers ──────────────────────────────────────────

def get_agent_text(events) -> str:
    """Collect text content from ADK event stream."""
    full_text = ""
    for event in events:
        if hasattr(event, "content") and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    full_text += part.text
    return full_text


def send_to_agent(runner, session_id: str, text: str) -> str:
    """Send a message and return the agent's full text response."""
    events = runner.run(
        user_id=USER_ID,
        session_id=session_id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text=text)],
        ),
    )
    return get_agent_text(events)


def print_agent(text: str) -> None:
    print(f"\n🤖 Agent: {text}")


# ── Manual paste fallback ─────────────────────────────────────────────

def run_manual_fallback(runner, session_id: str, video_url: str) -> TranscriptResult | None:
    """
    Guide the founder through pasting their transcript manually.
    Returns a TranscriptResult or None if the founder quits.
    """
    print("\n" + "-" * 50)
    print("MANUAL TRANSCRIPT MODE")
    print("-" * 50)
    print("\nPaste your transcript text below.")
    print("You can copy it from:")
    print("  • YouTube Studio (subtitles/captions section)")
    print("  • YouTube's auto-generated captions (click '...' → Open transcript)")
    print("  • Otter.ai, Descript, or any transcription tool")
    print("\nTip: Paste everything at once, then press Enter twice to finish.")
    print("Type 'skip' to try a different video instead.\n")

    lines = []
    print("Paste transcript (Enter twice when done):")
    empty_count = 0

    while True:
        try:
            line = input()
            if line.strip().lower() == "skip":
                return None
            if line == "":
                empty_count += 1
                if empty_count >= 2:
                    break
            else:
                empty_count = 0
                lines.append(line)
        except KeyboardInterrupt:
            return None

    raw_text = "\n".join(lines).strip()
    if not raw_text:
        print("\n⚠️  No text was pasted.")
        return None

    word_count = len(raw_text.split())
    print(f"\nProcessing {word_count} words...")

    result = parse_manual_transcript(raw_text, video_url, min_total_words=MIN_WORDS)

    if not result.is_success:
        response = send_to_agent(
            runner, session_id,
            f"Manual transcript failed: {result.error_type} — {result.error_message}"
        )
        print_agent(response)
        return None

    response = send_to_agent(
        runner, session_id,
        f"Manual transcript processed successfully. "
        f"Total words: {result.total_words}, segments: {result.total_segments}. "
        f"No timestamps available (manual mode)."
    )
    print_agent(response)
    return result


# ── Main ──────────────────────────────────────────────────────────────

def run_transcript(
    dna_path: str,
    brief_path: str,
    output_path: str,
    url_override: str = "",
    manual_mode: bool = False,
) -> None:
    """Full Phase 3 runner."""

    print("\n" + "=" * 60)
    print("  Social Spark Studio — Phase 3: Transcript")
    print("=" * 60)

    # Step 1: Prerequisites
    check_prerequisites(dna_path, brief_path)
    print(f"\n✅ MessageDNA found:    {dna_path}")
    print(f"✅ CampaignBrief found: {brief_path}")

    # Step 2: Determine the YouTube URL
    youtube_url = url_override or load_brief_url(brief_path) or ""

    if not youtube_url and not manual_mode:
        print("\nNo YouTube URL found in CampaignBrief.")
        print("Enter a YouTube URL, or press Enter to paste a transcript manually:")
        raw = input("URL: ").strip()
        if raw:
            youtube_url = raw
        else:
            manual_mode = True

    # Step 3: Set up ADK runner
    agent = create_transcript_agent()
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    session_id = f"transcript_{uuid.uuid4().hex[:8]}"
    runner.session_service.create_session_sync(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )

    result: TranscriptResult | None = None

    # Step 4a: Manual mode (skip auto-fetch)
    if manual_mode:
        print("\nManual transcript mode selected.")
        result = run_manual_fallback(runner, session_id, youtube_url or "manual")

    # Step 4b: Auto-fetch
    else:
        print(f"\nFetching transcript from: {youtube_url}")
        print("(This takes a few seconds...)\n")

        result = fetch_transcript(youtube_url, min_total_words=MIN_WORDS)

        # Report result to the agent and get a conversational response
        if result.is_success:
            agent_prompt = (
                f"Transcript fetch succeeded. "
                f"Video ID: {result.video_id}. "
                f"Segments: {result.total_segments}. "
                f"Total words: {result.total_words}. "
                f"Duration: {int(result.duration_seconds // 60)}m{int(result.duration_seconds % 60):02d}s. "
                f"Language: {result.language_code}. "
                f"Auto-generated: {result.is_generated}."
            )
        else:
            agent_prompt = (
                f"Transcript fetch failed. "
                f"Error type: {result.error_type}. "
                f"Message: {result.error_message}"
            )

        response = send_to_agent(runner, session_id, agent_prompt)
        print_agent(response)

        # Step 4c: Handle failure — offer fallback
        if not result.is_success:
            print()
            choice = input("Would you like to paste your transcript manually? (yes/no): ").strip().lower()
            if choice in ("yes", "y"):
                result = run_manual_fallback(runner, session_id, youtube_url)
            else:
                print("\nYou can run this script again with a different URL:")
                print("  python run_transcript.py --url YOUR_NEW_URL")
                return

    # Step 5: Final validation and save
    if result is None or not result.is_success:
        print("\n❌  No usable transcript. Phase 3 did not complete.")
        print("Run again with a different URL or paste a transcript manually.")
        return

    # Preview first 3 segments for the founder
    print("\n" + "-" * 50)
    print(f"TRANSCRIPT PREVIEW (first 3 of {result.total_segments} segments)")
    print("-" * 50)
    for seg in result.segments[:3]:
        ts = f"[{seg.start_timestamp}]" if seg.start_timestamp != "00:00" else "[manual]"
        print(f"\nSegment {seg.segment_index + 1} {ts} — {seg.word_count} words")
        preview = seg.text[:200] + ("..." if len(seg.text) > 200 else "")
        print(f"  {preview}")

    print()
    confirm = input("Does this look correct? Save and continue? (yes/no): ").strip().lower()
    if confirm not in ("yes", "y"):
        print("\nTranscript not saved. Run again to retry.")
        return

    # Save transcript
    save_transcript_result(result, output_path)

    # Update CampaignBrief with the confirmed URL
    if youtube_url and youtube_url != "manual":
        update_brief_url(brief_path, youtube_url)

    # Final summary
    print("\n" + "=" * 60)
    print("  Phase 3 complete!")
    print(f"  MessageDNA:    {dna_path}  (unchanged)")
    print(f"  CampaignBrief: {brief_path}  (url updated)")
    print(f"  Transcript:    {output_path}  (new)")
    print(f"\n  {result.summary()}")
    print("=" * 60)
    print("\nNext step (Phase 4): Run the moment selector agent.")
    print("  python run_moments.py")


# ── Entry point ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Phase 3: Fetch and structure a YouTube transcript."
    )
    parser.add_argument(
        "--url",
        default="",
        help="YouTube video URL (overrides URL stored in CampaignBrief)",
    )
    parser.add_argument(
        "--brief",
        default=DEFAULT_BRIEF_PATH,
        help=f"Path to CampaignBrief JSON (default: {DEFAULT_BRIEF_PATH})",
    )
    parser.add_argument(
        "--dna",
        default=DEFAULT_DNA_PATH,
        help=f"Path to MessageDNA JSON (default: {DEFAULT_DNA_PATH})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_TRANSCRIPT_OUTPUT,
        help=f"Path to save transcript JSON (default: {DEFAULT_TRANSCRIPT_OUTPUT})",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Skip auto-fetch and go straight to manual transcript paste",
    )
    args = parser.parse_args()

    if not os.getenv("GOOGLE_API_KEY") and not os.getenv("GOOGLE_CLOUD_PROJECT"):
        print("\n❌  No Google credentials found.")
        print("  export GOOGLE_API_KEY=your_key_here")
        sys.exit(1)

    run_transcript(
        dna_path=args.dna,
        brief_path=args.brief,
        output_path=args.output,
        url_override=args.url,
        manual_mode=args.manual,
    )


if __name__ == "__main__":
    main()
