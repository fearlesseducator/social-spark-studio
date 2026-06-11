"""
run_export.py

Phase 7 -- CSV Export.

Reads posts_output.json and writes a scheduler-ready CSV file using
the exact column format from the SparkCircle CSV import sample.

CSV columns (exact names -- do not change):
    postAtSpecificTime    YYYY-MM-DD HH:MM:SS or blank
    content +Hashtags     caption + blank line + hashtags
    link (OGmetaUrl)      CTA link or blank
    imageUrls             local image path or Cloud Storage URL or blank
    gifUrl                always blank
    videoUrls             YouTube timestamp URL or hosted clip URL or blank

Rules:
    - All posts are exported by default (video_clip, image_post, text_quote)
    - Posts with asset_status == image_generation_failed are included with
      a blank imageUrls and a warning printed to the terminal
    - gifUrl is always blank -- GIF generation is not in scope
    - postAtSpecificTime is blank unless the founder has set scheduled_date
    - Local image file paths are written as-is for local testing
      (swap for Cloud Storage URLs in production)
    - No post is silently dropped -- every post from posts_output.json
      appears in the CSV

Usage:
    python run_export.py
    python run_export.py --posts data/posts_output.json
    python run_export.py --output data/my_campaign_export.csv
    python run_export.py --approved-only
"""

import argparse
import csv
import os
import re
import sys
from datetime import datetime
from pathlib import Path

_URL_RE = re.compile(r"https?://[^\s]+")

def _extract_url(text: str) -> str:
    """Pull the first URL from a string, stripping trailing punctuation."""
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(".,;)\"'") if m else ""

from dotenv import load_dotenv
load_dotenv()

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))

from models.post_draft import (
    PostDraft,
    PostDraftSet,
    AssetStatus,
    load_post_draft_set,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_POSTS_PATH  = "data/posts_output.json"
DEFAULT_OUTPUT_DIR  = "data"

# Exact column names from the SparkCircle CSV import sample.
# Order matters -- do not change.
CSV_COLUMNS = [
    "postAtSpecificTime",
    "content +Hashtags",
    "link (OGmetaUrl)",
    "imageUrls",
    "gifUrl",
    "videoUrls",
]


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

def check_prerequisites(posts_path: str) -> None:
    if not Path(posts_path).exists():
        print(f"\n  posts_output.json not found: {posts_path}")
        print("  Run Phase 5 first: python run_captions.py")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def build_row(post: PostDraft) -> dict:
    """
    Map one PostDraft to one CSV row.

    All fields map directly from PostDraft to the CSV column spec.
    No data is invented -- blank fields stay blank.
    """
    return {
        "postAtSpecificTime": _scheduled_datetime(post),
        "content +Hashtags":  post.content_and_hashtags(),
        "link (OGmetaUrl)":   _extract_url(post.call_to_action),
        "imageUrls":          post.image_url or "",
        "gifUrl":             "",
        "videoUrls":          post.video_url or post.youtube_timestamp_url or "",
    }


def _scheduled_datetime(post: PostDraft) -> str:
    """
    Return scheduled datetime in YYYY-MM-DD HH:MM:SS format if set.
    PostDraft doesn't have scheduled_date/time fields in the alignment
    doc spec -- return blank for now. Founder sets schedule in the tool.
    """
    # If a future phase adds scheduled_date/scheduled_time fields,
    # add the logic here. For now always blank.
    return ""


# ---------------------------------------------------------------------------
# Output filename
# ---------------------------------------------------------------------------

def build_output_path(output_dir: str, campaign_id: str) -> str:
    """
    Build the output CSV filename.
    Format: social-spark-{campaign_id}-{YYYY-MM-DD}.csv
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_id  = campaign_id.replace(" ", "-").replace("/", "-") if campaign_id else "campaign"
    filename = f"social-spark-{safe_id}-{date_str}.csv"
    return str(Path(output_dir) / filename)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_export(
    posts_path: str,
    output_path: str | None,
    approved_only: bool,
    dry_run: bool = False,
) -> None:

    print("\n" + "=" * 60)
    print("  Social Spark Studio -- Phase 7: CSV Export")
    print("=" * 60)

    check_prerequisites(posts_path)

    draft_set = load_post_draft_set(posts_path)
    print(f"\n{draft_set.summary()}")

    # Determine which posts to export
    if approved_only:
        posts = [p for p in draft_set.posts if getattr(p, "status", "") == "approved"]
        print(f"\nApproved-only mode: {len(posts)} of {draft_set.total_posts} posts selected")
    else:
        posts = draft_set.posts
        print(f"\nExporting all {len(posts)} posts")

    if not posts:
        print("\n  No posts to export.")
        if approved_only:
            print("  No posts have status='approved'.")
            print("  Run without --approved-only to export all posts.")
        sys.exit(0)

    # Warn about any failed image posts
    failed_image = [
        p for p in posts
        if p.asset_status == AssetStatus.IMAGE_GENERATION_FAILED
    ]
    if failed_image:
        print(f"\n  Warning: {len(failed_image)} post(s) have image_generation_failed.")
        print(f"  Posts: {[p.post_number for p in failed_image]}")
        print("  imageUrls will be blank for these posts.")
        print("  Re-run: python run_images.py --post N  to retry image generation.")

    # Dry run — print rows without writing
    if dry_run:
        print(f"\n-- Dry run: {len(posts)} row(s) would be written --")
        for post in posts:
            row = build_row(post)
            print(f"\n  Post {post.post_number} | {post.content_type} | {post.platform}")
            print(f"  content : {row['content +Hashtags'][:80]}...")
            print(f"  link    : {row['link (OGmetaUrl)'] or '(blank)'}")
            print(f"  image   : {row['imageUrls'] or '(blank)'}")
            print(f"  video   : {row['videoUrls'] or '(blank)'}")
        print(f"\nRun without --dry-run to write the CSV file.")
        return

    # Build output path
    if not output_path:
        output_path = build_output_path(DEFAULT_OUTPUT_DIR, draft_set.campaign_id)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Write CSV
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=CSV_COLUMNS,
            quoting=csv.QUOTE_ALL,
        )
        writer.writeheader()
        for post in posts:
            writer.writerow(build_row(post))

    # Summary
    has_images  = sum(1 for p in posts if p.image_url)
    has_videos  = sum(1 for p in posts if p.video_url or p.youtube_timestamp_url)
    blank_imgs  = sum(1 for p in posts if not p.image_url and p.content_type == "image_post")

    print(f"\n-- Export summary ──────────────────────────────────────")
    print(f"  Posts exported  : {len(posts)}")
    print(f"  With imageUrls  : {has_images}")
    print(f"  With videoUrls  : {has_videos}")
    if blank_imgs:
        print(f"  Blank imageUrls : {blank_imgs}  (image generation pending or failed)")

    print(f"\n  Output: {output_path}")
    print("\n" + "=" * 60)
    print("  Phase 7 complete!")
    print("=" * 60)
    print(f"\nTo open: open \"{output_path}\"")
    print("Upload this file to your social media scheduler.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 7: Export posts to scheduler-ready CSV."
    )
    parser.add_argument(
        "--posts", default=DEFAULT_POSTS_PATH,
        help=f"Path to posts_output.json (default: {DEFAULT_POSTS_PATH})",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output CSV path (default: data/social-spark-{campaign_id}-{date}.csv)",
    )
    parser.add_argument(
        "--approved-only", action="store_true",
        help="Only export posts with status='approved'",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview CSV rows without writing the file",
    )
    args = parser.parse_args()

    run_export(
        posts_path    = args.posts,
        output_path   = args.output,
        approved_only = args.approved_only,
        dry_run       = args.dry_run,
    )


if __name__ == "__main__":
    main()
