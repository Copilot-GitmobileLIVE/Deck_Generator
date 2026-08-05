# Deck Generator Agent — Architecture & Flow Guide

A complete reference for understanding the multi-agent deck generation pipeline: how every piece works and how they interact to produce the final `.pptx`.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Project File Map](#2-project-file-map)
3. [Brand Skill System](#3-brand-skill-system)
4. [How LangGraph Orchestrates the Pipeline](#4-how-langgraph-orchestrates-the-pipeline)
5. [Shared State — The Backbone of the Pipeline](#5-shared-state--the-backbone-of-the-pipeline)
6. [Agent-by-Agent Breakdown](#6-agent-by-agent-breakdown)
7. [Image Providers](#7-image-providers)
8. [PPTX Rendering Layer](#8-pptx-rendering-layer)
9. [API Server & Frontend](#9-api-server--frontend)
10. [Configuration & Environment](#10-configuration--environment)
11. [End-to-End Data Flow Diagram](#11-end-to-end-data-flow-diagram)
12. [Common Q&A](#12-common-qa)

---

## 1. System Overview

The Deck Generator is a **multi-agent AI pipeline** built for ML arteka (powered by mobileLIVE). It takes a structured `DeckBrief` JSON and autonomously produces a polished, enterprise-quality PowerPoint `.pptx` file that conforms to the ML arteka brand system.

**Key technologies:**

| Layer | Technology |
|---|---|
| Agent orchestration | LangGraph `StateGraph` (directed graph, async) |
| LLM — narrative & prompts | LangChain + OpenAI `gpt-4o` |
| LLM — image generation | OpenAI `gpt-image-2` + Google `gemini-2.5-flash-preview-05-20` |
| LLM — image review | GPT-4o Vision |
| PPTX rendering | `python-pptx` |
| Brand rules source | `mlarteka-pptx.skill` (ZIP archive) |
| Configuration | `pydantic-settings` + `.env` |
| REST API (optional) | FastAPI + BackgroundTasks |
| Frontend (optional) | Single-page HTML (`deck_frontend/index.html`) |

**Pipeline at a glance (end-to-end):**

1. `DeckBrief` JSON is validated and loaded into `DeckState`
2. **ContentAgent** (GPT-4o) writes the full slide narrative — titles, bullets, brand copy
3. **VisualAgent** (GPT-4o) converts each slide spec into a detailed image generation prompt with provider routing
4. **ImageGenerationAgent** fires up to 2 × N concurrent API calls (OpenAI + Gemini, all slides in parallel)
5. **ImageReviewAgent** (GPT-4o Vision) scores every candidate image and picks the best per slide
6. **LayoutAgent** (pure logic) applies the ML arteka brand grid — pixel-perfect positions, colours, fonts
7. **AssemblyAgent** + `PPTBuilder` renders all text and images into a `.pptx` file on disk
8. **QAAgent** validates the output; if `error`-severity issues exist it retries from step 2 (up to `max_retries` times)

---

## 2. Project File Map

```
Deck_Generator_Agent/
├── run_demo.py                  ← CLI entry point
├── start_server.py              ← FastAPI server launcher
├── mlarteka-pptx.skill          ← Brand skill archive (ZIP)
├── sample_briefs/               ← Example DeckBrief JSON files
│   └── ai_strategy_brief.json
├── output/                      ← Generated .pptx files land here
│   └── images/                  ← Generated PNG images land here
├── skill_extracted/             ← Auto-extracted SKILL.md (from .skill ZIP)
│   └── mlarteka-pptx/
│       └── SKILL.md
└── deck_generator/
    ├── config.py                ← pydantic-settings singleton (all env vars)
    ├── api.py                   ← FastAPI REST server + job store
    ├── models/
    │   ├── schemas.py           ← All Pydantic models (DeckState, SlideSpec, …)
    │   └── __init__.py
    ├── workflow/
    │   └── graph.py             ← LangGraph StateGraph definition + node wiring
    ├── agents/
    │   ├── content_agent.py     ← ContentAgent (GPT-4o, narrative)
    │   ├── visual_agent.py      ← VisualAgent (GPT-4o, image prompts)
    │   ├── image_generation_agent.py ← Async parallel image generation
    │   ├── image_review_agent.py     ← ImageReviewAgent (GPT-4o Vision)
    │   ├── layout_agent.py      ← LayoutAgent (pure logic, brand grid)
    │   ├── assembly_agent.py    ← AssemblyAgent (file I/O, delegates to PPTBuilder)
    │   └── qa_agent.py          ← QAAgent (validation + retry trigger)
    ├── image_providers/
    │   ├── base.py              ← Abstract ImageProvider base class
    │   ├── openai_provider.py   ← OpenAI gpt-image-2 implementation
    │   └── gemini_provider.py   ← Google gemini-2.5-flash implementation
    ├── pptx_builder/
    │   ├── builder.py           ← PPTBuilder — top-level PPTX orchestrator
    │   ├── slide_renderer.py    ← SlideRenderer — text boxes and brand chrome
    │   └── image_renderer.py    ← ImageRenderer — PNG insertion
    └── utils/
        ├── skill_loader.py      ← BrandSkill loader + section parser
        ├── brief_validator.py   ← DeckBrief sanity checks before pipeline starts
        ├── timing.py            ← @timer context manager for logging durations
        └── logging_utils.py     ← Shared logging helpers
```

---

## 3. Brand Skill System

The ML arteka brand system is packaged as a **`.skill` archive** (`mlarteka-pptx.skill` — a ZIP file) that lives at the project root. This decouples brand rules from agent code: updating brand guidelines only requires updating the skill file, not touching any Python.

### How it works

```
mlarteka-pptx.skill  (ZIP)
    └── mlarteka-pptx/
        └── SKILL.md            ← Parsed into sections by skill_loader.py
```

`skill_loader.py` (via `get_brand_skill()`, cached with `@lru_cache`):
1. Checks for an already-extracted `skill_extracted/mlarteka-pptx/SKILL.md`
2. If missing, extracts the ZIP archive automatically
3. Parses `SKILL.md` into a dict of `## heading → body text`
4. Returns a `BrandSkill` instance that agents query by section name

### Which agents use the skill

| Agent | Sections consumed | Purpose |
|---|---|---|
| `ContentAgent` | `Fixed Slide Rules`, `Typography`, `Content Rules` | Injected into GPT-4o system prompt as authoritative brand rules |
| `VisualAgent` | `Image Placeholders` | Image prompt conventions (40-70 word structured slots) |
| `LayoutAgent` | `Layout Grid` | Confirms brand grid coordinates; logs section size |

### Key brand identifiers

| Token | Value | Used for |
|---|---|---|
| `bg_light` | `#FEF5EE` | Warm Peach — light slide background |
| `bg_dark` | `#0F0F37` | Navy — dark slide background, takeaway bar |
| `accent` | `#E9590C` | Primary Orange — tick rule, eyebrow on dark slides |
| `title_dark` | `#FEFBF8` | Off-White — headings on dark slides |
| `body_light` | `#3C3C5E` | Body Navy — body text on light slides |
| Font | `Nunito Sans` | All text elements |
| Canvas | 13.33 × 7.5 in | Standard 16:9 widescreen |

---

## 4. How LangGraph Orchestrates the Pipeline

LangGraph builds a **stateful directed graph** where every node shares one `DeckState` object. The graph is compiled once and then executed with `ainvoke()`.

### Core concepts

| Concept | Role in this system |
|---|---|
| `StateGraph` | Graph where all nodes share a single `DeckState` |
| **Node** | An `async def` (or sync) function that reads `DeckState`, does work, returns `Dict[str, Any]` of fields to update |
| **Edge** | `add_edge(A, B)` — B always runs immediately after A finishes |
| **Conditional edge** | `add_conditional_edges()` — a router function inspects state and returns a key (`"retry"` or `"finish"`) that maps to the next node |

### Pipeline topology

```
START
  │
  ▼
orchestrator ──► content ──► visual ──► image_generation
                                              │
                                        image_review
                                              │
                                           layout
                                              │
                                          assembly
                                              │
                                             qa
                                              │
                                    route_after_qa()
                                       ├── "retry"  ──► content   (loops back)
                                       └── "finish" ──► END
```

### The QA retry loop

`route_after_qa()` in `graph.py` is the only conditional edge:

| State | Decision |
|---|---|
| QA passed | `"finish"` → END; `.pptx` is delivered |
| QA failed + `retry_count < max_retries` | `"retry"` → routes back to `content`; entire pipeline re-runs |
| QA failed + retries exhausted | `"finish"` anyway; partial output delivered with a warning |

`max_retries` defaults to `2` (set in `config.py`).

---

## 5. Shared State — The Backbone of the Pipeline

All agents communicate through one **`DeckState`** Pydantic model. No agent calls another directly — they only read from and write back to this shared object. LangGraph calls `model_copy(update=returned_dict)` after every node, merging the returned fields into the canonical state.

### `DeckState` field map

| Field | Type | Populated by | Consumed by |
|---|---|---|---|
| `deck_brief` | `DeckBrief` | Caller (`run_demo.py` / API) | ContentAgent, VisualAgent |
| `slides` | `List[SlideSpec]` | ContentAgent | VisualAgent, LayoutAgent, ImageReviewAgent, AssemblyAgent, QAAgent |
| `slide_outline` | `str` | ContentAgent | Logging / debugging |
| `image_requests` | `List[ImageRequest]` | VisualAgent | ImageGenerationAgent |
| `generated_images` | `List[GeneratedImage]` | ImageGenerationAgent | ImageReviewAgent |
| `selected_images` | `List[SelectedImage]` | ImageReviewAgent | AssemblyAgent |
| `layout_specs` | `List[LayoutSpec]` | LayoutAgent | AssemblyAgent |
| `pptx_path` | `str` | AssemblyAgent | QAAgent, caller |
| `qa_results` | `QAResult` | QAAgent | `route_after_qa()`, caller |
| `retry_count` | `int` | QAAgent (increments) | `route_after_qa()` |
| `status` | `str` | Every agent | Polling / logging |
| `execution_logs` | `List[str]` | Every agent | Audit trail |

### Key data models

```
DeckBrief          → Input: title, client, industry, audience, objective,
                     key_messages, tone, slide_count_target, brand, additional_context

SlideSpec          → One slide: slide_number, slide_type, title, subtitle,
                     eyebrow, intro_line, takeaway,        ← ML arteka brand copy
                     key_message, bullets, speaker_notes,
                     visual_type, visual_description

ImageRequest       → One image job: slide_number, visual_type, prompt,
                     style_hints, aspect_ratio, preferred_provider

GeneratedImage     → One API result: slide_number, provider, prompt,
                     image_path, cost_estimate, generation_duration_seconds,
                     success, error

SelectedImage      → Winning image: slide_number, selected_image_path, provider,
                     relevance_score, quality_score, professionalism_score,
                     brand_alignment_score, overall_score,
                     selection_reason, rejected_alternatives

LayoutSpec         → Design blueprint: all x/y/width/height values in inches,
                     font sizes, hex colours, show_brand_header flag

QAIssue / QAResult → Validation findings: severity (error | warning | info),
                     descriptions, report_summary, passed flag
```

---

## 6. Agent-by-Agent Breakdown

### 6.1 Orchestrator Node
**File:** `workflow/graph.py` → `orchestrator_node()`
**Type:** Pure logic (no LLM, no API)
**Input → Output:** `DeckState` → updates `status` and `execution_logs`

Sets the initial status tag and appends the first log entry. Ensures `DeckState` has a clean starting state before any LLM is called. The brief title is extracted here for the log.

---

### 6.2 ContentAgent
**File:** `agents/content_agent.py`
**Type:** LLM — GPT-4o (`temperature=0.4`)
**Input:** `DeckBrief`
**Output:** `List[SlideSpec]`

This is the **most critical agent** — all downstream work depends on its output quality.

**Initialisation (once per agent instance):**
- Loads the `mlarteka-pptx` brand skill via `get_brand_skill()`
- Calls `skill.content_rules_prompt()` to extract the **Fixed Slide Rules**, **Typography**, and **Content Rules** sections (~12 KB of brand guidelines)
- Prepends a static McKinsey/Deloitte consultant persona introduction, then appends the live skill sections to build the full system prompt
- This means brand rule changes in `SKILL.md` flow through automatically — no code changes required

**Runtime (per pipeline invocation):**
1. Builds a two-message LangChain prompt:
   - **System:** consultant persona + full brand rules from skill
   - **Human:** all `DeckBrief` fields as template variables
2. Calls GPT-4o to produce a JSON array of slide objects
3. Parses and validates each item into a `SlideSpec`; null-coerces optional string fields to `""` so Pydantic never raises on missing optional content

**Narrative structure enforced:** Problem → Insight → Recommendation → Value → Action

**Slide sequencing enforced:** Title (dark) → Executive summary → Section dividers (dark) → Content slides → Recommendation → Closing (dark, CTA)

**Key ML arteka brand fields on every `content`/`agenda` slide:**

| Field | Rule |
|---|---|
| `eyebrow` | 2–4 word ALL CAPS theme label (e.g. `"BANKING PRIORITIES"`) rendered above the title |
| `intro_line` | One plain framing sentence below the title, before the content zone |
| `takeaway` | One "so what" sentence for the full-width navy bar at the bottom |

`title`, `closing`, and `section_divider` slides return `""` for all three fields.

---

### 6.3 VisualAgent
**File:** `agents/visual_agent.py`
**Type:** LLM — GPT-4o (`temperature=0.5`)
**Input:** `List[SlideSpec]`
**Output:** `List[ImageRequest]`

**Initialisation:**
- Loads the brand skill and extracts the **Image Placeholders** section via `skill.image_prompt_rules()`
- Injects these prompt conventions into the system message so every generated image prompt follows the 10-slot, 40–70 word structure defined in the skill

**Runtime:**
1. Filters out structural slides (`TITLE`, `AGENDA`, `CLOSING`) — these use brand-colour backgrounds, not generated images — to avoid unnecessary API spend
2. For remaining slides, sends the full `SlideSpec` (title, key message, visual type, description) to GPT-4o with a visual strategist persona
3. GPT-4o returns a structured prompt (40–70 words, 10 required slots), style hints, aspect ratio, and provider routing

**Provider routing logic:**

| `preferred_provider` | Meaning |
|---|---|
| `"openai"` | Photorealistic photography, executive portraits — send only to OpenAI |
| `"gemini"` | Infographics, diagrams, architecture visuals, abstract art — send only to Gemini |
| `null` | Both providers run in parallel; `ImageReviewAgent` picks the winner |

---

### 6.4 ImageGenerationAgent
**File:** `agents/image_generation_agent.py`
**Type:** External API calls (OpenAI + Gemini), fully async
**Input:** `List[ImageRequest]`
**Output:** `List[GeneratedImage]`

**Parallelism model:**
```
slide_1 ──► gather(openai_call, gemini_call)  ┐
slide_2 ──► gather(openai_call, gemini_call)  ├── asyncio.gather(all slides)
slide_N ──► gather(openai_call, gemini_call)  ┘
```
For N slides requesting both providers: up to **2N concurrent API calls**. Each provider wraps its call in `try/except` and returns `GeneratedImage(success=False)` on failure — a failing provider never cancels the others.

Images are saved as PNG files to `output/images/` before a `GeneratedImage` record is created. The `image_path` is always a real filesystem path when `success=True`.

---

### 6.5 ImageReviewAgent
**File:** `agents/image_review_agent.py`
**Type:** LLM — GPT-4o Vision (`temperature=0.1`)
**Input:** `List[GeneratedImage]` + `List[SlideSpec]`
**Output:** `List[SelectedImage]`

For each slide, sends every candidate image to GPT-4o Vision as a **base64-encoded data URI**, along with the slide's title and key message as context. GPT-4o scores each image on four dimensions (0–10 scale).

**Weighted scoring formula:**

```
overall = relevance      × 0.35
        + quality        × 0.25
        + professionalism× 0.25
        + brand_alignment× 0.15
```

Relevance carries the highest weight (0.35): in a C-suite consulting deck, an irrelevant image undermines the slide's argument no matter how beautiful it is.

The `rejected_alternatives` list is preserved in `SelectedImage` for audit and A/B testing purposes.

**Fallback:** If scoring fails (network error, quota), the agent assigns neutral scores (6.0/10) and continues — the pipeline never blocks on a single scoring failure.

---

### 6.6 LayoutAgent
**File:** `agents/layout_agent.py`
**Type:** Pure logic — no LLM, no API calls
**Input:** `List[SlideSpec]`
**Output:** `List[LayoutSpec]`

Deterministically assigns a design template to each slide based on `SlideType` alone. Produces a `LayoutSpec` with every positional value in inches, all font sizes, and all hex colour codes.

**ML arteka brand grid (for `content`/`agenda` slides):**
```
y 0.26"  Eyebrow text (ALL CAPS, orange accent)
y 0.58"  Tick rule (1px orange horizontal rule)
y 0.85"  Title heading (Navy)
y 1.55"  Intro line (Body Navy)
y 2.2"–6.2"  Content zone (bullets, key message)
y 6.45"  Takeaway bar (full-width Navy fill, Off-White text)
```

**Layout templates per slide type:**

| `SlideType` | Template | `show_brand_header` |
|---|---|---|
| `TITLE` | Full-bleed image (13.33 × 7.5"), title + subtitle overlaid lower-left | `False` |
| `AGENDA` | Image right (8.5" wide), numbered list left (4.5" wide) | `True` |
| `CONTENT` | Text left (6"), image right (6"), full brand header chrome | `True` |
| `SECTION_DIVIDER` | Dark Navy background, image fills right half | `False` |
| `CLOSING` | Full-bleed dark background, large white CTA text | `False` |

`show_brand_header=True` tells `SlideRenderer` to draw the eyebrow / tick rule / intro lockup. It is `False` on dark slides that use full-bleed images or flat colour backgrounds.

---

### 6.7 AssemblyAgent
**File:** `agents/assembly_agent.py`
**Type:** Pure logic / file I/O — no LLM
**Input:** `List[SlideSpec]`, `List[LayoutSpec]`, `List[SelectedImage]`
**Output:** `.pptx` file on disk + `pptx_path` in state

1. Derives a safe output filename: `<sanitised_title>_<YYYYMMDD_HHMMSS>.pptx` (timestamps prevent overwrites)
2. Converts list-based state fields to `Dict[int, ...]` keyed by `slide_number` for O(1) lookup
3. Calls `settings.ensure_dirs()` to create `output/` if it does not exist
4. Delegates entirely to `PPTBuilder.build()` which writes the final `.pptx`

---

### 6.8 QAAgent
**File:** `agents/qa_agent.py`
**Type:** Pure logic — no LLM
**Input:** Full `DeckState`
**Output:** `QAResult` (pass/fail + issue list) + incremented `retry_count` on failure

**Validation checks:**

| Category | Rule | Severity |
|---|---|---|
| Structural | Minimum 3 slides | `error` |
| Structural | Must have a `TITLE` slide | `error` |
| Structural | Must have a `CLOSING` slide | `warning` |
| Per-slide | Every slide must have a non-empty title | `error` |
| Per-slide | `CONTENT` slides must have bullet points | `warning` |
| Output | `.pptx` file must exist on disk | `error` |

**`error` severity** → increments `retry_count` and triggers a re-run (if retries remain).
**`warning`** → logged but does not block delivery.

---

## 7. Image Providers

Both providers implement the `ImageProvider` abstract base class (`image_providers/base.py`) and return the same `ImageGenerationResult` dataclass. This contract makes them fully swappable — adding a third provider (e.g. Stability AI) requires only subclassing `ImageProvider` and registering it in `ImageGenerationAgent.__init__`.

**Error contract:** Providers never raise exceptions — they always return `success=False` with an error message. This keeps `asyncio.gather()` clean and non-blocking.

| Provider | Class | Model | Best for |
|---|---|---|---|
| OpenAI | `OpenAIImageProvider` | `gpt-image-2` | Photorealistic photography, executive portraits |
| Google Gemini | `GeminiImageProvider` | `gemini-2.5-flash-preview-05-20` | Infographics, diagrams, architecture visuals, abstract art |

---

## 8. PPTX Rendering Layer

### PPTBuilder (`pptx_builder/builder.py`)

Top-level orchestrator for PPTX creation. Configures the blank `Presentation` object (16:9 canvas, 13.33 × 7.5 inches), then iterates over all slides in `slide_number` order and delegates to:

- **`SlideRenderer`** — adds all text boxes: title, eyebrow, tick rule, intro line, bullets, key message, takeaway bar, subtitle, speaker notes
- **`ImageRenderer`** — inserts the winning PNG at the exact `(left, top, width, height)` coordinates from `LayoutSpec`

### Z-order rules (critical for visual correctness)

python-pptx renders shapes in insertion order (first added = furthest back):

| Slide type | Order | Reason |
|---|---|---|
| `TITLE`, `CLOSING` (full-bleed) | Image **first**, then text | Text must appear on top of the background image |
| `CONTENT`, `AGENDA` (split-screen) | Text left, image right | Non-overlapping zones; z-order irrelevant |

### SlideRenderer brand chrome (for `show_brand_header=True` slides)

1. Eyebrow text box (ALL CAPS, orange, `y=0.26"`)
2. Tick rule (1px orange horizontal line, `y=0.58"`)
3. Title text box (`y=0.85"`)
4. Intro line text box (`y=1.55"`)
5. Content zone (bullets, key message)
6. Takeaway bar — full-width Navy rectangle at `y=6.45"` with Off-White text

---

## 9. API Server & Frontend

The pipeline can be driven by CLI (`run_demo.py`) or by a REST API + web frontend.

### REST API (`deck_generator/api.py`)

Built with **FastAPI** + **BackgroundTasks** (runs in the same asyncio event loop as the HTTP server).

| Endpoint | Method | Description |
|---|---|---|
| `/api/generate` | `POST` | Accept `DeckBrief` JSON; enqueue a background job; return `{job_id}` (HTTP 202) |
| `/api/jobs/{job_id}` | `GET` | Poll job status: `pending → running → complete | failed`; returns `download_url` + `elapsed_seconds` on success |
| `/api/download/{fname}` | `GET` | Stream the generated `.pptx` file to the browser |
| `/api/health` | `GET` | Liveness check |

**Job lifecycle:**

```
POST /api/generate
    → creates JobRecord (status=pending) in _jobs dict
    → queues _run_pipeline() as a BackgroundTask
    → returns {job_id} immediately (HTTP 202)

_run_pipeline() (background coroutine)
    → status = "running"
    → calls build_deck_graph() → graph.ainvoke(state)
    → status = "complete" + pptx_path  (on success)
    → status = "failed"  + error       (on exception)

GET /api/jobs/{job_id}
    → reads _jobs[job_id] and returns current status
```

**Job store:** In-memory `Dict[str, JobRecord]` — cleared on process restart. For production, replace with Redis/ARQ/Celery.

### Frontend (`deck_frontend/index.html`)

A single-page HTML app. Submits briefs via `POST /api/generate`, polls `GET /api/jobs/{job_id}`, and offers a download link when status is `complete`. Start via:

```bash
python start_server.py
```

---

## 10. Configuration & Environment

All configuration is centralised in `deck_generator/config.py` using `pydantic-settings`. Values are read from `.env` at startup (once, via `@lru_cache`). The singleton is accessed via `get_settings()` in every agent and provider.

### Required `.env` keys

| Key | Used by |
|---|---|
| `OPENAI_API_KEY` | ContentAgent, VisualAgent, ImageReviewAgent, OpenAIImageProvider |
| `GEMINI_API_KEY` | GeminiImageProvider |

### Full settings reference

| Key | Default | Purpose |
|---|---|---|
| `model_content` | `gpt-4o` | LLM for ContentAgent and VisualAgent |
| `model_image_openai` | `gpt-image-2` | OpenAI image generation model |
| `model_image_gemini` | `gemini-2.5-flash-preview-05-20` | Gemini image generation model |
| `model_review` | `gpt-4o` | GPT-4o Vision for image scoring |
| `model_layout` | `gpt-4o` | Reserved for future LLM-driven layout |
| `model_qa` | `gpt-4o` | Reserved for future LLM-driven QA |
| `max_retries` | `2` | Maximum QA → content retry loops |
| `content_temperature` | `0.4` | Creativity for narrative generation |
| `review_temperature` | `0.1` | Near-deterministic image scoring |
| `image_generation_timeout` | `120` | Per-image timeout in seconds |
| `output_dir` | `output` | Where `.pptx` files are saved |
| `images_dir` | `output/images` | Where generated PNGs are saved |
| `skill_file` | `mlarteka-pptx.skill` | Brand skill ZIP archive path |
| `skill_extract_dir` | `skill_extracted` | Target folder for auto-extraction |

### How to run

```bash
# CLI — uses default brief
python run_demo.py

# CLI — custom brief
python run_demo.py sample_briefs/ai_strategy_brief.json
python run_demo.py path/to/your_brief.json

# REST API + web frontend
python start_server.py
# then open http://localhost:8000 in a browser
```

---

## 11. End-to-End Data Flow Diagram

```
┌───────────────────────────────────────────────────────────────────────┐
│                       DeckBrief (Input JSON)                          │
│  title · client · industry · audience · objective · key_messages      │
│  tone · slide_count_target · brand · additional_context               │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │  validate_and_fix_brief()
                              │  DeckState initialised
                              ▼
                     ┌─────────────────┐
                     │  Orchestrator   │  Sets status, logs pipeline start
                     └────────┬────────┘
                              │
                     ┌────────▼────────┐
                     │  ContentAgent   │  GPT-4o + brand skill
                     │  (LLM)          │  → List[SlideSpec]
                     │                 │    (title, bullets, eyebrow,
                     │                 │     intro_line, takeaway, …)
                     └────────┬────────┘
                              │
                     ┌────────▼────────┐
                     │  VisualAgent    │  GPT-4o + image prompt rules
                     │  (LLM)          │  → List[ImageRequest]
                     │                 │    (40-70 word prompts,
                     │                 │     provider routing)
                     └────────┬────────┘
                              │
              ┌───────────────▼───────────────┐
              │     ImageGenerationAgent        │  asyncio.gather (2N parallel)
              │   OpenAIImageProvider           │
              │   GeminiImageProvider           │  → List[GeneratedImage]
              │   (External APIs, async)        │    (PNGs saved to disk)
              └───────────────┬───────────────┘
                              │
                     ┌────────▼────────┐
                     │ ImageReviewAgent│  GPT-4o Vision (base64 images)
                     │  (LLM + Vision) │  → List[SelectedImage]
                     │                 │    weighted score: relevance×0.35
                     │                 │    + quality×0.25
                     │                 │    + professionalism×0.25
                     │                 │    + brand_alignment×0.15
                     └────────┬────────┘
                              │
                     ┌────────▼────────┐
                     │  LayoutAgent    │  Pure logic — brand grid lookup
                     │  (No LLM)       │  → List[LayoutSpec]
                     │                 │    (inches, colours, fonts,
                     │                 │     show_brand_header flag)
                     └────────┬────────┘
                              │
                     ┌────────▼────────┐
                     │ AssemblyAgent   │  PPTBuilder
                     │  (No LLM)       │    SlideRenderer → text + chrome
                     │                 │    ImageRenderer → PNG insertion
                     │                 │  → .pptx file on disk
                     └────────┬────────┘
                              │
                     ┌────────▼────────┐
                     │    QAAgent      │  Pure validation checks
                     │    (No LLM)     │  → QAResult (pass | fail + issues)
                     └────────┬────────┘
                              │
              ┌───────────────▼───────────────┐
              │         route_after_qa()        │
              └───────────────┬───────────────┘
                   ┌──────────┴──────────┐
                   │                     │
              "retry" ↑            "finish" →
           (back to content)         END → .pptx delivered to caller
```

---

## 12. Common Q&A

**Q: Why use multiple agents instead of one big LLM call?**
Each agent is a specialist. ContentAgent writes like a consultant; VisualAgent thinks like an art director; LayoutAgent applies a deterministic design system; QAAgent validates without LLM hallucinations. Each stage can be independently tested, upgraded, or swapped without touching the others.

**Q: Why does the brand skill live in a `.skill` ZIP file instead of hardcoded strings?**
Decoupling brand rules from code. Updating typography, colour tokens, or slide rules only requires editing `SKILL.md` inside the archive — no Python changes, no deployment. The skill is loaded once at agent init via `@lru_cache` so there is no runtime overhead.

**Q: How does LangGraph know the execution order?**
Through `add_edge()` calls in `workflow/graph.py`. `add_edge("content", "visual")` means the visual node starts only after content returns. LangGraph compiles these edges into an execution plan before any `ainvoke()` call.

**Q: What is `DeckState` and why does it matter?**
It is the single Pydantic object that every agent reads from and writes to. No agent calls another directly. LangGraph merges each agent's returned dict back into the state via `model_copy()`, creating an immutable, traceable audit trail of every transformation.

**Q: Why generate images from both OpenAI and Gemini?**
Each model excels in different domains. `gpt-image-2` produces better photorealistic results; `gemini-2.5-flash` handles diagrams and abstract visuals better. By generating from both and having GPT-4o Vision select the winner, the pipeline automatically gets the best result per slide type.

**Q: How does the image scoring formula work?**
GPT-4o Vision receives the image (base64-encoded) plus the slide title and key message. It scores four dimensions (0–10): relevance (×0.35), quality (×0.25), professionalism (×0.25), brand alignment (×0.15). The image with the highest weighted composite wins.

**Q: Why does relevance carry the highest weight (0.35)?**
A C-suite audience immediately notices when an image does not match the slide's argument. A technically perfect but irrelevant image undermines the entire presentation's credibility — so relevance to the slide's message matters more than visual quality alone.

**Q: What triggers a pipeline retry?**
The QAAgent raises `error`-severity issues for: fewer than 3 slides, a missing title slide, any slide with an empty title, or the `.pptx` file not found on disk. If `retry_count < max_retries` (default 2), `route_after_qa()` sends the graph back to ContentAgent and the whole pipeline re-runs from scratch.

**Q: What happens if image generation fails for a slide?**
Providers return `GeneratedImage(success=False)` rather than raising. `asyncio.gather(return_exceptions=True)` ensures one failing provider never cancels others. If the review agent fails to score an image, it assigns neutral scores (6.0/10) so the pipeline continues.

**Q: How does LayoutAgent work without an LLM?**
Layout decisions are 100% deterministic — there is a dedicated `_<type>_layout()` method for each `SlideType` that returns hardcoded pixel positions, font sizes, and hex colour codes from the ML arteka brand palette. This eliminates LLM latency and guarantees brand consistency across every run.

**Q: What are `eyebrow`, `intro_line`, and `takeaway`?**
ML arteka brand copy elements required on every `content` and `agenda` slide:
- **eyebrow** — a 2–4 word ALL CAPS theme label rendered above the title in orange
- **intro_line** — a single framing sentence below the title that contextualises the content zone
- **takeaway** — a "so what" sentence that fills the full-width Navy bar at the bottom of the slide

**Q: How do you add a new slide type?**
Three steps: (1) add the value to `SlideType` in `models/schemas.py`, (2) add a `_<type>_layout()` method in `LayoutAgent`, (3) add the mapping to the `dispatch` dict in `LayoutAgent.run()`. No other files need to change.

**Q: How do you add a new image provider?**
Subclass `ImageProvider` in `image_providers/base.py`, implement `provider_name` and `generate_image()`, then register it in `ImageGenerationAgent.__init__`. The rest of the pipeline requires no changes.

**Q: Where is the final PowerPoint saved?**
In `output/` (configurable via `OUTPUT_DIR` in `.env`), with a filename of the form `<deck_title>_<YYYYMMDD_HHMMSS>.pptx`. Timestamps prevent accidental overwrites when the pipeline runs multiple times for the same brief.

**Q: What is the difference between `run_demo.py` and `start_server.py`?**
`run_demo.py` is a CLI script: reads a brief JSON, runs the pipeline synchronously in a single process, and prints a formatted Rich summary. `start_server.py` launches a FastAPI server that accepts briefs via HTTP, runs each generation job as a background coroutine, and exposes a polling API so the web frontend (`deck_frontend/index.html`) can track progress and download the result.

**Q: How is brief input validated before the pipeline starts?**
`run_demo.py` and the API both pass the `DeckBrief` through `brief_validator.py` (`validate_and_fix_brief()`), which performs sanity checks (e.g. slide count within bounds, required fields non-empty) and applies auto-fixes where possible before handing off to LangGraph.


