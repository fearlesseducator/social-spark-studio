"""
run_captions.py

Phase 5 runner — Post Draft Builder.

This script:
    1. Loads MessageDNA, CampaignBrief, and Moments (all must exist)
    2. Builds a combined context block from all three sources
    3. Sends context to caption_agent → up to 15 post drafts
    4. Sends post drafts to hashtag_agent → 3-tier hashtags per post
    5. Merges hashtags into drafts and displays a preview
    6. Saves complete post drafts to data/posts_output.json

Content mix per run (up to 15 total):
    video_clip  — up to 6  (from transcript moments)
    image_post  — up to 7  (from MessageDNA pillars + visual direction)
    text_quote  — up to 2  (from transcript language or MessageDNA beliefs)

Architecture rule: MESSAGE BEFORE MEDIA.
    Hard-stops if MessageDNA, CampaignBrief, or moments_output.json
    are missing.

Note:
    CSV export does not run in this phase.
    Image generation does not run in this phase.
    MP4 clip extraction does not run in this phase.
    This phase prepares asset fields (image_url blank, video_url as
    YouTube timestamp) so the asset phase can fill them in.

Usage:
    python run_captions.py
    python run_captions.py --moments data/moments_output.json
    python run_captions.py --dna data/my_dna.json --brief data/my_brief.json
    CAPTION_AGENT_MODEL=gemini-2.5-flash python run_captions.py
"""

import argparse
import json
import os
import re
import sys
import time
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

from agents.caption_agent import create_caption_agent
from agents.hashtag_agent import create_hashtag_agent
from models.post_draft import (
    PostDraft,
    PostDraftSet,
    save_post_drafts,
)
from models.transcript_moment import load_moments

# ── Constants ─────────────────────────────────────────────────────────

APP_NAME = "social_spark_studio"
USER_ID = "local_founder"
DEFAULT_DNA_PATH = "data/message_dna_output.json"
DEFAULT_BRIEF_PATH = "data/campaign_brief.json"
DEFAULT_MOMENTS_PATH = "data/moments_output.json"
DEFAULT_POSTS_OUTPUT = "data/posts_output.json"

CAPTION_MARKER_START = "<posts_complete>"
CAPTION_MARKER_END = "</posts_complete>"
HASHTAG_MARKER_START = "<hashtags_complete>"
HASHTAG_MARKER_END = "</hashtags_complete>"


# ── Prerequisites ─────────────────────────────────────────────────────

def check_prerequisites(dna_path: str, brief_path: str, moments_path: str) -> None:
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
    if not Path(moments_path).exists():
        missing.append(
            f"  Moments not found:       {moments_path}\n"
            f"  → Run: python run_moments.py"
        )
    if missing:
        print("\n❌  Prerequisites missing — cannot proceed.\n")
        for m in missing:
            print(m)
        sys.exit(1)


# ── Context builder ───────────────────────────────────────────────────

def build_caption_context(
    dna_path: str,
    brief_path: str,
    moments_path: str,
) -> tuple[str, str, str]:
    """
    Build the full context string sent to the caption_agent.

    Includes: MessageDNA (voice + visual direction), CampaignBrief,
    and all selected transcript moments.

    Returns:
        (context_text, campaign_id, primary_cta)
    """
    with open(dna_path, "r", encoding="utf-8") as f:
        dna = json.load(f)
    with open(brief_path, "r", encoding="utf-8") as f:
        brief = json.load(f)

    moments_result = load_moments(moments_path)
    campaign_id = brief.get("campaign_id", "")
    primary_cta = brief.get("primary_cta", "")

    # ── MessageDNA voice block ────────────────────────────────────
    fi = dna.get("founder_identity", {})
    ap = dna.get("audience_profile", {})
    vp = dna.get("voice_profile", {})
    cd = dna.get("content_direction", {})
    fp = dna.get("founder_positioning", {})

    use_text = "\n".join(f'  - "{p}"' for p in vp.get("phrases_to_use", []))
    avoid_text = "\n".join(f'  - "{p}"' for p in vp.get("phrases_to_avoid", []))
    pillars_text = "\n".join(f"  - {p}" for p in cd.get("content_pillars", []))
    metaphors_text = "\n".join(f"  - {m}" for m in cd.get("visual_metaphors", []))
    visual_avoid_text = "\n".join(f"  - {v}" for v in cd.get("visual_avoid_list", []))
    beliefs_text = "\n".join(f'  - "{b}"' for b in fp.get("signature_beliefs", []))

    dna_block = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FOUNDER MESSAGEDNA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Founder: {fi.get('founder_name', '')} | Brand: {fi.get('brand_name', '')}
Known for: {fi.get('known_for', '')}
Ideal audience: {ap.get('ideal_audience', '')}
Core problem solved: {ap.get('core_problem_solved', '')}

Contrarian belief: {fp.get('contrarian_belief', '')}

Signature beliefs:
{beliefs_text}

Brand voice words: {', '.join(vp.get('brand_voice_words', []))}
Tone rules: {vp.get('tone_rules', '')}
Teaching style: {vp.get('teaching_style', '')}
CTA style: {vp.get('cta_style', '')}

Phrases to USE (work these in naturally):
{use_text}

Phrases to AVOID (never use these — check every caption):
{avoid_text}

Content pillars (copy names exactly):
{pillars_text}

Visual metaphors (use for image_post prompts):
{metaphors_text}

Visual avoid list (never describe these in image_post prompts):
{visual_avoid_text}

Credibility markers (use these in captions where they fit naturally):
{chr(10).join(f'  - {m}' for m in cd.get('credibility_markers', []))}
"""

    # ── CampaignBrief block ───────────────────────────────────────
    platforms = brief.get("target_platforms", [])
    brief_block = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CAMPAIGN BRIEF
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Campaign goal: {brief.get('campaign_goal', '')}
Selected offer: {brief.get('selected_offer', '')}
Primary CTA: {primary_cta}
Target platforms: {', '.join(platforms)}
Campaign theme: {brief.get('campaign_theme', '')}
Specific audience segment: {brief.get('specific_audience_segment', '') or '(full audience)'}
Timely context: {brief.get('timely_context', '') or '(none)'}
Campaign ID: {campaign_id}
"""

    # ── Transcript moments block ──────────────────────────────────
    moment_lines = []
    for m in moments_result.moments:
        ts = (
            f"[{m.start_timestamp} → {m.end_timestamp}]"
            if m.start_timestamp and m.start_timestamp != "manual"
            else "[manual — no timestamps]"
        )
        moment_lines.append(f"""
MOMENT {m.moment_index} {ts}
Platform: {m.platform_recommendation}
Content pillar: {m.content_pillar}
Why social-worthy: {m.why_social_worthy}
Hook idea: {m.hook_idea}
Positioning angle: {m.positioning_angle}
Clip URL: {m.clip_url or '(manual — no clip URL)'}
Quote (verbatim — use exactly as written):
\"\"\"{m.quote}\"\"\"
""")

    moments_block = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TRANSCRIPT MOMENTS — {moments_result.total_moments} selected moments available
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{''.join(moment_lines)}"""

    context = (
        "Generate up to 15 social media post drafts for this campaign.\n"
        "Content mix: up to 6 video_clip posts (from transcript moments), "
        "up to 7 image_post posts (from MessageDNA pillars and visual direction), "
        "up to 2 text_quote posts (from strong transcript language or MessageDNA beliefs).\n\n"
        + dna_block
        + brief_block
        + moments_block
    )

    return context, campaign_id, primary_cta


def build_batch_context(
    dna_path: str,
    brief_path: str,
    moments_path: str,
    batch: str,
) -> tuple[str, str, str]:
    """
    Build a narrowed context for a single content-type batch.

    batch must be one of: "video_clip", "image_post", "text_quote".
    Reduces the generation request so the model handles a smaller task,
    which is less likely to time out or overload during high demand.
    """
    BATCH_INSTRUCTIONS = {
        "video_clip": (
            "Generate ONLY video_clip posts (up to 6). "
            "Each must be built from a transcript moment with a verbatim quote. "
            "Do not generate image_post or text_quote posts in this batch."
        ),
        "image_post": (
            "Generate ONLY image_post posts (up to 7). "
            "Each must be built from MessageDNA pillars and visual metaphors. "
            "Include a detailed image_prompt for every post. "
            "Do not generate video_clip or text_quote posts in this batch."
        ),
        "text_quote": (
            "Generate ONLY text_quote posts (up to 2). "
            "Use compelling single sentences from the transcript or MessageDNA beliefs. "
            "Do not generate video_clip or image_post posts in this batch."
        ),
    }

    if batch not in BATCH_INSTRUCTIONS:
        raise ValueError(f"Invalid batch type: {batch!r}. "
                         f"Must be one of: {list(BATCH_INSTRUCTIONS)}")

    full_context, campaign_id, primary_cta = build_caption_context(
        dna_path, brief_path, moments_path
    )

    # Replace the opening instruction line with the narrowed batch instruction
    batch_header = (
        f"BATCH MODE — {batch.upper()} only.\n"
        f"{BATCH_INSTRUCTIONS[batch]}\n\n"
    )
    context = batch_header + full_context.split("\n\n", 1)[-1]

    return context, campaign_id, primary_cta


def build_hashtag_context(
    draft_set: PostDraftSet,
    dna_path: str,
    brief_path: str,
) -> str:
    """
    Build the context string sent to the hashtag_agent.
    Includes all post summaries so the agent can assign relevant tags.
    """
    with open(dna_path, "r", encoding="utf-8") as f:
        dna = json.load(f)
    with open(brief_path, "r", encoding="utf-8") as f:
        brief = json.load(f)

    ap = dna.get("audience_profile", {})
    cd = dna.get("content_direction", {})

    header = f"""
Assign 3-tier hashtags to each post below.
Ideal audience: {ap.get('ideal_audience', '')}
Campaign theme: {brief.get('campaign_theme', '')}
Content pillars: {', '.join(cd.get('content_pillars', []))}

"""
    post_lines = []
    for p in draft_set.posts:
        post_lines.append(
            f"POST {p.post_number} | {p.content_type} | {p.platform} | "
            f"Pillar: {p.content_pillar}\n"
            f"Caption preview: {p.caption[:150]}...\n"
        )

    return header + "\n".join(post_lines)


# ── Response extraction ───────────────────────────────────────────────

def extract_post_drafts(
    text: str,
    campaign_id: str,
    primary_cta: str,
) -> PostDraftSet | None:
    """Extract the <posts_complete> block and parse into PostDraftSet."""
    pattern = re.compile(
        r"<posts_complete>\s*(.*?)\s*</posts_complete>",
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return None

    try:
        data = json.loads(match.group(1).strip())
    except json.JSONDecodeError as e:
        print(f"\n⚠️  JSON parse error in posts response: {e}")
        print("Raw snippet (first 600 chars):")
        print(match.group(1)[:600])
        return None

    if not data.get("campaign_id"):
        data["campaign_id"] = campaign_id

    # Backfill call_to_action if agent left it blank
    for p in data.get("posts", []):
        if not p.get("call_to_action") and primary_cta:
            p["call_to_action"] = primary_cta

    return PostDraftSet.from_dict(data)


def extract_hashtags(text: str) -> dict | None:
    """
    Extract the <hashtags_complete> block.
    Returns a dict mapping post_number (int) → {tier1, tier2, tier3}.
    """
    pattern = re.compile(
        r"<hashtags_complete>\s*(.*?)\s*</hashtags_complete>",
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return None

    try:
        data = json.loads(match.group(1).strip())
    except json.JSONDecodeError as e:
        print(f"\n⚠️  JSON parse error in hashtags response: {e}")
        return None

    result = {}
    for entry in data.get("hashtags", []):
        pn = entry.get("post_number")
        if pn is not None:
            result[int(pn)] = {
                "tier1": entry.get("hashtags_tier1", []),
                "tier2": entry.get("hashtags_tier2", []),
                "tier3": entry.get("hashtags_tier3", []),
            }
    return result


def merge_hashtags(draft_set: PostDraftSet, hashtag_map: dict) -> None:
    """Merge hashtag results into the draft set in-place."""
    for post in draft_set.posts:
        tags = hashtag_map.get(post.post_number)
        if tags:
            post.hashtags_tier1 = tags.get("tier1", [])
            post.hashtags_tier2 = tags.get("tier2", [])
            post.hashtags_tier3 = tags.get("tier3", [])


# ── ADK helpers ───────────────────────────────────────────────────────

RETRY_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 10

# Strings that indicate a transient server-overload error.
# Covers: HTTP 503, google.genai ServerError, google-api-core ServiceUnavailable,
# and informal messages the model layer sometimes emits.
_503_PHRASES = (
    "503",
    "UNAVAILABLE",
    "overloaded",
    "high demand",
    "Service Unavailable",
    "ServiceUnavailable",
    "ServerError",
    "server error",
    "try again",
    "quota",          # sometimes quota exhaustion looks like overload
)

# Try to import the concrete Google error types so we can catch them
# explicitly — the string on these objects can differ from str(exc).
try:
    from google.genai.errors import ServerError as _GenaiServerError
except ImportError:
    _GenaiServerError = None  # type: ignore

try:
    from google.api_core.exceptions import ServiceUnavailable as _ApiCoreUnavailable
except ImportError:
    _ApiCoreUnavailable = None  # type: ignore


def _is_503(exc: Exception) -> bool:
    """
    Return True if the exception represents a transient server-overload.

    Checks both the concrete type (when importable) and the string
    representation, because the ADK wraps errors and the stringified form
    varies across SDK versions.
    """
    if _GenaiServerError is not None and isinstance(exc, _GenaiServerError):
        return True
    if _ApiCoreUnavailable is not None and isinstance(exc, _ApiCoreUnavailable):
        return True
    # Also check any chained cause
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if cause is not None:
        if _GenaiServerError is not None and isinstance(cause, _GenaiServerError):
            return True
        if _ApiCoreUnavailable is not None and isinstance(cause, _ApiCoreUnavailable):
            return True
    # Fall back to string matching on the full repr
    full_msg = f"{type(exc).__name__} {exc!s} {repr(exc)}"
    return any(phrase.lower() in full_msg.lower() for phrase in _503_PHRASES)


def get_agent_text(events) -> str:
    """
    Consume the ADK event stream and concatenate all text parts.

    NOTE: runner.run() returns a lazy generator. The actual HTTP call to
    the model fires here during iteration — so this is where 503 errors
    surface. Keep this call inside the retry try/except block.
    """
    full_text = ""
    for event in events:
        if hasattr(event, "content") and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    full_text += part.text
    return full_text


def run_agent_once(agent, context: str, label: str = "agent") -> str:
    """
    Run an agent with a single message and return the full text response.

    Wraps the ENTIRE ADK call — session creation, runner.run(), AND
    get_agent_text() — inside the retry loop, because runner.run() returns
    a lazy generator and the real HTTP call happens during iteration inside
    get_agent_text().

    Retries up to RETRY_ATTEMPTS times on 503 / overload errors,
    waiting RETRY_WAIT_SECONDS between each attempt.
    On final failure, prints a clean message and raises SystemExit.
    """
    last_exc: Exception | None = None

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
            session_id = f"phase5_{uuid.uuid4().hex[:8]}"
            runner.session_service.create_session_sync(
                app_name=APP_NAME,
                user_id=USER_ID,
                session_id=session_id,
            )
            # runner.run() is lazy — get_agent_text() triggers the HTTP call.
            # Both lines must stay inside this try block.
            events = runner.run(
                user_id=USER_ID,
                session_id=session_id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part(text=context)],
                ),
            )
            return get_agent_text(events)  # ← HTTP call fires here

        except Exception as exc:
            if _is_503(exc):
                last_exc = exc
                if attempt < RETRY_ATTEMPTS:
                    print(
                        f"\n⚠️  Google model is temporarily overloaded. "
                        f"Waiting {RETRY_WAIT_SECONDS}s before retrying... "
                        f"Attempt {attempt} of {RETRY_ATTEMPTS}."
                    )
                    time.sleep(RETRY_WAIT_SECONDS)
                    # continue to next attempt
                else:
                    print(
                        f"\n❌  Google model is temporarily overloaded after "
                        f"{RETRY_ATTEMPTS} attempts ({label}).\n"
                        "    Please retry in a few minutes, or run smaller batches:\n"
                        "      python run_captions.py --batch video_clip\n"
                        "      python run_captions.py --batch image_post\n"
                        "      python run_captions.py --batch text_quote\n"
                        f"\n    Error detail: {type(exc).__name__}: {exc}"
                    )
                    sys.exit(1)
            else:
                # Not a 503 — surface immediately with full detail
                print(f"\n❌  {label} failed with unexpected error.")
                print(f"    {type(exc).__name__}: {exc}")
                raise


# ── Hashtags-only mode ───────────────────────────────────────────────

def run_hashtags_only(
    output_path: str,
    dna_path: str,
    brief_path: str,
) -> None:
    """
    Restore hashtag_tier1/2/3 on existing post drafts without touching anything else.

    Loads the current posts_output.json, runs only the hashtag_agent,
    merges tier hashtags back into each post, and saves.

    Does NOT call caption_agent.
    Does NOT change captions, image_prompts, video_url, asset_status,
    post count, or any other field.
    """
    print("\n" + "=" * 60)
    print("  Social Spark Studio — Phase 5: Hashtags Only")
    print("=" * 60)

    # Prerequisites: existing posts file + dna + brief (for context)
    missing = []
    if not Path(output_path).exists():
        missing.append(f"  Posts file not found: {output_path}\n"
                       f"  → Run full Phase 5 first: python run_captions.py")
    if not Path(dna_path).exists():
        missing.append(f"  MessageDNA not found: {dna_path}")
    if not Path(brief_path).exists():
        missing.append(f"  CampaignBrief not found: {brief_path}")
    if missing:
        print("\n❌  Prerequisites missing:\n")
        for m in missing:
            print(m)
        sys.exit(1)

    from models.post_draft import load_post_draft_set, save_post_drafts
    draft_set = load_post_draft_set(output_path)

    print(f"\n   {draft_set.total_posts} existing posts loaded — captions will not change")
    print(f"   Running hashtag_agent to assign 3-tier hashtags...\n")

    hashtag_agent = create_hashtag_agent()
    hashtag_context = build_hashtag_context(draft_set, dna_path, brief_path)
    hashtag_response = run_agent_once(
        hashtag_agent, hashtag_context, label="hashtag_agent"
    )

    hashtag_map = extract_hashtags(hashtag_response)
    if not hashtag_map:
        print("\n⚠️  Hashtag agent did not return a parseable result.")
        print("    Captions unchanged. Posts file not saved.")
        sys.exit(1)

    merge_hashtags(draft_set, hashtag_map)
    assigned = len(hashtag_map)
    print(f"✅ Hashtags assigned to {assigned} of {draft_set.total_posts} posts")

    # Preview
    print("\n" + "-" * 60)
    for post in draft_set.posts:
        all_tags = post.hashtags_tier1 + post.hashtags_tier2 + post.hashtags_tier3
        tag_str = " ".join(all_tags[:5]) + ("..." if len(all_tags) > 5 else "")
        print(f"  Post {post.post_number:02d} | {post.platform:<12} | "
              f"{tag_str or '(no tags returned)'}")
    print("-" * 60)

    print()
    confirm = input(
        f"Save hashtags to {output_path}? Captions will not change. (yes/no): "
    ).strip().lower()

    if confirm not in ("yes", "y"):
        print("\nHashtags not saved.")
        return

    save_post_drafts(draft_set, output_path)

    print("\n" + "=" * 60)
    print("  Hashtags restored!")
    print(f"  Posts file: {output_path}  (hashtags updated, nothing else changed)")
    print("=" * 60)
    print("\nRe-run CSV export to include hashtags:")
    print("  python run_export.py")


# ── Main ──────────────────────────────────────────────────────────────

def run_captions(
    dna_path: str,
    brief_path: str,
    moments_path: str,
    output_path: str,
    batch: str | None = None,
) -> None:
    """
    Full Phase 5 runner.

    batch: if set to "video_clip", "image_post", or "text_quote", generates
    only that content type. Useful when the full 15-post run hits a 503.
    Batch output is merged into an existing posts_output.json if one exists.
    """

    batch_label = f" [{batch.upper()} batch]" if batch else ""
    print("\n" + "=" * 60)
    print(f"  Social Spark Studio — Phase 5: Post Draft Builder{batch_label}")
    print("=" * 60)

    # Step 1: Prerequisites
    check_prerequisites(dna_path, brief_path, moments_path)
    print(f"\n✅ MessageDNA:    {dna_path}")
    print(f"✅ CampaignBrief: {brief_path}")
    print(f"✅ Moments:       {moments_path}")

    moments = load_moments(moments_path)
    print(f"\n   {moments.total_moments} transcript moments available")
    if batch:
        BATCH_TARGETS = {
            "video_clip": "up to 6 video_clip posts",
            "image_post": "up to 7 image_post posts",
            "text_quote": "up to 2 text_quote posts",
        }
        print(f"   Batch target: {BATCH_TARGETS.get(batch, batch)}")
    else:
        print("   Target: up to 15 post drafts "
              "(6 video_clip + 7 image_post + 2 text_quote)")

    # Step 2: Build context
    print("\nBuilding context from MessageDNA + CampaignBrief + Moments...")
    if batch:
        caption_context, campaign_id, primary_cta = build_batch_context(
            dna_path, brief_path, moments_path, batch
        )
    else:
        caption_context, campaign_id, primary_cta = build_caption_context(
            dna_path, brief_path, moments_path
        )

    # Step 3: Run caption_agent
    caption_agent = create_caption_agent()
    print(f"\nRunning caption_agent ({caption_agent.model})...")
    print("Generating post drafts in the founder's voice — may take 30–60 seconds.\n")

    caption_response = run_agent_once(caption_agent, caption_context, label="caption_agent")

    draft_set = extract_post_drafts(caption_response, campaign_id, primary_cta)

    if draft_set is None:
        print("\n❌  Could not extract post drafts from agent response.")
        print("Raw response (first 800 chars):")
        print(caption_response[:800])
        sys.exit(1)

    if draft_set.total_posts == 0:
        print("\n⚠️  Agent returned 0 posts.")
        if draft_set.generation_notes:
            print(f"    Notes: {draft_set.generation_notes}")
        sys.exit(1)

    print(f"✅ {draft_set.summary()}")

    # Step 4: Run hashtag_agent
    print(f"\nRunning hashtag_agent to assign 3-tier hashtags...")
    hashtag_agent = create_hashtag_agent()
    hashtag_context = build_hashtag_context(draft_set, dna_path, brief_path)
    hashtag_response = run_agent_once(hashtag_agent, hashtag_context, label="hashtag_agent")

    hashtag_map = extract_hashtags(hashtag_response)
    if hashtag_map:
        merge_hashtags(draft_set, hashtag_map)
        print(f"✅ Hashtags assigned to {len(hashtag_map)} posts")
    else:
        print("⚠️  Hashtag agent did not return a parseable result — hashtags left blank.")

    # Step 5: Preview
    print("\n" + "-" * 60)
    print(f"POST DRAFTS — {draft_set.total_posts} total")
    print(f"  video_clip: {draft_set.video_clip_count}  "
          f"image_post: {draft_set.image_post_count}  "
          f"text_quote: {draft_set.text_quote_count}")
    print("-" * 60)

    if draft_set.generation_notes:
        print(f"\nAgent notes: {draft_set.generation_notes}\n")

    for post in draft_set.posts:
        print(f"\n{post.summary_line()}")
        print(f"  Asset status: {post.asset_status}")
        print(f"  Caption: {post.caption[:200]}{'...' if len(post.caption) > 200 else ''}")
        all_tags = post.hashtags_tier1 + post.hashtags_tier2 + post.hashtags_tier3
        if all_tags:
            print(f"  Tags: {' '.join(all_tags[:6])}{'...' if len(all_tags) > 6 else ''}")

    print()
    confirm = input(
        f"Save these {draft_set.total_posts} post drafts and continue? (yes/no): "
    ).strip().lower()

    if confirm not in ("yes", "y"):
        print("\nPost drafts not saved. Run again to retry.")
        return

    # Step 6: Merge batch into existing file (if batch mode and file exists)
    if batch and Path(output_path).exists():
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
            # Validate it is a real posts file before merging
            if "posts" in existing_data:
                from models.post_draft import PostDraftSet as _PDS
                existing_set = _PDS.from_dict(existing_data)
                existing_numbers = {p.post_number for p in existing_set.posts}
                # Renumber new posts to avoid collisions
                next_number = max(existing_numbers, default=0) + 1
                for p in draft_set.posts:
                    p.post_number = next_number
                    next_number += 1
                existing_set.posts.extend(draft_set.posts)
                # Recompute totals
                from models.post_draft import CONTENT_VIDEO_CLIP, CONTENT_IMAGE_POST, CONTENT_TEXT_QUOTE
                existing_set.total_posts = len(existing_set.posts)
                existing_set.video_clip_count = sum(
                    1 for p in existing_set.posts if p.content_type == CONTENT_VIDEO_CLIP
                )
                existing_set.image_post_count = sum(
                    1 for p in existing_set.posts if p.content_type == CONTENT_IMAGE_POST
                )
                existing_set.text_quote_count = sum(
                    1 for p in existing_set.posts if p.content_type == CONTENT_TEXT_QUOTE
                )
                draft_set = existing_set
                print(f"\n   Merged with existing file → {draft_set.summary()}")
        except (json.JSONDecodeError, KeyError) as merge_err:
            print(f"\n⚠️  Could not merge with existing file ({merge_err}). "
                  f"Saving batch output as new file.")

    # Step 7: Save
    save_post_drafts(draft_set, output_path)

    print("\n" + "=" * 60)
    print("  Phase 5 complete!")
    print(f"  MessageDNA:    {dna_path}  (unchanged)")
    print(f"  CampaignBrief: {brief_path}  (unchanged)")
    print(f"  Moments:       {moments_path}  (unchanged)")
    print(f"  Post drafts:   {output_path}  (new)")
    print(f"\n  {draft_set.summary()}")
    print("=" * 60)
    print("\nNext step (Phase 6A): Run the image generation phase.")
    print("  python run_images.py")


# ── Entry point ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Phase 5: Generate up to 15 post drafts from transcript moments + MessageDNA."
    )
    parser.add_argument("--dna", default=DEFAULT_DNA_PATH,
                        help=f"MessageDNA JSON path (default: {DEFAULT_DNA_PATH})")
    parser.add_argument("--brief", default=DEFAULT_BRIEF_PATH,
                        help=f"CampaignBrief JSON path (default: {DEFAULT_BRIEF_PATH})")
    parser.add_argument("--moments", default=DEFAULT_MOMENTS_PATH,
                        help=f"Moments JSON path (default: {DEFAULT_MOMENTS_PATH})")
    parser.add_argument("--output", default=DEFAULT_POSTS_OUTPUT,
                        help=f"Output path (default: {DEFAULT_POSTS_OUTPUT})")
    parser.add_argument(
        "--batch",
        choices=["video_clip", "image_post", "text_quote"],
        default=None,
        help=(
            "Generate only one content type instead of all 15 posts. "
            "Use when the full run hits a 503 overload error. "
            "Run once per batch type; results are merged into the output file. "
            "Choices: video_clip, image_post, text_quote"
        ),
    )
    parser.add_argument(
        "--fallback-model",
        dest="fallback_model",
        default=None,
        metavar="MODEL",
        help=(
            "Override the caption agent model. Use when gemini-2.5-flash is "
            "overloaded. Example: --fallback-model gemini-2.5-flash-lite"
        ),
    )
    parser.add_argument(
        "--hashtags-only",
        dest="hashtags_only",
        action="store_true",
        help=(
            "Restore hashtags on existing posts without regenerating captions. "
            "Loads posts_output.json, runs only hashtag_agent, saves tier hashtags back. "
            "Requires --dna and --brief. Does not use --moments or --batch."
        ),
    )
    args = parser.parse_args()

    if args.hashtags_only:
        if not os.getenv("GOOGLE_API_KEY") and not os.getenv("GOOGLE_CLOUD_PROJECT"):
            print("\n❌  No Google credentials found.")
            print("  Add GOOGLE_API_KEY=... to your .env file")
            sys.exit(1)
        run_hashtags_only(
            output_path=args.output,
            dna_path=args.dna,
            brief_path=args.brief,
        )
        return

    if args.fallback_model:
        os.environ["CAPTION_AGENT_MODEL"] = args.fallback_model
        print(f"\n   Using fallback model: {args.fallback_model}")

    if not os.getenv("GOOGLE_API_KEY") and not os.getenv("GOOGLE_CLOUD_PROJECT"):
        print("\n❌  No Google credentials found.")
        print("  Add GOOGLE_API_KEY=... to your .env file")
        sys.exit(1)

    run_captions(
        dna_path=args.dna,
        brief_path=args.brief,
        moments_path=args.moments,
        output_path=args.output,
        batch=args.batch,
    )


if __name__ == "__main__":
    main()
