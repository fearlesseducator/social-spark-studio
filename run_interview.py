"""
run_interview.py

Local runner for the MessageDNA interview.

Run this file to start a MessageDNA interview in your terminal.
The agent will ask you questions one at a time.
When the interview is complete, it saves a JSON file to:
    data/message_dna_output.json

Usage:
    python run_interview.py

    # Or to save to a custom path:
    python run_interview.py --output data/my_founder_dna.json

    # To use a specific Gemini model:
    MESSAGE_DNA_MODEL=gemini-2.0-pro python run_interview.py

Requirements:
    - GOOGLE_API_KEY environment variable set (see README.md)
    OR
    - GOOGLE_CLOUD_PROJECT + Application Default Credentials

What this script does:
    1. Creates an ADK InMemoryRunner with the MessageDNA agent
    2. Creates a session for this interview
    3. Sends each user message to the agent and prints the response
    4. Watches for the <message_dna_complete> marker in the response
    5. Extracts the JSON and saves it to disk
    6. Prints a confirmation when done
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Fix emoji output on Windows console
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from google.adk.runners import InMemoryRunner
from google.genai import types

# Make sure our local packages are importable
sys.path.insert(0, str(Path(__file__).parent))

from agents.message_dna_agent import create_message_dna_agent
from models.message_dna import MessageDNA, save_message_dna, load_message_dna


# ── Constants ─────────────────────────────────────────────────────────

APP_NAME = "social_spark_studio"
DEFAULT_OUTPUT_PATH = "data/message_dna_output.json"
USER_ID = "local_founder"   # In production this would be the Firebase UID
SESSION_ID = "interview_001"

# The agent outputs this tag when the interview is complete
COMPLETION_MARKER_START = "<message_dna_complete>"
COMPLETION_MARKER_END = "</message_dna_complete>"


# ── Helpers ───────────────────────────────────────────────────────────

def extract_json_from_response(text: str) -> dict | None:
    """
    Look for the <message_dna_complete>...</message_dna_complete> block
    in the agent's response and extract the JSON inside it.

    Returns the parsed dict, or None if not found.
    """
    pattern = re.compile(
        r"<message_dna_complete>\s*(.*?)\s*</message_dna_complete>",
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return None
    raw_json = match.group(1).strip()
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError as e:
        print(f"\n⚠️  The agent returned JSON that couldn't be parsed: {e}")
        print("Raw content received:")
        print(raw_json[:500])
        return None


def save_dna_from_dict(data: dict, output_path: str) -> None:
    """
    Build a MessageDNA object from the dict the agent returned
    and save it to disk.
    """
    # Ensure the output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Write the raw JSON directly (simplest approach — preserves all agent output)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"\n✅ MessageDNA saved to: {output_path}")


def get_agent_text_response(events) -> str:
    """
    Extract the text content from ADK event stream.
    ADK returns a generator of Event objects — we collect the
    text from all model output events.
    """
    full_text = ""
    for event in events:
        # ADK events have a .content attribute with parts
        if hasattr(event, "content") and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    full_text += part.text
    return full_text


def print_agent_response(text: str) -> None:
    """Print the agent's response with clear formatting."""
    # Don't print the raw JSON block — just the conversational parts
    if COMPLETION_MARKER_START in text:
        # Print everything before the JSON block
        before_json = text.split(COMPLETION_MARKER_START)[0].strip()
        if before_json:
            print(f"\n🤖 Agent: {before_json}")
        # Print everything after the JSON block
        after_json = text.split(COMPLETION_MARKER_END)[-1].strip()
        if after_json:
            print(f"\n🤖 Agent: {after_json}")
    else:
        print(f"\n🤖 Agent: {text}")


# ── Main interview loop ───────────────────────────────────────────────

def run_interview(output_path: str = DEFAULT_OUTPUT_PATH) -> None:
    """
    Run the full MessageDNA interview in the terminal.

    This is a simple read-print loop:
        1. Agent speaks first (we send an empty start message)
        2. Founder types their answer
        3. Agent responds
        4. Repeat until <message_dna_complete> appears
        5. Save the JSON and exit
    """

    print("\n" + "=" * 60)
    print("  Social Spark Studio — MessageDNA Interview")
    print("=" * 60)
    print("\nStarting your MessageDNA interview...")
    print("Type your answers and press Enter. Type 'quit' to exit.\n")

    # ── Set up ADK runner and session ──────────────────────────────
    agent = create_message_dna_agent()

    runner = InMemoryRunner(
        agent=agent,
        app_name=APP_NAME,
    )

    # Create the session (ADK requires a session before running)
    # For local testing we use synchronous create_session_sync
    session = runner.session_service.create_session_sync(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=SESSION_ID,
    )

    # ── Send the opening trigger ───────────────────────────────────
    # We send a simple "start" message to get the agent's welcome message.
    opening_message = types.Content(
        role="user",
        parts=[types.Part(text="Hello, I'm ready to start my MessageDNA interview.")],
    )

    opening_events = runner.run(
        user_id=USER_ID,
        session_id=SESSION_ID,
        new_message=opening_message,
    )

    opening_text = get_agent_text_response(opening_events)
    print_agent_response(opening_text)

    # ── Main conversation loop ─────────────────────────────────────
    interview_done = False

    while not interview_done:
        try:
            # Get founder's input
            print()
            user_input = input("You: ").strip()

            if user_input.lower() in ("quit", "exit", "q"):
                print("\nInterview paused. Your progress has not been saved.")
                print("Run the script again to start a new interview.")
                break

            if not user_input:
                print("(Please type something — the agent is waiting for your answer.)")
                continue

            # Send to agent
            user_message = types.Content(
                role="user",
                parts=[types.Part(text=user_input)],
            )

            response_events = runner.run(
                user_id=USER_ID,
                session_id=SESSION_ID,
                new_message=user_message,
            )

            response_text = get_agent_text_response(response_events)

            # Print the agent's response
            print_agent_response(response_text)

            # Check if the interview is complete
            if COMPLETION_MARKER_START in response_text:
                dna_data = extract_json_from_response(response_text)
                if dna_data:
                    save_dna_from_dict(dna_data, output_path)
                    print("\n" + "=" * 60)
                    print("  Phase 1 complete! Your MessageDNA is ready.")
                    print(f"  File: {output_path}")
                    print("=" * 60)
                    interview_done = True
                else:
                    print("\n⚠️  Interview finished but JSON could not be extracted.")
                    print("   The raw response has been printed above.")
                    print("   Please copy the JSON manually and save it.")

        except KeyboardInterrupt:
            print("\n\nInterview interrupted. Run again to restart.")
            break


# ── Entry point ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run the Social Spark Studio MessageDNA interview."
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_PATH,
        help=f"Path to save the MessageDNA JSON file (default: {DEFAULT_OUTPUT_PATH})",
    )
    args = parser.parse_args()

    # Check that an API key is available before starting
    has_api_key = bool(os.getenv("GOOGLE_API_KEY"))
    has_project = bool(os.getenv("GOOGLE_CLOUD_PROJECT"))

    if not has_api_key and not has_project:
        print("\n❌  No Google credentials found.")
        print("\nYou need one of these:")
        print("  Option A (easiest for local testing):")
        print("    export GOOGLE_API_KEY=your_key_here")
        print("    Get a key at: https://aistudio.google.com/app/apikey")
        print()
        print("  Option B (for Google Cloud):")
        print("    export GOOGLE_CLOUD_PROJECT=your-project-id")
        print("    gcloud auth application-default login")
        print()
        sys.exit(1)

    run_interview(output_path=args.output)


if __name__ == "__main__":
    main()
