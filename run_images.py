"""
run_images.py

Phase 6A -- Image Generation.

Reads posts_output.json, generates images for all posts where
asset_status == "pending_image", saves PNGs to data/generated_images/,
and writes updated posts_output.json.

No LlmAgent is used. This is a direct Imagen API call.

Usage:
    python run_images.py              # process all pending_image posts
    python run_images.py --dry-run    # print prompts, no API calls
    python run_images.py --post 2     # regenerate one specific post by number

Environment:
    GOOGLE_API_KEY        -- Gemini Developer API (local testing)
    GOOGLE_CLOUD_PROJECT  -- Vertex AI project (production)
    GOOGLE_CLOUD_LOCATION -- Vertex AI location (default: us-central1)
    IMAGEN_MODEL          -- override model (default: imagen-4.0-generate-001)
"""

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))

from models.post_draft import (
    PostDraftSet,
    PostDraft,
    AssetStatus,
    load_post_draft_set,
    save_post_draft_set,
)
from tools.imagen_tool import generate_image

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_POSTS_PATH   = "data/posts_output.json"
DEFAULT_IMAGES_DIR   = "data/generated_images"

# Imagen model strings (GOOGLE_API_KEY / Gemini Developer API):
#
#   imagen-4.0-generate-001       best quality          (default)
#   imagen-4.0-fast-generate-001  faster, lower cost    (use for retries / quota issues)
#
# Override at runtime:
#   python run_images.py --model imagen-4.0-generate-001
#   python run_images.py --model imagen-4.0-fast-generate-001
#   IMAGEN_MODEL=imagen-4.0-fast-generate-001 python run_images.py

DEFAULT_IMAGEN_MODEL = "imagen-4.0-generate-001"
RETRY_LIMIT          = 3
RETRY_WAIT_SECONDS   = 10


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

def check_prerequisites(posts_path: str) -> None:
    if not Path(posts_path).exists():
        print(f"\n  posts_output.json not found: {posts_path}")
        print("  MESSAGE BEFORE MEDIA -- run Phase 5 first.")
        print("  python run_captions.py")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Image prompt sanitisation
# ---------------------------------------------------------------------------

def sanitise_prompt(prompt: str) -> str:
    """
    Append a no-text instruction to every prompt.
    Imagen cannot reliably render readable text, equations, or labels.
    """
    if not prompt:
        return prompt
    return (
        prompt.rstrip()
        + " No text, words, labels, equations, arrows, or written "
        "characters of any kind in the image. Clean visual composition only."
    )


# ---------------------------------------------------------------------------
# Aspect ratio
# ---------------------------------------------------------------------------

def aspect_ratio_for_platform(platform: str) -> str:
    mapping = {
        "LinkedIn":  "16:9",
        "Instagram": "1:1",
        "Twitter/X": "16:9",
        "Twitter":   "16:9",
        "Facebook":  "16:9",
    }
    return mapping.get(platform, "1:1")


# ---------------------------------------------------------------------------
# Generate one post
# ---------------------------------------------------------------------------

def generate_for_post(
    post: PostDraft,
    images_dir: str,
    model: str,
) -> None:
    """Generate an image for one post. Updates the post object in place."""

    if not post.image_prompt:
        print(f"  Post {post.post_number}: no image_prompt -- skipping")
        post.asset_status = AssetStatus.IMAGE_GENERATION_FAILED
        post.quality_notes = (
            (post.quality_notes + " | " if post.quality_notes else "")
            + "No image_prompt. Manual image required."
        )
        return

    prompt      = sanitise_prompt(post.image_prompt)
    aspect      = aspect_ratio_for_platform(post.platform)
    output_path = str(Path(images_dir) / f"post_{post.post_number:03d}.png")

    print(f"\n  Post {post.post_number} | {post.platform} | {post.content_pillar[:50]}")
    print(f"  Prompt : {prompt[:90]}...")
    print(f"  Output : {output_path}")

    last_error = ""
    for attempt in range(1, RETRY_LIMIT + 1):
        if attempt > 1:
            print(f"  Retry {attempt}/{RETRY_LIMIT} -- waiting {RETRY_WAIT_SECONDS}s...")
            time.sleep(RETRY_WAIT_SECONDS)

        result = generate_image(
            prompt=prompt,
            aspect_ratio=aspect,
            output_path=output_path,
            post_index=post.post_number,
            model=model,
        )

        if result.success:
            post.image_url             = result.image_url
            post.image_storage_status  = "local_only"
            post.asset_status          = AssetStatus.IMAGE_GENERATED
            print(f"  Saved : {output_path}")
            return

        last_error = result.error_message

        # Classify the error to decide whether to retry and what to record.
        is_404 = "404" in last_error or "not found" in last_error.lower()
        is_429 = "429" in last_error or "resource_exhausted" in last_error.lower() or "resource exhausted" in last_error.lower()
        is_transient = any(s in last_error.lower() for s in [
            "503", "unavailable", "overload", "high demand",
            "serviceunavailable", "server error", "try again",
        ])

        if is_404:
            # Wrong model name or API version. Retrying will not help.
            print(f"  FAILED (model_config_error) : {last_error[:80]}")
            post.asset_status  = AssetStatus.IMAGE_GENERATION_FAILED
            post.image_url     = ""
            prefix = post.quality_notes + " | " if post.quality_notes else ""
            post.quality_notes = (
                prefix
                + f"model_config_error: model '{model}' not found. "
                + "Check --model flag or IMAGEN_MODEL env var. "
                + "Try: --model imagen-4.0-generate-001 or --model imagen-4.0-fast-generate-001"
            )
            return

        if is_429:
            # Quota exhausted. Retrying immediately burns more quota.
            print(f"  FAILED (quota_exhausted) : {last_error[:80]}")
            post.asset_status  = AssetStatus.IMAGE_GENERATION_FAILED
            post.image_url     = ""
            prefix = post.quality_notes + " | " if post.quality_notes else ""
            post.quality_notes = (
                prefix
                + "quota_exhausted: Imagen quota limit hit. "
                + "Wait a few minutes then retry with: python run_images.py --post "
                + str(post.post_number)
            )
            return

        if not is_transient:
            # Unknown non-retryable error.
            break

        print(f"  Overloaded ({last_error[:60]}) -- will retry")

    # All retry attempts exhausted on a transient error.
    post.asset_status  = AssetStatus.IMAGE_GENERATION_FAILED
    post.image_url     = ""
    prefix = post.quality_notes + " | " if post.quality_notes else ""
    post.quality_notes = (
        prefix
        + f"Image generation failed after {RETRY_LIMIT} attempts (transient error). "
        + "Retry with: python run_images.py --post " + str(post.post_number)
    )
    print(f"  FAILED : {last_error[:80]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_images(
    posts_path: str,
    images_dir: str,
    model: str,
    dry_run: bool,
    target_post,
) -> None:

    print("\n" + "=" * 60)
    print("  Social Spark Studio -- Phase 6A: Image Generation")
    print("=" * 60)

    check_prerequisites(posts_path)

    draft_set = load_post_draft_set(posts_path)
    print(f"\n{draft_set.summary()}")

    # Determine which posts to process
    if target_post is not None:
        posts_to_process = [
            p for p in draft_set.posts if p.post_number == target_post
        ]
        if not posts_to_process:
            print(f"\n  No post with post_number={target_post} found.")
            print(f"  Available post numbers: {[p.post_number for p in draft_set.posts]}")
            sys.exit(1)
        print(f"\nRegenerating Post {target_post} only.")
    else:
        posts_to_process = draft_set.pending_image_posts()

    if not posts_to_process:
        print("\n  No pending_image posts found. Nothing to do.")
        print("  All image_post drafts already have asset_status = image_generated.")
        return

    # Dry run -- print prompts only
    if dry_run:
        print(f"\n-- Dry run: {len(posts_to_process)} post(s) would generate images --")
        for post in posts_to_process:
            print(f"\n  Post {post.post_number} | {post.platform}")
            print(f"  Pillar : {post.content_pillar[:60]}")
            print(f"  Prompt : {post.image_prompt[:120]}")
            print(f"  Output : data/generated_images/post_{post.post_number:03d}.png")
        print("\nRun without --dry-run to generate images.")
        return

    # Create output directory
    Path(images_dir).mkdir(parents=True, exist_ok=True)

    print(f"\n-- Generating {len(posts_to_process)} image(s) --")
    print(f"   Model  : {model}")
    print(f"   Output : {images_dir}/")

    for post in posts_to_process:
        generate_for_post(post, images_dir, model)

    # Save updated posts_output.json
    save_post_draft_set(draft_set, posts_path)

    # Summary
    generated = [p for p in posts_to_process
                 if p.asset_status == AssetStatus.IMAGE_GENERATED]
    failed    = [p for p in posts_to_process
                 if p.asset_status == AssetStatus.IMAGE_GENERATION_FAILED]

    print("\n" + "=" * 60)
    print("  Phase 6A complete!")
    print(f"  {len(generated)} image(s) generated  -->  {images_dir}/")
    if failed:
        print(f"  {len(failed)} failed --> post(s): {[p.post_number for p in failed]}")
        print("  Re-run with --post N to retry a specific post.")
    print(f"  posts_output.json updated: {posts_path}")
    print("=" * 60)
    print("\nNext: inspect data/generated_images/")
    print("Then run Phase 7: python run_export.py")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 6A: Generate images for pending_image posts."
    )
    parser.add_argument(
        "--posts", default=DEFAULT_POSTS_PATH,
        help=f"Path to posts_output.json (default: {DEFAULT_POSTS_PATH})",
    )
    parser.add_argument(
        "--images-dir", default=DEFAULT_IMAGES_DIR,
        help=f"Directory for saved images (default: {DEFAULT_IMAGES_DIR})",
    )
    parser.add_argument(
        "--model", default=os.getenv("IMAGEN_MODEL", DEFAULT_IMAGEN_MODEL),
        help=f"Imagen model (default: {DEFAULT_IMAGEN_MODEL})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print image prompts without calling the API",
    )
    parser.add_argument(
        "--post", type=int, default=None, metavar="N",
        help="Regenerate one specific post by post_number",
    )
    args = parser.parse_args()

    if not os.getenv("GOOGLE_API_KEY") and not os.getenv("GOOGLE_CLOUD_PROJECT"):
        print("\n  No Google credentials found.")
        print("  Add GOOGLE_API_KEY=your_key to your .env file.")
        print("  Get a free key at: https://aistudio.google.com/app/apikey")
        sys.exit(1)

    run_images(
        posts_path  = args.posts,
        images_dir  = args.images_dir,
        model       = args.model,
        dry_run     = args.dry_run,
        target_post = args.post,
    )


if __name__ == "__main__":
    main()
