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
    skill's Image Placeholders section, which defines all 10 required prompt
    slots (subject, setting, people, shot, lens, lighting, mood, text-safe
    region, quality, negatives).  This keeps prompt conventions in sync with
    the skill without hardcoding them here.

Cost optimisation:
    Title, Agenda, and Closing slides are skipped — they use flat brand-colour
    backgrounds or full-bleed image placeholders handled separately.
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

# Static preamble; the authoritative Image Placeholders section is appended from the skill.
_SYSTEM_BASE = """You are a visual strategist for ML arteka (powered by mobileLIVE) consulting decks.

Your role: translate slide content into precise, high-quality image generation prompts.

CRITICAL RULES:
- Always use real enterprise, operational, and human imagery. NEVER generic AI stock.
- Write prompts using all 10 slots: subject/action, setting, people, shot, lens, lighting, mood, text-safe region, quality, negatives.
- Slot 10 always ends: "No text, no logos, no watermarks, no neon or sci-fi glow, no staged stock cliche."

Provider routing:
- "openai"  → photorealistic people, offices, real-world business environments
- "gemini"  → infographics, process/architecture diagrams, conceptual visuals
- null      → both providers run; ImageReviewAgent picks the winner

The authoritative ML arteka image guidelines follow. Apply every convention exactly:
"""

_HUMAN = """Generate image prompts for the following slides:

{slides_json}

Return a JSON ARRAY. Each element must have:
  slide_number       — integer matching the slide
  visual_type        — one of: hero_image, infographic, process_diagram, architecture_diagram,
                       comparison_table, timeline, roadmap, statistics_visual, executive_illustration
  prompt             — detailed prompt using all 10 slots from the system message (40-70 words)
  style_hints        — array of 3-5 style keywords, e.g. ["editorial", "warm highlights", "navy palette"]
  aspect_ratio       — "16:9" for full-width panels, "1:1" for square insets
  preferred_provider — "openai", "gemini", or null

IMPORTANT: Return ONLY valid JSON. No markdown fences. No surrounding text.
"""


class VisualAgent:
    """Determines visual strategy and crafts image prompts for each slide.

    Loads the mlarteka-pptx brand skill at init time and injects the
    authoritative Image Placeholders section into the system prompt so image
    prompts always follow the current skill conventions.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._llm = ChatOpenAI(
            model=s.model_content,
            temperature=0.5,
            api_key=s.openai_api_key,
        )
        # Build system prompt: base intro + live skill Image Placeholders section.
        skill = get_brand_skill()
        image_rules = skill.image_prompt_rules()
        self._system = _SYSTEM_BASE + image_rules
        logger.info(
            "VisualAgent: loaded brand skill from %s (%d chars of image rules)",
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

        # Exclude chrome slides (title, agenda, closing) — they use brand colours
        # instead of custom images, which avoids unnecessary API spend.
        visual_slides = [
            s for s in slides
            if s.slide_type not in (SlideType.TITLE, SlideType.AGENDA, SlideType.CLOSING)
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
