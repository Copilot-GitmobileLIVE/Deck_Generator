"""
visual_agent.py — Visual Strategist Agent

Responsibility:
    Translate each SlideSpec into a precise ImageRequest that tells the image
    generation providers exactly what picture to create and how.

Key decisions made here:
    - Which visual_type fits each slide (e.g. 'roadmap' vs 'hero_image')
    - A 40-70 word prompt (10 mandatory slots) describing the desired image
    - Which provider should generate it (OpenAI for photos, Gemini for diagrams)
    - What aspect ratio to use (16:9 full-width vs 1:1 inset)

Prompt quality:
    The system prompt is assembled at init time from the mlarteka-pptx brand
    skill's Visual Storytelling section, which defines the visual narrative
    mandate and all 10 required prompt slots (subject, setting, people, shot,
    lens, lighting, mood, text-safe region, quality, negatives).  This keeps
    prompt conventions in sync with the skill without hardcoding them here.

Cost optimisation:
    Title, Agenda, and Closing slides are skipped — they use brand-colour
    backgrounds or full-bleed narrative images handled by the layout templates.
"""
from __future__ import annotations

import json
import logging
from typing import List

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from deck_generator.config import get_settings
from deck_generator.models import DeckState, ImageRequest, SlideSpec, SlideType
from deck_generator.utils.skill_loader import get_brand_skill
from deck_generator.utils.timing import timer

logger = logging.getLogger("deck_generator.visual_agent")

# Static preamble; the authoritative Visual Storytelling section is appended from the skill.
_SYSTEM_BASE = """You are a visual strategist for ML arteka (powered by mobileLIVE) consulting decks.

Your role: translate slide content into precise, high-quality image generation prompts that serve as mandatory narrative elements.

VISUAL STORYTELLING MANDATE:
- Visuals are NOT decorative. Every image must communicate a meaningful part of the slide's insight.
- The visual, title, key_message, bullets, and takeaway must work together as one story.
- Every content slide should be understandable at a glance from title + visual alone.
- Prefer visuals that reveal the "why" or "so what" — not just the subject matter.
- visual_description must specify: key entities, relationships, flow of information, metrics, comparisons, emphasis areas, and focal points.
- NEVER produce generic stock descriptions, abstract filler, or images that merely repeat the slide title.

PROMPT QUALITY RULES — TWO DISTINCT TRACKS:

TRACK A — PHOTOGRAPHIC VISUALS (hero_image, executive_illustration):
- Use real enterprise, operational, and human imagery. NEVER generic AI stock.
- Write prompts using all 10 slots: subject/action, setting, people, shot, lens, lighting, mood, text-safe region, quality, negatives.
- Slot 10 ends: "No text, no logos, no watermarks, no neon or sci-fi glow, no staged stock cliche."
- Be specific enough to brief a professional photographer. 40-70 words.

TRACK B — INFORMATIONAL VISUALS (infographic, process_diagram, architecture_diagram, comparison_table, timeline, roadmap, statistics_visual):
- These visuals MUST contain the slide's actual information. Text labels ARE REQUIRED and must be legible.
- "No text" rules DO NOT apply to these types. The entire value of these visuals is the labelled, structured information they show.
- Name every element that should appear: each stage/node/step label, metric value, column header, relationship arrow label, and the main conclusion.
- Style: clean flat-design vector illustration, ML arteka palette (Navy #0F0F37 backgrounds, Orange #E9590C for emphasis, Off-White #FEFBF8 text, Warm Peach #FEF5EE light background).
- The key insight or conclusion node must be visually emphasised (orange fill, bold label, or larger text).
- preferred_provider must be "gemini" for all Track B types — Gemini produces far better structured diagrams.
- Prompt ends with: "Clean flat-design vector illustration, legible labels, ML arteka navy/orange/peach palette, no stock photography."
- 50-100 words.

CONSULTING-GRADE VISUAL STANDARDS (Track B — always apply):
- Produce diagrams that look like McKinsey, BCG, Bain, or Deloitte presentation visuals. Dense, labelled, and information-rich.
- Every chart must include an insight statement (what the data concludes, not just what it shows). Embed this as an orange callout label on the diagram.
- NEVER produce sparse, generic, or template-looking diagrams. Every element must carry the slide's actual content.
- Preferred diagram types in order: assessment matrix → RACI chart → heatmap → maturity model → stage-gate process → roadmap → 3-layer architecture.
- Preferred formats for specific content:
  * Architecture/ecosystem: 3-layer box structure (AI/Application layer → Integration/API layer → Data/Infrastructure layer), named components, labeled directional arrows.
  * RACI charts: 4-column table (Responsible=dark-red, Accountable=orange, Consulted=navy, Informed=grey), all cells filled, activity rows on left spine.
  * Maturity models: 5-column horizontal grid (Initial→Managed→Defined→Quantified→Optimising), named capability rows, current-state column orange-outlined, target-state column dark-orange filled, each cell contains 2-4 word description.
  * Heatmaps: 5-level colour gradient (green=low risk→yellow→orange→red→dark-red=critical), all cells labeled with risk name and score, legend on right.
  * Assessment scorecards: row-per-dimension table, columns = Metric | Score | Threshold | Status | Owner, status cells colour-coded (green=pass, orange=warning, red=fail).
  * Governance frameworks: hierarchy chart with named roles (Board / Executive / Operations), accountability arrows, control labels at each node, RACI assignment per level.
  * Transformation roadmaps: horizontal timeline divided into phases (Q1–Q4 or Year 1–3), each phase contains named deliverables, dependencies shown as arrows, outcome callouts in orange.
  * Executive dashboards: grid of KPI metric cards, each with metric name, large current value, trend arrow, and target benchmark.
  * Capability maps: grid of named capability cells organised by domain, maturity scores per cell.
  * Stage-gate process flows: left-to-right swim lane, named stages with gate criteria, decision diamonds, outcome labels, current-stage highlighted in orange.
- Use consistent visual encoding: one colour per category, identical font size across sibling elements, labels on every element, zero empty areas.

Provider routing:
- "openai"  → photorealistic people, offices, real-world business environments
- "gemini"  → infographics, process/architecture diagrams, conceptual visuals
- null      → both providers run; ImageReviewAgent picks the winner

The authoritative ML arteka visual storytelling guidelines follow. Apply every convention exactly:
"""

_HUMAN = """Generate image prompts for the following slides:

{slides_json}

Return a JSON ARRAY. Each element must have:
  slide_number       — integer matching the slide
  visual_type        — one of: hero_image, infographic, process_diagram, architecture_diagram,
                       comparison_table, timeline, roadmap, statistics_visual, executive_illustration
  prompt             — detailed generation prompt following the TRACK rules in the system message:
                       TRACK A (hero_image, executive_illustration): 40-70 word photographic prompt
                         using the 10-slot convention. Ends with "No text, no logos, no watermarks..."
                       TRACK B (infographic, process_diagram, architecture_diagram, comparison_table,
                         timeline, roadmap, statistics_visual): 50-100 word informational prompt that
                         names every label, metric, step, node, column header, and relationship that
                         must appear in the visual. Specifies layout type (e.g. horizontal flow,
                         radial diagram, 2x2 grid, vertical timeline), the ML arteka colour palette,
                         and ends with "Clean flat-design vector illustration, legible labels, no stock photography."
  style_hints        — array of 3-5 style keywords, e.g. ["flat vector", "navy palette", "orange accent"]
  aspect_ratio       — "16:9" for full-width panels, "1:1" for square insets
  preferred_provider — "openai" for Track A photographic types, "gemini" for ALL Track B informational types

IMPORTANT: Return ONLY valid JSON. No markdown fences. No surrounding text.
"""


class VisualAgent:
    """Determines visual strategy and crafts image prompts for each slide.

    Loads the mlarteka-pptx brand skill at init time and injects the
    authoritative Visual Storytelling section into the system prompt so image
    prompts always follow the current skill conventions and narrative mandate.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._llm = ChatOpenAI(
            model=s.model_content,
            temperature=0.5,
            api_key=s.openai_api_key,
        )
        # Build system prompt: base intro + live skill Visual Storytelling section.
        skill = get_brand_skill()
        image_rules = skill.image_prompt_rules()
        self._system = _SYSTEM_BASE + image_rules
        logger.info(
            "VisualAgent: loaded brand skill from %s (%d chars of visual storytelling rules)",
            skill.skill_path.name, len(image_rules),
        )

    def _strip_fences(self, raw: str) -> str:
        """Strip markdown code fences from LLM output (same pattern as ContentAgent)."""
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = lines[1:] if lines[0].startswith("```") else lines
            lines = lines[:-1] if lines and lines[-1].strip() == "```" else lines
            raw = "\n".join(lines).strip()
        return raw

    async def run(self, state: DeckState) -> dict:
        """Generate image requests for all visual slides and return a state update.

        Args:
            state: Must have `slides` populated by ContentAgent.

        Returns:
            Dict with `image_requests` (List[ImageRequest]) and updated status/logs.
        """
        slides = state.slides
        if not slides:
            raise ValueError("No slides in state; run ContentAgent first")

        # Exclude chrome slides and image-free layouts (dense_consulting, stat_band)
        _NO_IMAGE = {"dense_consulting", "stat_band"}
        visual_slides = [
            s for s in slides
            if s.slide_type not in (SlideType.TITLE, SlideType.AGENDA, SlideType.CLOSING)
            and getattr(s, "layout_variant", "") not in _NO_IMAGE
        ]

        logger.info("VisualAgent: building prompts for %d slides", len(visual_slides))

        # Serialise SlideSpec objects to JSON so the LLM can read the full slide data.
        # mode="json" ensures enums are serialised as their .value strings.
        slides_json = json.dumps(
            [s.model_dump(mode="json") for s in visual_slides],
            indent=2,
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", self._system),
            ("human", _HUMAN),
        ])
        chain = prompt | self._llm

        with timer("VisualAgent.run", logger):
            response = await chain.ainvoke({"slides_json": slides_json})

        raw = self._strip_fences(response.content)

        try:
            data = json.loads(raw)
            # Handle accidental dict wrapping, e.g. {"image_requests": [...]}
            if isinstance(data, dict) and "image_requests" in data:
                data = data["image_requests"]
            # Convert string "null" to Python None for optional fields.
            for item in data:
                if item.get("preferred_provider") == "null":
                    item["preferred_provider"] = None
            requests: List[ImageRequest] = [ImageRequest(**r) for r in data]
        except Exception as exc:
            logger.error("VisualAgent: JSON parse error — %s", exc)
            raise ValueError(f"VisualAgent failed to parse LLM response: {exc}") from exc

        log_entry = f"VisualAgent: {len(requests)} image requests created"
        logger.info(log_entry)

        return {
            "image_requests": requests,
            "status": "visual_strategy_complete",
            "execution_logs": state.execution_logs + [log_entry],
        }
