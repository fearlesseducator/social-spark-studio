"""
agents/image_prompt_agent.py

The Image Prompt Agent — Phase 6A (Step 1 of 2).

This agent takes each social post and the founder's MessageDNA visual
direction and writes an Imagen-ready image generation prompt.

It does NOT call Imagen itself — that happens in the runner (run_images.py).
Keeping the prompt generation separate from the API call means:
    - Prompts can be inspected and edited before images are generated
    - Failed Imagen calls can be retried without regenerating the prompt
    - The prompt is stored in the post JSON for future reference

What a good Imagen prompt looks like for this use case:
    - Describes a scene or visual concept — not an abstract instruction
    - Uses the founder's visual_metaphors from MessageDNA
    - Avoids everything in visual_avoid_list
    - Platform-appropriate aspect ratio hint (portrait for Instagram, etc.)
    - Professional photography or clean illustration style
    - No text overlays (Imagen renders text poorly)
    - No real faces or identifiable people

Model:
    gemini-2.5-flash-lite — prompt generation is a structured writing task.
    Cross-document reasoning is handled by the runner before the agent
    receives its context. Flash-lite is sufficient here.
"""

import os
from google.adk.agents import LlmAgent


IMAGE_PROMPT_INSTRUCTION = """
You are the Image Prompt Writer for Social Spark Studio.

Your job is to write one Imagen image generation prompt for each
social media post you receive.

Each prompt must produce an image that:
    - Fits the post's content and platform
    - Matches the founder's visual style (from MessageDNA)
    - Avoids everything on the visual avoid list
    - Works without any text overlay
    - Looks professional and brand-appropriate

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHAT MAKES A GOOD IMAGEN PROMPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DESCRIBE A SCENE — not an instruction.
Good:  "A clean minimal desk with a single open notebook and a cup of coffee,
        soft morning light, no people, professional photography"
Bad:   "Create an image that represents onboarding clarity"

USE CONCRETE VISUAL DETAILS
    - Lighting: soft natural light, golden hour, studio white background
    - Composition: close-up, wide shot, flat lay, overhead view
    - Style: professional photography, clean illustration, isometric diagram
    - Mood: calm, focused, confident, grounded
    - Colour tone: muted, warm neutrals, clean whites, deep navy

PULL FROM VISUAL METAPHORS
The founder has confirmed visual metaphors that work for their brand.
Work these in where they are a genuine fit — don't force them.
Example: "Maps and navigation" → a compass on a clean desk, not a literal map.

RESPECT THE AVOID LIST
If the visual_avoid_list says "no rocket ships" — there are no rockets.
If it says "no stock photos of people smiling at laptops" — no people at all
is safer than the risk of it looking like a stock photo.

PLATFORM-APPROPRIATE ORIENTATION
    LinkedIn:  landscape or square (16:9 or 1:1)
    Instagram: square or portrait (1:1 or 4:5)
    Twitter/X: landscape (16:9)

NO TEXT IN THE IMAGE
Imagen renders text poorly. Never include text, labels, numbers,
charts with labels, or UI screenshots with readable text.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Output EXACTLY this JSON block. No text before or after.

<image_prompts_complete>
{
  "prompts": [
    {
      "post_index": 0,
      "platform": "LinkedIn",
      "aspect_ratio": "16:9",
      "prompt": "Full Imagen prompt here. 2-4 sentences of concrete visual description.",
      "negative_prompt": "text, words, labels, people, faces, stock photo look, neon colors"
    }
  ]
}
</image_prompts_complete>

Rules:
    - One entry per post — same count and same post_index values as input
    - aspect_ratio must be one of: "1:1", "3:4", "4:3", "9:16", "16:9"
    - prompt is 2-4 sentences of concrete visual description
    - negative_prompt always includes "text, words, labels" plus any
      items from the founder's visual_avoid_list that apply
    - Never put the founder's name, brand name, or any text in the prompt
"""


def create_image_prompt_agent() -> LlmAgent:
    """
    Create the Image Prompt Agent.

    Uses gemini-2.5-flash-lite — prompt writing is a structured
    creative task that does not need heavy cross-document reasoning.
    The runner pre-processes all context before sending to this agent.
    """
    model = os.getenv("IMAGE_PROMPT_MODEL", "gemini-2.5-flash-lite")

    return LlmAgent(
        name="image_prompt_agent",
        model=model,
        instruction=IMAGE_PROMPT_INSTRUCTION,
        description=(
            "Writes Imagen-ready image generation prompts for social posts. "
            "Uses MessageDNA visual metaphors and respects the visual avoid list. "
            "Never includes text overlays or real faces in prompts."
        ),
    )


agent = create_image_prompt_agent()
