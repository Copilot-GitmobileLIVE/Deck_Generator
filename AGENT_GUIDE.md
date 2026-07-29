# Deck Generator Agent — Architecture & Presentation Guide

A deep-dive reference for understanding, explaining, and answering questions about the multi-agent deck generation pipeline.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [How LangGraph Orchestrates the Pipeline](#2-how-langgraph-orchestrates-the-pipeline)
3. [Shared State — The Backbone of the Pipeline](#3-shared-state--the-backbone-of-the-pipeline)
4. [Agent-by-Agent Breakdown](#4-agent-by-agent-breakdown)
5. [Image Providers](#5-image-providers)
6. [PPTX Rendering Layer](#6-pptx-rendering-layer)
7. [Configuration & Environment](#7-configuration--environment)
8. [Data Flow Diagram](#8-data-flow-diagram)
9. [Common Q&A for Presentations](#9-common-qa-for-presentations)

---

## 1. System Overview

The Deck Generator is a **multi-agent AI pipeline** that takes a structured brief (title, client, industry, audience, key messages) and autonomously produces a polished, enterprise-quality PowerPoint `.pptx` file.

**Key technologies:**
| Layer | Technology |
|---|---|
| Agent orchestration | [LangGraph](https://github.com/langchain-ai/langgraph) `StateGraph` |
| LLM calls | LangChain + OpenAI `gpt-4o` |
| Image generation | OpenAI `gpt-image-2` + Google `gemini-2.5-flash-preview` |
| Image review | GPT-4o Vision |
| PPTX rendering | `python-pptx` |
| Config management | `pydantic-settings` |

**What the pipeline does, end-to-end:**
1. Reads a `DeckBrief` JSON (the "brief")
2. A consultant-style LLM writes the full slide narrative
3. A visual strategist LLM crafts detailed image generation prompts
4. Two image providers (OpenAI + Gemini) generate images **in parallel**
5. GPT-4o Vision scores and selects the best image per slide
6. A deterministic layout engine maps slide types to pixel-perfect templates
7. `python-pptx` assembles the final `.pptx` file
8. A QA agent validates the output and can **retry** the full pipeline if issues are found

---

## 2. How LangGraph Orchestrates the Pipeline

LangGraph is a framework for building stateful, multi-step agent workflows as **directed graphs**.

### Core Concepts

| Concept | What it means here |
|---|---|
| `StateGraph` | The graph whose every node shares one `DeckState` object |
| **Node** | A Python function (sync or async) that reads `DeckState`, does work, and returns a dict of fields to update |
| **Edge** | A directed link — `A → B` means B always runs after A finishes |
| **Conditional Edge** | A function inspects the state and returns a string key that maps to the next node (used for the QA retry loop) |

### Pipeline Topology

```
orchestrator → content → visual → image_generation
    → image_review → layout → assembly → qa
    → route_after_qa()
           ├── "retry"  → content   (loops back on QA failure)
           └── "finish" → END
```

### The QA Retry Loop

After `assembly`, the `qa` node validates the deck. The `route_after_qa()` function then decides:
- **QA passed** → `"finish"` → pipeline ends, PPTX is delivered
- **QA failed + retries available** → `"retry"` → routes back to `content`, the whole pipeline re-runs
- **QA failed + retries exhausted** → `"finish"` anyway (partial output is delivered with a warning)

`max_retries` is set to `2` by default in `config.py`.

---

## 3. Shared State — The Backbone of the Pipeline

All agents communicate through a single **`DeckState`** Pydantic object. No agent calls another directly — they only read from and write to this shared state. LangGraph merges each agent's returned dict back into the state after every node.

### State Fields Summary

| Field | Type | Populated by |
|---|---|---|
| `deck_brief` | `DeckBrief` | Caller (`run_demo.py`) |
| `slides` | `List[SlideSpec]` | ContentAgent |
| `slide_outline` | `str` | ContentAgent |
| `image_requests` | `List[ImageRequest]` | VisualAgent |
| `generated_images` | `List[GeneratedImage]` | ImageGenerationAgent |
| `selected_images` | `List[SelectedImage]` | ImageReviewAgent |
| `layout_specs` | `List[LayoutSpec]` | LayoutAgent |
| `pptx_path` | `str` | AssemblyAgent |
| `qa_results` | `QAResult` | QAAgent |
| `retry_count` | `int` | QAAgent (incremented on failure) |
| `status` | `str` | Every agent (progress tag) |
| `execution_logs` | `List[str]` | Every agent (audit trail) |

### Key Data Models

```
DeckBrief          → Input. The brief the user provides.
SlideSpec          → One slide: title, bullets, key_message, speaker_notes, visual_type
ImageRequest       → One image request: prompt, provider preference, aspect_ratio
GeneratedImage     → One API result: provider, path on disk, cost, success flag
SelectedImage      → The winning image: scores (relevance, quality, professionalism, brand)
LayoutSpec         → Design blueprint: all x/y/width/height/color values in inches
QAIssue / QAResult → Validation findings: severity (error | warning | info), descriptions
```

---

## 4. Agent-by-Agent Breakdown

### 4.1 Orchestrator Node
**File:** `workflow/graph.py` → `orchestrator_node()`  
**Type:** Pure logic (no LLM)  
**What it does:** Sets the initial `status` tag and appends the first log entry. It is a clean entry point that ensures DeckState has a valid starting state before any LLM is called.

---

### 4.2 ContentAgent
**File:** `agents/content_agent.py`  
**Type:** LLM (GPT-4o, temperature 0.4)  
**Input:** `DeckBrief`  
**Output:** `List[SlideSpec]`

This is the **most critical agent** — everything downstream depends on the quality of its output.

**What it does:**
- Builds a two-message LangChain prompt: a **system** message that sets a McKinsey/Deloitte consultant persona, and a **human** message that injects all brief fields
- Instructs the LLM to structure the narrative as: **Problem → Insight → Recommendation → Value → Action**
- The LLM returns a JSON array; ContentAgent parses and validates it into `SlideSpec` objects
- Each `SlideSpec` includes: title, slide type, key message, up to 5 bullets (≤10 words each), speaker notes, and a visual recommendation

**Key design rule:** One slide = one clear message. No slide does two jobs.

---

### 4.3 VisualAgent
**File:** `agents/visual_agent.py`  
**Type:** LLM (GPT-4o, temperature 0.5)  
**Input:** `List[SlideSpec]`  
**Output:** `List[ImageRequest]`

**What it does:**
- Filters out structural slides (Title, Agenda, Closing) to avoid unnecessary API spend — these use brand-colour backgrounds instead
- For remaining slides, sends the full `SlideSpec` data to GPT-4o with a visual strategist persona
- The LLM produces a detailed 100–200 word image generation prompt per slide, style hints, aspect ratio, and **provider routing** (`"openai"` / `"gemini"` / `null`)

**Provider routing logic:**
- `"openai"` → photorealistic photography, executive portraits
- `"gemini"` → infographics, diagrams, architecture visuals, abstract art
- `null` → both providers run, best image wins in review

---

### 4.4 ImageGenerationAgent
**File:** `agents/image_generation_agent.py`  
**Type:** External API calls (OpenAI + Gemini), fully async  
**Input:** `List[ImageRequest]`  
**Output:** `List[GeneratedImage]`

**What it does:**
- For each `ImageRequest`, fires up to **two concurrent API calls** (one per provider) using `asyncio.gather()`
- All slides are also gathered in parallel — for N slides and 2 providers: up to **2N concurrent API calls**
- Each provider wraps its call in try/except and returns a `GeneratedImage` with `success=False` on failure — one failing provider never cancels the other
- Images are saved as PNG files to `output/images/` on disk

**Parallelism topology:**
```
slide_1 → gather(openai_call, gemini_call)  ┐
slide_2 → gather(openai_call, gemini_call)  ├── asyncio.gather(all slides)
slide_N → gather(openai_call, gemini_call)  ┘
```

---

### 4.5 ImageReviewAgent
**File:** `agents/image_review_agent.py`  
**Type:** LLM (GPT-4o Vision, temperature 0.1)  
**Input:** `List[GeneratedImage]` + `List[SlideSpec]`  
**Output:** `List[SelectedImage]`

**What it does:**
- For each slide, sends every candidate image to **GPT-4o Vision** as a base64-encoded data URI
- GPT-4o scores the image on 4 dimensions (0–10 scale) with context from the slide's title and key message

**Weighted scoring formula:**
```
overall = relevance × 0.35
        + quality × 0.25
        + professionalism × 0.25
        + brand_alignment × 0.15
```

Relevance gets the highest weight (0.35) because an irrelevant image — no matter how beautiful — undermines a consulting slide's argument.

**Fallback:** If scoring fails (network error, quota), the agent assigns neutral scores (6.0/10) and continues — the pipeline never blocks on a single scoring failure.

---

### 4.6 LayoutAgent
**File:** `agents/layout_agent.py`  
**Type:** Pure logic (no LLM, no API calls)  
**Input:** `List[SlideSpec]`  
**Output:** `List[LayoutSpec]`

**What it does:**
- Deterministically assigns a design template to each slide based on its `SlideType`
- Produces a `LayoutSpec` with every positioning value in inches, font sizes, and hex colours
- Uses the **mobileLIVE consulting colour palette** (`#0A1628` dark navy, `#0057B8` accent blue, `#F6C94A` highlight gold)

**Layout templates per slide type:**
| SlideType | Layout |
|---|---|
| `TITLE` | Full-bleed image, title overlaid in lower-left third |
| `AGENDA` | Image right (4.8"), numbered list left |
| `CONTENT` | Split-screen: text left (6"), image right (6") |
| `SECTION_DIVIDER` | Dark background, image fills right half |
| `CLOSING` | Full-bleed branded background, large white CTA |

---

### 4.7 AssemblyAgent
**File:** `agents/assembly_agent.py`  
**Type:** Pure logic / file I/O (no LLM)  
**Input:** `List[SlideSpec]`, `List[LayoutSpec]`, `List[SelectedImage]`  
**Output:** `.pptx` file on disk + `pptx_path` in state

**What it does:**
- Derives a safe output filename: `<sanitised_title>_<YYYYMMDD_HHMMSS>.pptx` (timestamps prevent overwrites)
- Converts list-based state fields to dicts keyed by `slide_number` for O(1) lookup
- Delegates to `PPTBuilder.build()` which writes the final file
- No LLM calls — pure file I/O

---

### 4.8 QAAgent
**File:** `agents/qa_agent.py`  
**Type:** Pure logic (no LLM)  
**Input:** Full `DeckState`  
**Output:** `QAResult` (pass/fail + issue list) + incremented `retry_count` on failure

**Validation checks:**

| Category | Rule | Severity |
|---|---|---|
| Structural | Minimum 3 slides | error |
| Structural | Must have a TITLE slide | error |
| Structural | Must have a CLOSING slide | warning |
| Per-slide | Every slide must have a non-empty title | error |
| Per-slide | CONTENT slides must have bullet points | warning |
| Output | `.pptx` file must exist on disk | error |

**`error` severity** triggers a retry (if retries remain). **`warning`** is noted but does not block delivery.

---

## 5. Image Providers

Both providers implement the `ImageProvider` abstract base class (`image_providers/base.py`), returning the same `ImageGenerationResult` dataclass. This makes the providers swappable — adding a third provider (e.g. Stability AI) only requires subclassing `ImageProvider`.

| Provider | Model | Best for |
|---|---|---|
| `OpenAIImageProvider` | `gpt-image-2` | Photorealistic photography, executive portraits |
| `GeminiImageProvider` | `gemini-2.5-flash-preview-05-20` | Infographics, diagrams, architecture visuals, abstract art |

**Error contract:** Providers never raise exceptions — they always return `success=False` with an error message. This keeps `asyncio.gather()` clean.

---

## 6. PPTX Rendering Layer

### PPTBuilder (`pptx_builder/builder.py`)
Top-level orchestrator for PPTX creation. Iterates over all slides in order and delegates to:
- `SlideRenderer` — adds text boxes (title, bullets, key message, speaker notes)
- `ImageRenderer` — inserts PNG files at the exact coordinates from `LayoutSpec`

**Z-order matters:**
- For full-bleed slides (TITLE, CLOSING): image is added **first** (behind), text added on top
- For split-screen slides (CONTENT): text and image are in separate screen regions, z-order irrelevant

**Canvas:** 13.33 × 7.5 inches (standard 16:9 widescreen)

---

## 7. Configuration & Environment

All configuration lives in `deck_generator/config.py` via `pydantic-settings`. Values are read from `.env` at startup (once, via `@lru_cache`).

### Required `.env` Keys

| Key | Used by |
|---|---|
| `OPENAI_API_KEY` | ContentAgent, VisualAgent, ImageReviewAgent, OpenAIImageProvider |
| `GEMINI_API_KEY` | GeminiImageProvider |

### Key Tuning Parameters

| Key | Default | Purpose |
|---|---|---|
| `model_content` | `gpt-4o` | LLM for ContentAgent + VisualAgent |
| `model_image_openai` | `gpt-image-2` | OpenAI image model |
| `model_image_gemini` | `gemini-2.5-flash-preview-05-20` | Gemini image model |
| `model_review` | `gpt-4o` | GPT-4o Vision for image scoring |
| `max_retries` | `2` | QA retry limit |
| `content_temperature` | `0.4` | Creativity for narrative generation |
| `review_temperature` | `0.1` | Near-deterministic image scoring |
| `output_dir` | `output/` | Where `.pptx` files are saved |
| `images_dir` | `output/images/` | Where generated PNGs are saved |

### How to Run

```bash
# Default brief
python run_demo.py

# Custom brief
python run_demo.py sample_briefs/ai_strategy_brief.json
python run_demo.py path/to/your_brief.json
```

---

## 8. Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         DeckBrief (Input)                       │
│  title, client, industry, audience, objective, key_messages     │
└────────────────────────────┬────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │  Orchestrator   │  Sets status, logs pipeline start
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  ContentAgent   │  GPT-4o → List[SlideSpec]
                    │  (LLM)          │  Slide titles, bullets, speaker notes
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  VisualAgent    │  GPT-4o → List[ImageRequest]
                    │  (LLM)          │  100-200 word prompts + provider routing
                    └────────┬────────┘
                             │
              ┌──────────────▼──────────────┐
              │   ImageGenerationAgent       │  Async parallel API calls
              │   (OpenAI + Gemini)          │  → List[GeneratedImage] (PNGs on disk)
              └──────────────┬──────────────┘
                             │
                    ┌────────▼────────┐
                    │ ImageReviewAgent │  GPT-4o Vision → List[SelectedImage]
                    │ (LLM + Vision)   │  Scores: relevance, quality, professionalism
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  LayoutAgent    │  Pure logic → List[LayoutSpec]
                    │  (No LLM)       │  Pixels, colours, positions per slide type
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ AssemblyAgent   │  python-pptx → .pptx file on disk
                    │ (No LLM)        │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │    QAAgent      │  Pure logic → QAResult (pass/fail)
                    │    (No LLM)     │
                    └────────┬────────┘
                             │
              ┌──────────────▼──────────────┐
              │       route_after_qa()       │
              └──────────────┬──────────────┘
                   ┌─────────┴─────────┐
                   │                   │
             "retry" ↑           "finish" →
          (back to content)        END → .pptx delivered
```

---

## 9. Common Q&A for Presentations

**Q: Why use multiple agents instead of one big LLM call?**  
A: Separation of concerns — each agent is a specialist. ContentAgent writes like a consultant; VisualAgent thinks like an art director; LayoutAgent applies a deterministic design system; QAAgent validates without introducing LLM hallucinations. Each stage can be independently improved, tested, or swapped out.

**Q: How does LangGraph know what order to run the agents?**  
A: Through edges defined in `workflow/graph.py`. `add_edge("content", "visual")` means the visual node only starts after content finishes. LangGraph compiles these edges into an execution plan before any run starts.

**Q: What is `DeckState` and why is it important?**  
A: It is the single Pydantic object that all agents share. No agent calls another directly — they only read from and write to this state. LangGraph merges each agent's returned dict back into the state, creating an immutable, traceable audit trail of every transformation.

**Q: Why generate images from both OpenAI and Gemini?**  
A: Each model has different strengths. OpenAI (`gpt-image-2`) produces better photorealistic results; Gemini handles diagrams and abstract visuals better. By generating from both and having GPT-4o Vision select the winner, the pipeline gets the best of each provider per slide type automatically.

**Q: How does the image scoring work?**  
A: GPT-4o Vision receives the image (base64-encoded) plus the slide's title and key message as context. It scores four dimensions: relevance (0.35 weight), quality (0.25), professionalism (0.25), and brand alignment (0.15). The winner is the image with the highest weighted composite score.

**Q: Why does relevance have the highest scoring weight (0.35)?**  
A: In a consulting deck, a C-suite audience will immediately notice if an image does not match the slide's argument. A technically perfect but irrelevant image undermines the credibility of the entire presentation — so relevance to the slide's message matters more than visual quality alone.

**Q: What happens if image generation fails?**  
A: Providers never raise exceptions — they return `success=False` with an error message. `asyncio.gather(return_exceptions=True)` ensures one failing provider never cancels the other. If the review agent fails, it assigns neutral scores (6.0/10) so the pipeline continues.

**Q: What triggers a retry?**  
A: The QAAgent raises `error`-severity issues for: fewer than 3 slides, missing title slide, any slide with no title, or the `.pptx` file not being found on disk. If `retry_count < max_retries` (default 2), LangGraph routes back to ContentAgent and the entire pipeline re-runs from scratch.

**Q: How does LayoutAgent work without an LLM?**  
A: Layout decisions are 100% deterministic, based only on `SlideType`. There is a dedicated `_<type>_layout()` method for each slide type (title, agenda, content, section_divider, closing) that returns hardcoded pixel positions, font sizes, and hex colour codes. This avoids LLM latency and ensures brand consistency.

**Q: How do you add a new slide type?**  
A: Three steps — add the value to the `SlideType` enum in `models/schemas.py`, add a `_<type>_layout()` method in `LayoutAgent`, and add it to the dispatch dict in `LayoutAgent.run()`. No other files need to change.

**Q: How do you add a new image provider (e.g. Stability AI)?**  
A: Subclass `ImageProvider` in `image_providers/base.py`, implement `provider_name` and `generate_image()`, then register it in `ImageGenerationAgent.__init__`. The rest of the pipeline requires no changes.

**Q: Where is the final PowerPoint saved?**  
A: In the `output/` directory (configurable via `OUTPUT_DIR` in `.env`), with a filename in the format `<deck_title>_<YYYYMMDD_HHMMSS>.pptx`. Timestamps prevent accidental overwrites when the pipeline runs multiple times for the same brief.

**Q: How do you control the tone or style of the content?**  
A: Set the `tone` field in the `DeckBrief` (e.g. `"professional"`, `"executive"`, `"technical"`). The `content_temperature` setting in `.env` (default 0.4) controls creativity — lower = more deterministic, higher = more varied.

**Q: What is the entry point?**  
A: `run_demo.py`. It reads a brief JSON, creates a `DeckState`, calls `build_deck_graph()` to compile the LangGraph, and runs it with `graph.ainvoke(state)`. It prints a rich formatted summary when the pipeline completes.
