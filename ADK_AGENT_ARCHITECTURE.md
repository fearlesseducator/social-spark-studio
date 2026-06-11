# Social Spark Studio — ADK Agent Architecture

Social Spark Studio is a **multi-agent workflow built on the Google Agent
Development Kit (ADK)**. Eight specialised Gemini agents, coordinated by a
FastAPI web layer, turn one YouTube video into a complete, scheduler-ready
social media campaign — in the founder's authentic voice.

The architectural rule that shapes everything: **message before media.**
No content is generated until the founder's long-term voice (MessageDNA)
has been captured and confirmed.

---

## The Agents

All agents are `LlmAgent` instances created in `agents/` and executed with
ADK's `InMemoryRunner`. Each runs in a fresh session, receives only the
context it needs, and returns a structured output block (JSON wrapped in
sentinel tags) that the next step parses.

| # | Agent | File | Model | Role |
|---|-------|------|-------|------|
| 1 | **MessageDNA Agent** | `agents/message_dna_agent.py` | gemini-2.5-flash | Conducts the 4-block founder interview (audience, offer, voice, positioning) and extracts the structured MessageDNA profile |
| 2 | **Voice Conversation Agent** | `agents/voice_conversation_agent.py` | gemini-2.5-flash | Drives the spoken version of the MessageDNA interview — one question at a time, block summaries, explicit confirmation gates |
| 3 | **Campaign Brief Agent** | `agents/campaign_brief_agent.py` | gemini-2.5-flash | Interviews the founder about one specific campaign: goal, offer, CTA, platforms, and the YouTube source video |
| 4 | **Transcript Agent** | `agents/transcript_agent.py` | gemini-2.5-flash | Cleans and structures the raw YouTube caption track into timestamped segments |
| 5 | **Moment Selector Agent** | `agents/moment_selector_agent.py` | gemini-2.5-flash | Scores every transcript segment against MessageDNA + brief and selects up to 9 high-signal, quotable moments |
| 6 | **Caption Agent** | `agents/caption_agent.py` | gemini-2.5-flash | Drafts up to 15 posts across three content types (video_clip, image_post, text_quote) in the founder's confirmed voice |
| 7 | **Hashtag Agent** | `agents/hashtag_agent.py` | gemini-2.5-flash | Second-pass agent that assigns 3-tier hashtag sets (broad / niche / brand) to every post |
| 8 | **Image Prompt Agent** | `agents/image_prompt_agent.py` | gemini-2.5-flash | Converts each image post's concept into a detailed Imagen prompt using the founder's visual metaphors and avoid-list |

## The Tools

Non-LLM capabilities live in `tools/` and are called by the agents or the
service layer:

| Tool | File | Purpose |
|------|------|---------|
| **Speech-to-Text** | `tools/speech_to_text_tool.py` | Google Cloud Speech-to-Text v2 (Chirp 3) — transcribes the founder's spoken interview answers |
| **Text-to-Speech** | `tools/text_to_speech_tool.py` | Google Cloud Text-to-Speech (Chirp 3 HD) — speaks the agent's questions aloud in the browser |
| **YouTube transcript fetch** | `run_transcript.py` | youtube-transcript-api — pulls the timestamped caption track for any public video |
| **Imagen generation** | `run_images.py` | Google Imagen 4.0 via Vertex AI — generates on-brand visuals, with automatic fallback to `imagen-4.0-fast-generate-001` on safety blocks |

Both speech tools degrade gracefully: if the package or API is unavailable,
the Voice Studio falls back to text input and the interview continues.

---

## Data Flow

```
                    ┌─────────────────────────────┐
  Founder speaks →  │ Voice Conversation Agent     │ ←→ STT / TTS tools
  or types          │ (4 blocks, confirm each)     │
                    └──────────────┬──────────────┘
                                   ▼
                    data/message_dna_output.json   ← long-term, reused
                                   │
                    ┌──────────────▼──────────────┐
                    │ Campaign Brief Agent         │
                    └──────────────┬──────────────┘
                                   ▼
                    data/campaign_brief.json       ← per-campaign
                                   │  (includes YouTube URL)
                                   ▼
                    youtube-transcript-api fetch
                                   ▼
                    data/transcript_output.json
                                   │
                    ┌──────────────▼──────────────┐
                    │ Moment Selector Agent        │ ← reads DNA + brief
                    └──────────────┬──────────────┘
                                   ▼
                    data/moments_output.json
                                   │
                    ┌──────────────▼──────────────┐
                    │ Caption Agent → Hashtag Agent│ ← reads DNA + brief
                    └──────────────┬──────────────┘    + moments
                                   ▼
                    data/posts_output.json (≤15 posts)
                                   │
                ┌──────────────────┼──────────────────┐
                ▼                                     ▼
   Image Prompt Agent → Imagen 4.0        CSV export (run_export.py)
   data/generated_images/*.png            data/social-spark-*.csv
                                          (Buffer / Hypefury /
                                           SparkCircle ready)
```

Every downstream agent reads `message_dna_output.json` — the founder's
voice words, forbidden phrases, content pillars, and visual metaphors —
so the output sounds like the founder, not like a generic AI.

---

## How the Web UI Triggers the Agent Workflow

The FastAPI app (`app.py`) exposes the pipeline through
`routes/pipeline_routes.py`:

| UI page | Button | Route | Execution |
|---------|--------|-------|-----------|
| `/youtube` | Generate Transcript | `POST /api/run/transcript` | synchronous (~5 s) |
| `/moments` | Generate Moments | `POST /api/run/moments` | background job (~30–90 s) |
| `/posts` | Generate Post Drafts | `POST /api/run/captions` | background job (~60–120 s, two agents) |
| `/posts` | Generate All Images | `POST /api/run/images` | background job (~20 s/image) |
| `/export` | Generate CSV | `POST /api/run/export` | synchronous |

Long-running phases run as FastAPI `BackgroundTasks` with an in-memory job
store. The browser polls `GET /api/jobs/{job_id}` every 3 seconds and shows
running / done / failed states. Agent calls include automatic retry with
backoff on Gemini 503 (model overloaded) errors.

The same logic powers the CLI: each phase has a `run_*.py` script, and the
web routes import the pure inner functions from those scripts — the two
entry points share one implementation.

---

## How Voice Conversation Studio Connects to MessageDNA

1. The browser records the founder's answer (`MediaRecorder`, WebM/Opus)
   and posts it to `/voice/turn/audio`.
2. `services/voice_conversation_service.py` transcribes it with
   **Speech-to-Text Chirp 3**, sends the text to the
   **Voice Conversation Agent**, and synthesises the agent's reply with
   **Text-to-Speech Chirp 3 HD**.
3. The browser plays the reply audio and displays the transcript — a true
   spoken conversation.
4. The agent works through 4 blocks (Audience & Pain, Offer &
   Transformation, Campaign Goal & Voice, Founder Positioning). After each
   block it summarises what it heard and waits for explicit confirmation
   before continuing.
5. When all 4 blocks are confirmed, the structured profile is written to
   `data/message_dna_output.json` — the foundation every other agent
   builds on.

---

## Why Multiple Agents Instead of One Big Prompt

- **Focused instructions** — each agent has one job and a tight
  instruction set, which keeps outputs reliable and parseable.
- **Predictable schemas** — every agent returns JSON inside sentinel tags
  (`<posts_complete>…</posts_complete>` etc.) that the pipeline validates
  before continuing.
- **Isolated failures** — a 503 or parse failure in one phase fails one
  job, not the whole campaign; each phase can be retried independently
  from the UI.
- **Right-sized context** — agents receive only the files they need
  (DNA + brief + moments, not the entire conversation history), which
  keeps latency and cost down.

## Tech Stack

Google ADK 2.2 · Gemini 2.5 Flash · Google Imagen 4.0 · Vertex AI ·
Cloud Speech-to-Text v2 (Chirp 3) · Cloud Text-to-Speech (Chirp 3 HD) ·
FastAPI · Jinja2 · youtube-transcript-api · Python 3.10+
