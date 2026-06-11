"""
tools/imagen_tool.py

Calls the Imagen API and saves generated images to local disk.

This utility is intentionally kept separate from the ADK agent layer.
The image_prompt_agent (ADK) generates the text prompts.
This module takes those prompts and makes the actual API call.

Keeping these separate means:
    - Prompts can be reviewed before images are generated
    - Failed API calls can be retried without rerunning the agent
    - The Imagen call logic is easy to swap for Cloud Storage later

Local save path:
    data/images/{campaign_id}_post_{post_index:03d}.png

The image_url field in SocialPost is set to this local path.
In production, swap save_image_locally() for a Cloud Storage upload
and update image_url to the resulting https:// URL.

Supported Imagen models (GOOGLE_API_KEY / Gemini Developer API):
    imagen-4.0-generate-001       -- best quality (default)
    imagen-4.0-fast-generate-001  -- faster, lower cost
    imagen-4.0-ultra-generate-001 -- highest quality, slower

Requires:
    GOOGLE_API_KEY   — for Gemini Developer API (local testing)
    OR
    GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION — for Vertex AI (production)
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ── Result type ───────────────────────────────────────────────────────

@dataclass
class ImageResult:
    """Result of one Imagen generation attempt."""
    post_index: int
    success: bool
    image_path: str = ""        # local file path if success
    image_url: str = ""         # same as image_path for local; https:// in production
    error_message: str = ""
    model_used: str = ""
    prompt_used: str = ""


# ── Core function ─────────────────────────────────────────────────────

def generate_image(
    prompt: str,
    aspect_ratio: str,
    output_path: str,
    post_index: int,
    model: str = "imagen-4.0-generate-001",
) -> ImageResult:
    """
    Call Imagen to generate one image and save it to disk.

    Args:
        prompt:       The Imagen prompt.
        aspect_ratio: One of "1:1", "3:4", "4:3", "9:16", "16:9".
        output_path:  Full file path to save the PNG (e.g. data/images/post_001.png).
        post_index:   The post number — used for error messages only.
        model:        Imagen model string.

    Returns:
        ImageResult with success=True and image_path set, or
        ImageResult with success=False and error_message set.
    """
    try:
        import google.genai as genai
        from google.genai import types as gtypes

        # ── Build client ──────────────────────────────────────────
        api_key = os.getenv("GOOGLE_API_KEY")
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

        if api_key:
            # Gemini Developer API (local testing)
            client = genai.Client(api_key=api_key)
        elif project:
            # Vertex AI (production / Google Cloud)
            client = genai.Client(
                vertexai=True,
                project=project,
                location=location,
            )
        else:
            return ImageResult(
                post_index=post_index,
                success=False,
                error_message=(
                    "No credentials found. Set GOOGLE_API_KEY for local testing "
                    "or GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION for Vertex AI."
                ),
                model_used=model,
                prompt_used=prompt,
            )

        config_kwargs = {
            "number_of_images": 1,
            "aspect_ratio": aspect_ratio,
        }

        response = client.models.generate_images(
            model=model,
            prompt=prompt,
            config=gtypes.GenerateImagesConfig(**config_kwargs),
        )

        # ── Extract image bytes ───────────────────────────────────
        if not response.generated_images:
            return ImageResult(
                post_index=post_index,
                success=False,
                error_message="Imagen returned no images (may have been filtered by safety checks).",
                model_used=model,
                prompt_used=prompt,
            )

        generated = response.generated_images[0]

        # Check for safety filter rejection
        if generated.rai_filtered_reason:
            return ImageResult(
                post_index=post_index,
                success=False,
                error_message=f"Image filtered by safety system: {generated.rai_filtered_reason}",
                model_used=model,
                prompt_used=prompt,
            )

        if not generated.image or not generated.image.image_bytes:
            return ImageResult(
                post_index=post_index,
                success=False,
                error_message="Generated image has no bytes (unexpected API response).",
                model_used=model,
                prompt_used=prompt,
            )

        # ── Save to disk ──────────────────────────────────────────
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(generated.image.image_bytes)

        return ImageResult(
            post_index=post_index,
            success=True,
            image_path=output_path,
            image_url=output_path,     # local path; swap for GCS URL in production
            model_used=model,
            prompt_used=prompt,
        )

    except Exception as e:
        return ImageResult(
            post_index=post_index,
            success=False,
            error_message=str(e),
            model_used=model,
            prompt_used=prompt,
        )


def upload_image_to_gcs(local_path: str, dest_name: str = "") -> str:
    """
    Upload a generated PNG to the GCS_BUCKET_NAME bucket and return its
    public https:// URL. Returns "" on any failure (caller keeps the
    local path as fallback — upload failure never breaks the pipeline).

    Objects land under generated_images/ and are served via
    https://storage.googleapis.com/{bucket}/generated_images/{name}.
    """
    bucket_name = os.getenv("GCS_BUCKET_NAME", "")
    project     = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    if not bucket_name or not project:
        return ""
    try:
        from google.cloud import storage as gcs_storage
        client = gcs_storage.Client(project=project)
        bucket = client.bucket(bucket_name)
        name   = dest_name or Path(local_path).name
        blob   = bucket.blob(f"generated_images/{name}")
        blob.upload_from_filename(local_path, content_type="image/png")
        return f"https://storage.googleapis.com/{bucket_name}/generated_images/{name}"
    except Exception as e:
        print(f"[imagen] GCS upload failed for {local_path}: {e}")
        return ""


def build_image_output_path(campaign_id: str, post_index: int, images_dir: str) -> str:
    """
    Build the local file path for a generated image.

    Example: data/images/campaign_abc123_post_001.png
    """
    safe_id = campaign_id.replace("/", "_").replace(" ", "_")
    filename = f"{safe_id}_post_{post_index:03d}.png"
    return str(Path(images_dir) / filename)
