"""
tools/preview_writer.py

Writes a local HTML preview file showing all generated posts with
their images, captions, hashtags, and platform labels.

After Phase 6A runs, open this file in any browser to review all
posts before Phase 7 (CSV export).

Usage:
    from tools.preview_writer import write_preview
    write_preview(caption_result, output_path="data/preview.html")

    # Mac
    open data/preview.html

    # Windows
    start data/preview.html
"""

import base64
import os
from pathlib import Path

from models.social_post import CaptionResult, SocialPost


# ---------------------------------------------------------------------------
# Platform and status colours
# ---------------------------------------------------------------------------

PLATFORM_COLOURS = {
    "LinkedIn": "#0A66C2",
    "Instagram": "#E1306C",
    "Twitter/X": "#000000",
    "Twitter": "#1DA1F2",
    "Facebook": "#1877F2",
}

STATUS_COLOURS = {
    "draft": "#888888",
    "approved": "#22c55e",
    "rejected": "#ef4444",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _image_to_base64(image_path: str) -> str:
    """
    Read a local image file and return it as a base64 data URI.
    Embedding the image in the HTML means the preview file works
    even if the images folder is moved or renamed.
    Returns empty string if the file does not exist.
    """
    if not image_path or not Path(image_path).exists():
        return ""
    try:
        with open(image_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        return f"data:image/png;base64,{data}"
    except Exception:
        return ""


def _badge(text: str, colour: str) -> str:
    return (
        f'<span style="background:{colour};color:white;padding:3px 10px;'
        f'border-radius:12px;font-size:12px;font-weight:600;">'
        f'{text}</span>'
    )


def _post_card(post: SocialPost, card_num: int) -> str:
    """Build one post card as an HTML string."""

    # Image
    img_src = _image_to_base64(post.image_url) if post.image_url else ""
    if img_src:
        image_html = (
            f'<div style="margin-bottom:16px;">'
            f'<img src="{img_src}" '
            f'style="width:100%;max-height:400px;object-fit:cover;'
            f'border-radius:8px;border:1px solid #e5e7eb;" '
            f'alt="Generated image for post {card_num}"/></div>'
        )
    else:
        image_html = (
            '<div style="background:#f3f4f6;border:2px dashed #d1d5db;'
            'border-radius:8px;padding:40px;text-align:center;'
            'color:#9ca3af;margin-bottom:16px;font-size:14px;">'
            'No image generated</div>'
        )

    # Caption -- escape HTML and preserve line breaks
    caption_html = (
        post.caption
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )

    # Hashtag pills
    hashtag_html = ""
    if post.hashtags.all_tags():
        pills = "".join(
            f'<span style="background:#eff6ff;color:#1d4ed8;padding:3px 8px;'
            f'border-radius:12px;font-size:12px;margin:2px;display:inline-block;">'
            f'{tag}</span>'
            for tag in post.hashtags.all_tags()
        )
        hashtag_html = (
            '<div style="margin-top:12px;">'
            '<div style="font-size:11px;color:#6b7280;margin-bottom:6px;'
            'font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">'
            'Tier 1 to Tier 3</div>'
            f'{pills}</div>'
        )

    # Image prompt (collapsible)
    prompt_html = ""
    if post.image_prompt:
        prompt_escaped = (
            post.image_prompt
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        prompt_html = (
            '<details style="margin-top:12px;">'
            '<summary style="font-size:12px;color:#6b7280;cursor:pointer;'
            'font-weight:600;">Image prompt used</summary>'
            f'<p style="font-size:12px;color:#374151;margin-top:8px;'
            f'background:#f9fafb;padding:10px;border-radius:6px;'
            f'font-family:monospace;">{prompt_escaped}</p>'
            '</details>'
        )

    # Source clip link
    clip_html = ""
    if post.clip_url:
        clip_html = (
            '<div style="margin-top:8px;font-size:12px;">'
            f'<a href="{post.clip_url}" target="_blank" '
            f'style="color:#0A66C2;">Watch source clip</a></div>'
        )

    platform_badge = _badge(post.platform, PLATFORM_COLOURS.get(post.platform, "#666"))
    status_badge = _badge(post.status.upper(), STATUS_COLOURS.get(post.status, "#888"))
    pillar_text = post.content_pillar[:60] if post.content_pillar else ""

    return f"""
<div style="background:white;border:1px solid #e5e7eb;border-radius:12px;
    padding:24px;margin-bottom:24px;box-shadow:0 1px 3px rgba(0,0,0,0.07);">

  <div style="display:flex;justify-content:space-between;align-items:center;
      margin-bottom:16px;flex-wrap:wrap;gap:8px;">
    <div style="display:flex;align-items:center;gap:10px;">
      <span style="font-weight:700;font-size:16px;color:#111827;">Post {card_num}</span>
      {platform_badge}
      {status_badge}
    </div>
    <span style="font-size:12px;color:#6b7280;">{pillar_text}</span>
  </div>

  {image_html}

  <div style="font-size:14px;color:#111827;line-height:1.7;">
    {caption_html}
  </div>

  {hashtag_html}
  {clip_html}
  {prompt_html}

</div>"""


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def write_preview(
    result: CaptionResult,
    output_path: str = "data/preview.html",
) -> str:
    """
    Write a complete HTML preview file for all posts in a CaptionResult.

    Images are embedded as base64 so the file works as a single
    standalone file -- no server, no folder dependency.

    Args:
        result:      The CaptionResult containing all posts.
        output_path: Where to write the HTML file.

    Returns:
        The output_path that was written (for display in the runner).
    """
    total = result.total_posts
    approved = len(result.approved_posts())
    has_images = any(p.image_url for p in result.posts)

    cards_html = "".join(
        _post_card(post, i + 1)
        for i, post in enumerate(result.posts)
    )

    images_badge = (
        "<span class='stat'>Images generated</span>"
        if has_images
        else "<span class='stat'>No images yet</span>"
    )

    no_images_notice = "" if has_images else (
        '<div class="notice">'
        'Images have not been generated yet. '
        'Run Phase 6A to generate images, then refresh this preview.'
        '</div>'
    )

    open_cmd = "open" if os.name != "nt" else "start"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Social Spark Studio -- Campaign Preview</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #f9fafb;
      color: #111827;
      padding: 24px;
    }}
    .container {{ max-width: 800px; margin: 0 auto; }}
    .header {{
      background: white;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
      padding: 24px;
      margin-bottom: 24px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.07);
    }}
    .stat {{
      display: inline-block;
      background: #f3f4f6;
      border-radius: 8px;
      padding: 8px 16px;
      margin: 6px 6px 0 0;
      font-size: 14px;
    }}
    .notice {{
      background: #fffbeb;
      border: 1px solid #fcd34d;
      border-radius: 8px;
      padding: 12px 16px;
      margin-bottom: 24px;
      font-size: 13px;
      color: #92400e;
    }}
  </style>
</head>
<body>
  <div class="container">

    <div class="header">
      <h1 style="font-size:22px;font-weight:700;margin-bottom:8px;">
        Campaign Preview
      </h1>
      <p style="color:#6b7280;font-size:14px;margin-bottom:14px;">
        Campaign ID: {result.campaign_id}
      </p>
      <div>
        <span class="stat"><strong>{total}</strong> posts total</span>
        <span class="stat"><strong>{approved}</strong> approved</span>
        <span class="stat"><strong>{total - approved}</strong> draft</span>
        {images_badge}
      </div>
    </div>

    {no_images_notice}

    {cards_html}

    <div style="text-align:center;padding:24px;color:#9ca3af;font-size:13px;">
      Social Spark Studio &middot; {output_path}
    </div>

  </div>
</body>
</html>"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Preview saved to: {output_path}")
    print(f"To view: {open_cmd} {output_path}")
    return output_path
