"""
schemas.py — All Pydantic data models for the deck generation pipeline.

This module is the single source of truth for every data structure that flows
between agents.  LangGraph passes a DeckState object through every node; each
agent reads what it needs from the state and writes its output back into it.

Data flow summary:
    DeckBrief (input)
        ↓  ContentAgent
    SlideSpec[] (one per slide)
        ↓  VisualAgent
    ImageRequest[] (one per visual slide)
        ↓  ImageGenerationAgent
    GeneratedImage[] (one or two per slide — OpenAI + Gemini)
        ↓  ImageReviewAgent
    SelectedImage[] (best image per slide)
        ↓  LayoutAgent
    LayoutSpec[] (design blueprint per slide)
        ↓  AssemblyAgent
    .pptx file on disk
        ↓  QAAgent
    QAResult (pass / fail + issue list)
"""
from __future__ import annotations

import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Enumerations ──────────────────────────────────────────────────────────────

class VisualType(str, Enum):
    """Categories of visuals the VisualAgent can request for a slide.

    The choice drives both the image generation prompt and the provider
    routing decision (OpenAI is preferred for photorealistic hero images;
    Gemini handles diagrams and infographics better).
    """

    HERO_IMAGE = "hero_image"                     # Full-bleed atmospheric photography
    INFOGRAPHIC = "infographic"                   # Data-driven visual summary
    PROCESS_DIAGRAM = "process_diagram"           # Step-by-step flow with stages
    ARCHITECTURE_DIAGRAM = "architecture_diagram" # Technology / system topology
    COMPARISON_TABLE = "comparison_table"         # Side-by-side option comparison
    TIMELINE = "timeline"                         # Chronological event sequence
    ROADMAP = "roadmap"                           # Phased delivery plan
    STATISTICS_VISUAL = "statistics_visual"       # Charts, metrics, KPI callouts
    EXECUTIVE_ILLUSTRATION = "executive_illustration"  # Abstract / conceptual art


class SlideType(str, Enum):
    """Structural role of a slide within the deck.

    The LayoutAgent selects a completely different design template for each
    type, so correct classification by ContentAgent is important.
    """

    TITLE = "title"                   # Opening slide: full-bleed image, overlaid title
    AGENDA = "agenda"                 # Table of contents: image right, numbered list left
    CONTENT = "content"               # Standard body slide: split-screen text+image
    SECTION_DIVIDER = "section_divider"  # Dark chapter-break slide between major sections
    CLOSING = "closing"               # Final slide: call-to-action over branded background


# ── Input model ───────────────────────────────────────────────────────────────

class DeckBrief(BaseModel):
    """Input brief describing the deck to generate.

    This is the only model populated by the caller (run_demo.py or an API
    consumer).  Every downstream agent reads from DeckState.deck_brief to
    understand the context of what they are building.

    Example JSON (see sample_briefs/ai_strategy_brief.json):
        {
          "title": "AI Strategy Roadmap",
          "client": "Rogers Communications",
          "industry": "Telecom",
          ...
        }
    """

    title: str = Field(..., description="Deck title shown on the title slide")
    client: str = Field(..., description="Client or company name used throughout the narrative")
    industry: str = Field(..., description="Industry vertical — shapes tone and terminology, e.g. BFSI, Telecom")
    audience: str = Field(..., description="Who will see this deck, e.g. C-Suite, IT Leadership, Board")
    objective: str = Field(..., description="Primary business goal this deck must achieve")
    key_messages: List[str] = Field(..., description="3–5 core insights every slide should reinforce")
    tone: str = Field(default="professional", description="Writing register: professional | executive | technical")
    slide_count_target: int = Field(default=10, ge=3, le=20, description="Desired number of slides (3–20)")
    brand: str = Field(default="default", description="Brand system to apply, e.g. 'mobilelive'")
    additional_context: Optional[str] = Field(default=None, description="Extra background the LLM should know")


# ── Pipeline intermediate models ──────────────────────────────────────────────

class SlideSpec(BaseModel):
    """Full specification for a single slide, produced by ContentAgent.

    Each SlideSpec represents one slide in the final deck.  The LLM generates
    all fields as a JSON array; ContentAgent parses and validates each item here.

    Consumed by:
      - VisualAgent     → reads visual_type and visual_description
      - LayoutAgent     → reads slide_type to select the right template
      - SlideRenderer   → reads title, eyebrow, intro_line, key_message,
                           bullets, takeaway, and speaker_notes

    ML arteka brand fields (content/agenda slides only):
      eyebrow     — 2-4 word ALL CAPS theme label rendered above the title
      intro_line  — one plain sentence below the title before the content zone
      takeaway    — one "so what" sentence for the full-width navy bar at the bottom
    Title, closing, and section_divider slides leave these three fields empty.
    """

    slide_number: int               # 1-based position in the deck
    slide_type: SlideType = SlideType.CONTENT  # Determines which LayoutSpec template is used
    title: str                      # Bold heading displayed at the top of the slide
    subtitle: Optional[str] = None  # Secondary heading (mainly used on title/closing slides)
    eyebrow: str = ""               # ALL CAPS eyebrow label (2-4 words), e.g. "BANKING PRIORITIES"
    intro_line: str = ""            # One-line intro sentence below the title
    takeaway: str = ""              # Bottom takeaway bar "so what" sentence
    key_message: str                # The ONE insight this slide must communicate
    bullets: List[str] = Field(default_factory=list)  # Supporting evidence (max 5, each ≤15 words)
    speaker_notes: str = ""         # Presenter guidance — not shown on screen
    visual_type: Optional[VisualType] = None   # Hints to VisualAgent which visual category fits
    visual_description: str = ""    # Detailed description of what the image should literally show


class ImageRequest(BaseModel):
    """A request for one image, produced by VisualAgent and consumed by ImageGenerationAgent.

    The `prompt` is the most important field — it is sent verbatim to the
    image generation API.  The `preferred_provider` field lets VisualAgent
    route diagram-style visuals to Gemini and photorealistic shots to OpenAI.
    When `preferred_provider` is None, both providers run in parallel and
    ImageReviewAgent picks the winner.
    """

    slide_number: int               # Ties this request back to its SlideSpec
    visual_type: VisualType         # High-level category (used for logging / analytics)
    prompt: str = Field(..., description="40-70 word image generation prompt using all 10 required slots")
    style_hints: List[str] = Field(default_factory=list)  # e.g. ["minimal", "dark", "blue"]
    aspect_ratio: str = "16:9"      # "16:9" for full-width, "1:1" for square insets
    preferred_provider: Optional[str] = None  # "openai" | "gemini" | None (compete)


class GeneratedImage(BaseModel):
    """The result of one image generation API call, produced by ImageGenerationAgent.

    A single slide can have up to two GeneratedImage records (one from OpenAI,
    one from Gemini) when preferred_provider is None.  The image file is saved
    to disk at `image_path` before this model is created, so the path is always
    a real filesystem path when `success` is True.
    """

    slide_number: int               # Which slide this image belongs to
    provider: str                   # "openai" or "gemini"
    prompt: str                     # The exact prompt that was submitted
    image_path: str                 # Absolute path to the saved PNG on disk
    cost_estimate: float = 0.0      # Approximate USD cost for this single image
    generation_duration_seconds: float = 0.0  # Wall-clock time for the API call
    success: bool = True            # False if the API call failed after all retries
    error: Optional[str] = None     # Exception message if success is False


class SelectedImage(BaseModel):
    """The winning image for a slide, chosen by ImageReviewAgent using GPT-4o Vision.

    Scoring uses a weighted formula:
        overall = relevance*0.35 + quality*0.25 + professionalism*0.25 + brand_alignment*0.15

    The rejected_alternatives list preserves the losing candidates for audit
    purposes (useful for debugging or future A/B testing).
    """

    slide_number: int
    selected_image_path: str        # Path to the winning PNG file
    provider: str                   # Which provider produced the winner

    # GPT-4o Vision scores (0–10 scale, all validated by Pydantic)
    relevance_score: float = Field(ge=0.0, le=10.0)      # Does it match the slide topic?
    quality_score: float = Field(ge=0.0, le=10.0)        # Is it well-composed and sharp?
    professionalism_score: float = Field(ge=0.0, le=10.0) # Would it appear in a C-suite deck?
    brand_alignment_score: float = Field(ge=0.0, le=10.0) # Does it feel on-brand?
    overall_score: float = Field(ge=0.0, le=10.0)        # Weighted composite score

    selection_reason: str           # One-sentence GPT-4o explanation for the choice
    rejected_alternatives: List[Dict[str, Any]] = Field(default_factory=list)  # Audit trail


class LayoutSpec(BaseModel):
    """Complete design blueprint for a single slide, produced by LayoutAgent.

    All positional values are in inches from the top-left corner of the
    13.33 × 7.5 inch (16:9 widescreen) canvas.  SlideRenderer and ImageRenderer
    read these values directly when calling python-pptx's add_textbox() and
    add_picture(), so changing a value here changes the physical output.

    Layout templates per SlideType:
      TITLE           — full-bleed image behind overlaid title (show_brand_header=False)
      AGENDA          — numbered list left, image right
      CONTENT         — brand header lockup top-left, bullets below, image right panel
      SECTION_DIVIDER — dark flat background, image fills right half (show_brand_header=False)
      CLOSING         — full-bleed dark background + CTA (show_brand_header=False)

    Brand header lockup (show_brand_header=True on CONTENT and AGENDA):
      SlideRenderer uses eyebrow_top_inches, the hardcoded tick-rule y (0.56"),
      title_top_inches (0.85"), and intro_top_inches (1.55") to draw the
      eyebrow → tick rule → title → intro sequence per the brand spec.
    """

    slide_number: int
    slide_type: SlideType  # Determines which template was used to produce these numbers

    # ── Image placement (inches from top-left of slide) ──────────────────────
    image_width_inches: float = 5.5
    image_height_inches: float = 4.0
    image_left_inches: float = 5.8
    image_top_inches: float = 1.5

    # ── Title text box ────────────────────────────────────────────────────────
    # On content/agenda slides the title sits at y=0.85" (below eyebrow + tick).
    # On title/closing slides it sits lower in the image scrim area.
    title_left_inches: float = 0.4
    title_top_inches: float = 0.4
    title_width_inches: float = 5.0
    title_height_inches: float = 1.0

    # ── Content / body text box ───────────────────────────────────────────────
    # On content/agenda slides this zone begins at y=2.2" per the brand grid.
    content_left_inches: float = 0.4
    content_top_inches: float = 1.6
    content_width_inches: float = 5.2
    content_height_inches: float = 4.0

    # ── Colours (hex strings, validated as strings only — not RGBColor) ─
    background_color: str = "#FEF5EE"   # Slide fill (ML arteka Warm Peach default)
    title_color: str = "#0F0F37"        # Title text colour (Navy)
    body_color: str = "#3C3C5E"         # Bullet / body text colour (Body Navy)
    accent_color: str = "#E9590C"       # Orange — item accents, tick rule, eyebrow on dark
    header_bar_color: str = "#E9590C"   # Orange tick rule under eyebrow
    eyebrow_color: str = "#434E80"      # Indigo Grey — eyebrow text on light slides (AA-safe)
    intro_color: str = "#3C3C5E"        # Body navy — intro sentence on light slides
    takeaway_bg_color: str = "#0F0F37"  # Navy — full-width takeaway bar fill
    takeaway_text_color: str = "#FEFBF8" # Off-White — takeaway bar text

    # ── Brand header lockup positions (inches from slide top-left) ─
    eyebrow_top_inches: float = 0.26    # ALL CAPS eyebrow text
    intro_top_inches: float = 1.55      # One-line intro sentence below title
    takeaway_top_inches: float = 6.45   # Full-width bottom takeaway bar
    takeaway_height_inches: float = 0.55
    show_brand_header: bool = True      # False on title/closing (full-bleed image slides)

    # ── Typography ────────────────────────────────────────────
    title_font_size: int = 20      # Points (18-20pt for content slides)
    subtitle_font_size: int = 11   # Key message / subtitle line
    body_font_size: int = 11       # Bullet points
    font_family: str = "Nunito Sans"  # ML arteka brand typeface; Arial is rendering fallback


class QAIssue(BaseModel):
    """One individual finding from the QA validation pass.

    Severity levels:
      error   — deck is unusable; triggers a retry loop via LangGraph routing
      warning — deck is suboptimal but still deliverable
      info    — informational only, does not affect pass/fail
    """

    slide_number: Optional[int] = None  # None means the issue is deck-wide, not slide-specific
    issue_type: str                     # Machine-readable code, e.g. "missing_title"
    description: str                    # Human-readable explanation shown in the QA report
    severity: str = "warning"           # "error" | "warning" | "info"


class QAResult(BaseModel):
    """Complete QA report produced by QAAgent at the end of the pipeline.

    The `passed` flag drives the conditional routing in graph.py:
      - passed=True  → the graph reaches END and the PPTX is returned
      - passed=False → the graph routes back to ContentAgent for a retry
                       (up to DeckState.max_retries times)
    """

    passed: bool              # True only if there are zero 'error'-severity issues
    slide_count: int          # Total number of slides in the deck
    issues: List[QAIssue] = Field(default_factory=list)  # All findings, any severity
    report_summary: str       # One-line human-readable summary for the console
    checked_at: str = Field(
        default_factory=lambda: datetime.datetime.utcnow().isoformat()
    )                         # UTC ISO timestamp when QA ran


# ── Root LangGraph state ──────────────────────────────────────────────────────

class DeckState(BaseModel):
    """Root state object that LangGraph threads through every node in the pipeline.

    How LangGraph uses this model:
      - The graph is created with `StateGraph(DeckState)`.
      - Each node function receives the full current state as its argument.
      - Each node returns a dict of *only the fields it wants to update*.
      - LangGraph calls `state.model_copy(update=node_output)` to produce the
        next state, so untouched fields carry forward automatically.

    Important: when a node returns a list field (e.g. `execution_logs`), it
    replaces the entire list.  Agents that append to a list must therefore
    read the current list first:  `state.execution_logs + [new_entry]`.
    """

    # ── Input (set once by run_demo.py before the graph starts) ──────────
    deck_brief: Optional[DeckBrief] = None  # The original brief; never mutated
    audience: str = ""     # Copied from brief for quick access without None checks
    industry: str = ""     # Copied from brief for quick access
    brand: str = "default" # Brand system name; affects colour palette and logo choice
    theme: str = "consulting_blue"  # Reserved for future multi-theme support

    # ── Pipeline intermediate outputs (each node replaces its own field) ──
    slide_outline: List[str] = Field(default_factory=list)  # Plain-text outline for logging
    slides: List[SlideSpec] = Field(default_factory=list)   # Set by ContentAgent
    image_requests: List[ImageRequest] = Field(default_factory=list)  # Set by VisualAgent
    generated_images: List[GeneratedImage] = Field(default_factory=list)  # Set by ImageGenerationAgent
    selected_images: List[SelectedImage] = Field(default_factory=list)    # Set by ImageReviewAgent
    layout_specs: List[LayoutSpec] = Field(default_factory=list)          # Set by LayoutAgent
    qa_results: Optional[QAResult] = None   # Set by QAAgent
    pptx_path: Optional[str] = None         # Set by AssemblyAgent (filesystem path to .pptx)

    # ── Observability / pipeline control ────────────────────────────
    execution_logs: List[str] = Field(default_factory=list)  # Chronological log of agent completions
    status: str = "initialized"   # Free-form status tag updated by each node
    retry_count: int = 0          # Incremented by QAAgent on failure; checked by route_after_qa
    max_retries: int = 2          # Maximum number of QA-triggered content retries
