# Social Spark Studio
### A multi-agent Google ADK workflow — one founder video in, a full campaign out
**Built with Python + FastAPI + Google ADK · Gemini 2.5 Flash · Imagen 4.0 · Chirp 3 voice**

Social Spark Studio turns one founder video into a ready-to-schedule
social media campaign — in the founder's real voice, not generic AI voice.

**Eight specialised ADK agents** work in sequence across seven phases:

| Phase | What happens | Powered by |
|-------|-------------|------------|
| 1. MessageDNA | Spoken/typed founder interview captures voice, audience, positioning | Voice Conversation Agent + STT/TTS (Chirp 3) |
| 2. Campaign Brief | Goal, offer, CTA, and source video for one campaign | Campaign Brief Agent |
| 3. Video Transcript | Upload a 5–20 min founder MP4 — audio extracted and transcribed | Cloud Storage + ffmpeg + Chirp 3 STT |
| 4. Moments | Highest-signal quotes selected and scored | Moment Selector Agent |
| 5. Post Drafts | Up to 15 posts in the founder's confirmed voice + 3-tier hashtags | Caption Agent → Hashtag Agent |
| 6. Images | On-brand visuals for every image post | Image Prompt Agent → Imagen 4.0 |
| 7. Export | Scheduler-ready CSV (Buffer / Hypefury / SparkCircle) | run_export |

Everything runs from a **web UI** (FastAPI + background jobs with live
status polling) or from per-phase CLI scripts — both share the same code.

➡ **Full agent and data-flow documentation: [ADK_AGENT_ARCHITECTURE.md](ADK_AGENT_ARCHITECTURE.md)**

**Run it locally:**

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python -m uvicorn app:app --reload --port 8000
# open http://localhost:8000
```

---

## Deployment (Cloud Run)

**Local development:**
```bash
python -m uvicorn app:app --reload --port 8000
```

**Production (what the Dockerfile runs):**
```bash
uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}
```
No `--reload` in production. Cloud Run injects `$PORT` automatically.

**Required environment variables** (set in the Cloud Run service, not in a file):

| Variable | Purpose |
|----------|---------|
| `GOOGLE_API_KEY` | Gemini agents |
| `GOOGLE_CLOUD_PROJECT` | Imagen, Speech-to-Text, Text-to-Speech |
| `GOOGLE_CLOUD_LOCATION` | GCP region (e.g. `global`) |
| `IMAGEN_MODEL` | e.g. `imagen-4.0-generate-001` |
| `GCS_BUCKET_NAME` | intended target for generated image/CSV URLs |
| `SPEECH_TO_TEXT_MODEL` / `TTS_VOICE_NAME` / `TTS_LANGUAGE_CODE` | voice studio |
| `VOICE_AGENT_MODEL` | e.g. `gemini-2.5-flash` |
| `DEMO_PASSWORD` | gates the demo login on a public URL |

**Credentials:**
- **Never upload `.env`** — it is gitignored and dockerignored on purpose.
- **Do not ship a service-account JSON in the image.** Instead, attach a
  service account to the Cloud Run service (with Vertex AI, Speech, and
  Text-to-Speech permissions) and omit `GOOGLE_APPLICATION_CREDENTIALS`
  entirely — Google client libraries pick up the attached identity
  automatically.

**Known demo limitation — local file storage:**
All generated outputs (JSON, images, CSVs) are written to the local
`data/` directory. On Cloud Run this filesystem is **ephemeral**: outputs
disappear on restart or scale-to-zero. This is acceptable for a live demo
(run the pipeline once per session). For persistence, the intended path is
uploading generated images and CSVs to the `GCS_BUCKET_NAME` bucket and
serving public URLs — not yet wired up.

---

## Phase 1 deep-dive: the MessageDNA interview agent

**The most important rule in this system:**
> **Message before media.**
> Before any YouTube video is analyzed, any caption is written, or any
> hashtag is researched — the system must first understand the founder's
> long-term voice, beliefs, audience, and positioning.

That's what Phase 1 builds: the **MessageDNA interview agent**.

---

## The Two Core Objects

This system separates long-term founder identity from campaign-specific details.
This is the most important architectural decision in the product.

### MessageDNA (Phase 1)
- **Belongs to:** The founder's profile
- **Lifespan:** Long-term, reused across all campaigns
- **Captures:** Voice, beliefs, positioning, audience worldview, tone,
  signature phrases, content pillars, visual metaphors, what to avoid
- **Rule:** Never overwritten by campaign details unless the founder
  explicitly chooses to update it

### CampaignBrief (Phase 2)
- **Belongs to:** One specific campaign
- **Lifespan:** Changes every campaign
- **Captures:** Campaign goal, selected offer, CTA, platforms, selected
  YouTube video, campaign theme, timely context, scheduling needs
- **Rule:** Always uses MessageDNA for voice — never replaces it

---

## Phase 1 — What Was Built

```
social-spark-studio/
├── agents/
│   ├── __init__.py
│   └── message_dna_agent.py     ← The interview agent (ADK LlmAgent)
├── models/
│   ├── __init__.py
│   └── message_dna.py           ← MessageDNA data model (Python dataclasses)
├── utils/
│   ├── __init__.py
│   └── interview_state.py       ← Interview progress tracker
├── data/
│   └── sample_output/
│       └── sample_message_dna.json  ← Example of what a completed interview produces
├── run_interview.py             ← Local runner (start here)
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md                    ← This file
```

---

## Setup — Do This First

### Step 1: Install dependencies

```bash
pip install -r requirements.txt
```

### Step 2: Get a Google API Key

Go to: https://aistudio.google.com/app/apikey
Create a key. It's free for development.

### Step 3: Set your API key

```bash
# Mac/Linux:
export GOOGLE_API_KEY=your_key_here

# Windows:
set GOOGLE_API_KEY=your_key_here

# Or copy .env.example to .env and fill in the key, then:
source .env
```

---

## Run the Interview

```bash
python run_interview.py
```

The agent will welcome you and start asking questions.
Answer each question. Press Enter to send your answer.
Type `quit` to stop.

When all 5 sections are complete, your MessageDNA is saved to:
```
data/message_dna_output.json
```

### Optional: Save to a custom path

```bash
python run_interview.py --output data/my_founder_profile.json
```

### Optional: Use Gemini Pro (better for long conversations)

```bash
MESSAGE_DNA_MODEL=gemini-2.5-flash python run_interview.py
```

---

## How You Know Phase 1 Works

Run through this checklist after running the interview:

- [ ] The agent starts with a warm welcome message
- [ ] The agent asks only ONE question at a time (never stacks questions)
- [ ] When you give a vague answer, the agent asks ONE clarifying follow-up
- [ ] After all questions in a section are answered, the agent reads back a summary
- [ ] The agent asks "Does that sound right?" before moving to the next section
- [ ] After you confirm the summary, the agent moves to the next section
- [ ] After all 5 sections, the agent outputs the `<message_dna_complete>` block
- [ ] A JSON file is saved to `data/message_dna_output.json`
- [ ] The JSON file contains all 5 sections with your actual answers
- [ ] The JSON matches the structure in `data/sample_output/sample_message_dna.json`

---

## The 5 Interview Sections

| # | Section | What It Captures |
|---|---------|-----------------|
| 1 | Founder Identity | Name, brand name, what they want to be known for |
| 2 | Audience and Pain | Ideal audience, worldview, core problem, misconceptions |
| 3 | Beliefs and Positioning | Signature beliefs, contrarian belief, origin story, future vision |
| 4 | Voice and Language | Brand voice words, phrases to use/avoid, tone rules, CTA style |
| 5 | Content and Visual Direction | Content pillars, visual metaphors, avoid list, credibility markers |

---

## Full 6-Phase Build Plan

This is the complete roadmap. You are on **Phase 1**.
Each phase builds on the one before it.

---

### Phase 1 — MessageDNA Interview Agent ✅ (This phase)

**What it does:** Conducts a structured voice interview with the founder.
Captures their long-term messaging identity. Saves it as a JSON file.

**Files built:**
- `agents/message_dna_agent.py` — ADK agent with interview instruction
- `models/message_dna.py` — MessageDNA data model
- `utils/interview_state.py` — Interview progress tracker
- `run_interview.py` — Local terminal runner

**Phase 1 is complete when:**
The agent runs in the terminal, asks all 5 sections, confirms each section,
and saves a valid `message_dna_output.json` file.

---

### Phase 2 — CampaignBrief Agent

**What it does:** Asks the founder 5–7 questions about THIS specific campaign.
Loads the existing MessageDNA. Combines both into a session object.

**Files to build:**
- `models/campaign_brief.py` — CampaignBrief data model
- `agents/campaign_brief_agent.py` — Short interview (campaign-specific details only)
- `run_campaign.py` — Runner that loads MessageDNA + creates CampaignBrief

**CampaignBrief captures:**
- Campaign goal (what do you want this campaign to achieve?)
- Selected offer (which product or service is this campaign for?)
- Primary CTA (what one action do you want people to take?)
- Target platforms (LinkedIn, Instagram, Twitter/X, etc.)
- Campaign theme (the main angle or hook for this campaign)
- Timely context (any promotions, launches, or deadlines?)
- Scheduling preferences (how many posts per week?)

**Phase 2 is complete when:**
The runner loads an existing `message_dna_output.json`, runs the CampaignBrief
interview, and saves a `campaign_brief_output.json` that includes both.

**Key test:** Changing campaign details must NEVER overwrite the MessageDNA file.

---

### Phase 3 — YouTube Miner + Transcript Agent

**What it does:** Takes a YouTube video URL from the CampaignBrief,
fetches the transcript, and structures it into timestamped segments.

**Files to build:**
- `agents/youtube_agent.py` — Fetches transcript and structures it
- `utils/youtube_fetcher.py` — YouTube transcript extraction utility
  (use `youtube-transcript-api` Python package for local testing)

**New dependency to add:**
```
youtube-transcript-api>=0.6.0
```

**YouTube agent captures per segment:**
- Full transcript text
- Timestamps (start/end)
- Speaker confidence (is this clearly stated or vague?)

**Phase 3 is complete when:**
You paste a YouTube URL, the agent fetches the transcript, and saves it as
structured JSON with timestamps. Test with a 5–15 minute video.

**Important:** The agent must confirm the video has usable captions.
If captions are unavailable, it must return a clear failure message —
never fabricate or summarize missing transcript content.

---

### Phase 4 — Transcript Moment Selector

**What it does:** Reads the transcript segments, reads the MessageDNA and
CampaignBrief, and identifies the 8–12 best moments for social content.

**Files to build:**
- `agents/moment_selector_agent.py` — Selects social-worthy transcript moments

**What a "moment" is:**
A segment of the transcript that:
1. Connects to at least one content pillar in MessageDNA
2. Contains a specific insight, story, or proof point (not a vague comment)
3. Is short enough to quote (30–120 words)
4. Would make a non-follower stop scrolling

**Each selected moment includes:**
- The exact quote (no paraphrasing)
- The timestamp
- Which content pillar it connects to
- Why it's social-worthy (one sentence)
- Which platform it's best suited for

**Phase 4 is complete when:**
Given a transcript JSON + MessageDNA + CampaignBrief, the agent returns
a list of 8–12 selected moments with complete metadata.

---

### Phase 5 — Caption and Hashtag Writer

**What it does:** Takes the selected transcript moments, MessageDNA,
and CampaignBrief, and generates ready-to-post social media captions.

**Files to build:**
- `agents/caption_agent.py` — Writes captions in the founder's voice
- `agents/hashtag_agent.py` — Selects relevant hashtags per post

**Caption rules (enforced in agent instruction):**
- Must use MessageDNA voice words, phrases_to_use, tone_rules
- Must NOT use any phrase in MessageDNA's phrases_to_avoid
- Must quote the transcript directly — no paraphrasing
- Must include a CTA in the founder's cta_style
- Must be aligned to one content pillar from MessageDNA
- No fabricated statistics, fake testimonials, or invented claims

**Hashtag rules:**
- Tier 1 Niche: 2–3 tags under 100K posts
- Tier 2 Mid-range: 2–3 tags between 100K–1M posts
- Tier 3 Broad: max 2 tags over 1M posts

**Phase 5 is complete when:**
Given transcript moments + MessageDNA + CampaignBrief, the caption agent
produces captions that sound like the founder's confirmed voice words
and do not use any of the phrases_to_avoid.

Manual test: Read the output captions and compare them to the MessageDNA
voice profile. They should match.

---

### Phase 6 — CSV Export

**What it does:** Takes the reviewed and approved posts and formats them
into a scheduler-ready CSV file.

**Files to build:**
- `agents/csv_export_agent.py` — Formats posts into CSV
- `utils/csv_formatter.py` — CSV formatting utility

**CSV columns (exact names — these match your uploaded CSV format):**

| Column | Source |
|--------|--------|
| postAtSpecificTime | User-set or blank (YYYY-MM-DD HH:mm:ss) |
| content +Hashtags | Caption + hashtag block |
| link (OGmetaUrl) | CTA link from CampaignBrief |
| imageUrls | Placeholder or image URL |
| gifUrl | Always blank in Phase 1 |
| videoUrls | YouTube timestamp URL or blank |

**Phase 6 is complete when:**
The system produces a CSV file that can be uploaded to a social media
scheduler (like Buffer, Later, or Publer) without modification.

Test by opening the CSV in a spreadsheet and verifying:
- All 6 columns are present with the exact header names from the sample CSV
- No extra whitespace or encoding errors
- Content field includes both caption text and hashtags
- Timestamps are in YYYY-MM-DD HH:mm:ss format when present

---

## Prompt Sequence for Claude Code

Use these prompts in order. **Finish each phase and test it before moving on.**

### Phase 1 Prompt (already done — see this file)
You're reading the output of Phase 1.

### Phase 2 Prompt (paste this into Claude Code)
```
You are helping me build Social Spark Studio using Python and Google ADK.
Phase 1 is complete. I have a working MessageDNA interview agent that saves
a JSON file to data/message_dna_output.json.

PHASE 2 ONLY: Build the CampaignBrief agent.

The CampaignBrief agent must:
1. Load the existing MessageDNA from data/message_dna_output.json
2. Ask the founder 6-7 questions about THIS specific campaign (one at a time)
3. Confirm the campaign details with a summary before saving
4. Save a campaign_brief_output.json that contains BOTH the MessageDNA
   (unchanged, by reference) and the new CampaignBrief data

CampaignBrief must capture:
- campaignGoal
- selectedOffer
- primaryCTA
- targetPlatforms (array)
- campaignTheme
- timelyContext
- schedulingPreferences

ARCHITECTURE RULE: The CampaignBrief agent must NEVER modify or overwrite
message_dna_output.json. It reads it. It does not write to it.

Use the same project structure already in place (agents/, models/, utils/).
Follow the same patterns as message_dna_agent.py.
Add a run_campaign.py runner that loads MessageDNA first, then runs the
CampaignBrief interview.
```

### Phase 3 Prompt
```
You are helping me build Social Spark Studio using Python and Google ADK.
Phases 1 and 2 are complete. I have MessageDNA and CampaignBrief working.

PHASE 3 ONLY: Build the YouTube transcript agent.

This agent must:
1. Accept a YouTube video URL from the CampaignBrief
2. Use youtube-transcript-api to fetch the captions
3. Structure the transcript into timestamped segments (minimum 30 words each)
4. Save the structured transcript as data/transcript_output.json
5. Return a clear failure message if captions are unavailable — never fabricate content

Each transcript segment must include:
- text (the actual words)
- start_time (in seconds)
- end_time (in seconds, estimated from duration)
- word_count

Add youtube-transcript-api to requirements.txt.
Build agents/youtube_agent.py following the same patterns as existing agents.
Add a test in the runner that verifies minimum 300 words were captured.
```

### Phase 4 Prompt
```
Phases 1, 2, and 3 are complete. I have MessageDNA, CampaignBrief, and
a structured transcript.

PHASE 4 ONLY: Build the transcript moment selector agent.

This agent reads:
- data/message_dna_output.json (for content pillars and positioning)
- data/campaign_brief_output.json (for campaign goal and theme)
- data/transcript_output.json (the source transcript)

It selects 8-12 of the best moments for social content.

Each selected moment must include:
- exact_quote (verbatim from transcript — no paraphrasing)
- start_time, end_time
- content_pillar (must match a pillar from MessageDNA exactly)
- platform_recommendation (LinkedIn, Instagram, etc.)
- why_social_worthy (one sentence explanation)

Moments must NOT be selected if they:
- Don't connect to any content pillar in MessageDNA
- Are too vague or generic
- Are under 30 words
- Are over 150 words

Save output to data/moments_output.json.
```

### Phase 5 Prompt
```
Phases 1-4 are complete.

PHASE 5 ONLY: Build the caption and hashtag writer agents.

The caption agent takes:
- data/moments_output.json (selected transcript moments)
- data/message_dna_output.json (for voice, phrases, tone rules)
- data/campaign_brief_output.json (for CTA and campaign goal)

It writes one caption per selected moment.

STRICT CAPTION RULES (enforce in the agent instruction):
- Must use phrases from MessageDNA.voice_profile.phrases_to_use
- Must NOT use any phrase from MessageDNA.voice_profile.phrases_to_avoid
- Must quote the transcript directly
- Must end with a CTA matching MessageDNA.voice_profile.cta_style
- No fabricated statistics
- No generic AI hooks ("In today's fast-paced world...")

The hashtag agent adds three tiers per post:
- Tier 1: 2-3 niche tags (under 100K)
- Tier 2: 2-3 mid-range tags (100K-1M)
- Tier 3: max 2 broad tags (over 1M)

Save output to data/posts_output.json with one object per post
containing: caption, hashtags_tier1, hashtags_tier2, hashtags_tier3,
source_quote, timestamp_start, timestamp_end, content_pillar, platform.
```

### Phase 6 Prompt
```
Phases 1-5 are complete.

PHASE 6 ONLY: Build the CSV export agent.

The CSV must use EXACTLY these 6 column headers (from my uploaded CSV sample):
- postAtSpecificTime
- content +Hashtags
- link (OGmetaUrl)
- imageUrls
- gifUrl
- videoUrls

The content +Hashtags column must combine the caption and the full
hashtag block (all three tiers) into one field.
postAtSpecificTime format: YYYY-MM-DD HH:mm:ss (blank if not scheduled)
videoUrls: YouTube URL with timestamp if available, blank otherwise
imageUrls: blank for Phase 6 (images come in a later phase)
gifUrl: always blank

Read from data/posts_output.json.
Save to data/export_[TIMESTAMP].csv.
Encoding: UTF-8.
```

---

## Understanding Google ADK — Quick Reference

### What is ADK?
Google ADK (Agent Development Kit) is a Python framework for building
AI agents. It handles the conversation loop, session memory, and
communication with Gemini models.

### Key ADK concepts used in Phase 1

**LlmAgent** — The main agent class. You give it:
- A name
- A model (e.g., "gemini-2.5-flash-lite")
- An instruction (the system prompt that tells it what to do)

**InMemoryRunner** — Runs the agent locally for testing.
In production, you'd use Agent Engine on Google Cloud.

**Session** — Represents one ongoing conversation.
Each conversation has a user_id and session_id.

**types.Content / types.Part** — How you send messages to the agent.
Every message is wrapped in Content with a list of Parts.

**Events** — What the runner returns. You iterate through events
to collect the agent's text response.

### The conversation pattern (used in run_interview.py)
```python
runner = InMemoryRunner(agent=agent, app_name="my_app")
session = runner.session_service.create_session_sync(
    app_name="my_app",
    user_id="user_123",
    session_id="session_abc",
)
events = runner.run(
    user_id="user_123",
    session_id="session_abc",
    new_message=types.Content(role="user", parts=[types.Part(text="Hello")])
)
for event in events:
    # collect event.content.parts[x].text
```

---

## Common Issues and Fixes

### "No module named google.adk"
```bash
pip install google-adk
```

### "GOOGLE_API_KEY not set" error
```bash
export GOOGLE_API_KEY=your_key_here
```
Get a key at: https://aistudio.google.com/app/apikey

### Agent gives generic answers
The instruction in `message_dna_agent.py` is the key.
If the agent isn't following the rules, strengthen the instruction.
The most common issue: the agent asks multiple questions at once.
Add "RULE 1 — ONE QUESTION AT A TIME. Never. Not once." to the instruction.

### JSON not being saved
Check that the agent is including the exact tags:
`<message_dna_complete>` and `</message_dna_complete>`
around its JSON output. If the tags are missing, the runner can't extract it.
You can test the extraction with: `python -c "from run_interview import extract_json_from_response; print(extract_json_from_response('<message_dna_complete>{\"test\": 1}</message_dna_complete>'))")`

### The interview feels too slow
Switch to `gemini-2.5-flash-lite` (the default).
Flash is faster than Pro. Use Pro when you need better reasoning.

---

## What's NOT Built Yet (By Design)

Phase 1 intentionally excludes everything except MessageDNA:

- ❌ CampaignBrief (Phase 2)
- ❌ YouTube video mining (Phase 3)
- ❌ Transcript extraction (Phase 3)
- ❌ Caption generation (Phase 5)
- ❌ Hashtag generation (Phase 5)
- ❌ CSV export (Phase 6)
- ❌ Frontend (future)
- ❌ Firebase Auth (future)
- ❌ Firestore database (future)
- ❌ Cloud Run deployment (future)
- ❌ Voice input (future)
- ❌ Imagen image generation (future)

Build one phase. Test it. Then move to the next.
This approach catches problems early and keeps the codebase clean.
