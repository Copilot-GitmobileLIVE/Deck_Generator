"""
content_agent.py — Content Agent

Responsibility:
    Take the raw DeckBrief and produce a structured list of SlideSpec objects
    — one per slide — that defines the full narrative arc of the presentation.

How it works:
    1. Builds a LangChain prompt (system + human messages) that embeds the
       brief fields as template variables.
    2. Sends the prompt to GPT-4o via an async LangChain chain.
    3. Parses the LLM's JSON response into validated SlideSpec objects.
    4. Returns a state update dict that LangGraph merges into DeckState.

LLM output contract:
    The LLM is instructed to return a plain JSON array of objects.  Each
    object matches the SlideSpec schema.  If the model wraps the array in
    markdown fences or a dict key, _strip_fences() and the dict-unwrap logic
    handle it gracefully.
"""
from __future__ import annotations

import json
import logging
from typing import List

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from deck_generator.config import get_settings
from deck_generator.models import DeckBrief, DeckState, SlideSpec, SlideType, VisualType
from deck_generator.utils.skill_loader import get_brand_skill
from deck_generator.utils.timing import timer

logger = logging.getLogger("deck_generator.content_agent")

# Static preamble; authoritative brand rules are appended from the skill at init time.
_SYSTEM_BASE = """You are a senior management consultant and presentation strategist at ML arteka (powered by mobileLIVE).

Your task: create a professional, executive-quality slide outline following the ML arteka brand system.

Narrative structure: Problem → Insight → Recommendation → Value → Action
Slide sequencing: Title (dark) → Exec summary → Section dividers (dark) → Content → Recommendation → Closing (dark, CTA).

CRITICAL BRAND RULES (always follow):
- Titles are action titles in sentence case: one idea, no Title Case, no ALL CAPS.
- Every content/agenda slide must have an EYEBROW (2-4 ALL CAPS words), INTRO_LINE (one framing sentence), and TAKEAWAY (one "so what" sentence for the bottom bar).
- No scaffolding on the slide face: caveats, citations, and hedges go in speaker_notes only.

The authoritative ML arteka brand guidelines follow. Apply every rule below exactly:
"""
_HUMAN = """Build a complete slide deck outline for this brief:

Title: {title}
Client: {client}
Industry: {industry}
Audience: {audience}
Objective: {objective}
Key Messages:
{key_messages}
Target Slide Count: {slide_count}
Tone: {tone}
Additional Context: {additional_context}

Return a JSON ARRAY of slide objects. Each object must have exactly these keys:
  slide_number       — integer, starting at 1
  slide_type         — one of: title, agenda, content, section_divider, closing
  title              — action title, sentence case, one idea (no Topic Case)
  subtitle           — string or null (used only on title and closing slides)
  eyebrow            — 2-4 words ALL CAPS theme label for content/agenda slides; empty string "" for title/closing/section_divider
  intro_line         — one plain sentence below the title for content/agenda slides; empty string "" for title/closing/section_divider
  takeaway           — one bold "so what" sentence for the bottom bar on content slides; empty string "" for title/closing/section_divider/agenda
  key_message        — one crisp sentence summarising this slide's single insight
  bullets            — array of strings (max 5, each ≤15 words, insight-driven not descriptive)
  speaker_notes      — 2-6 sentences for the presenter; include any image prompt here for dark/image slides
  visual_type        — one of: hero_image, infographic, process_diagram, architecture_diagram,
                       comparison_table, timeline, roadmap, statistics_visual,
                       executive_illustration, or null
  visual_description — what the visual should literally show (1-3 sentences), null for title/closing

IMPORTANT: Return ONLY valid JSON. No markdown code fences. No surrounding text.
"""


class ContentAgent:
    """Generates the full slide narrative from a :class:`DeckBrief`.

    Loads the mlarteka-pptx brand skill at init time and injects the
    authoritative Fixed Slide Rules, Typography, and Content Rules sections
    into the system prompt so the LLM always works from the current skill,
    not from a summarised static string.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._llm = ChatOpenAI(
            model=s.model_content,
            temperature=s.content_temperature,
            api_key=s.openai_api_key,
        )
        # Build the system prompt once at init: base intro + live skill sections.
        skill = get_brand_skill()
        brand_rules = skill.content_rules_prompt()
        self._system = _SYSTEM_BASE + brand_rules
        logger.info(
            "ContentAgent: loaded brand skill from %s (%d chars of brand rules)",
            skill.skill_path.name, len(brand_rules),
        )

    @staticmethod
    def _strip_fences(raw: str) -> str:
        """Remove markdown code fences that some models add despite instructions.

        Even when the system prompt says "Return ONLY valid JSON", some model
        versions still wrap the output in triple backticks.  This method strips
        those fences so `json.loads()` can parse the content cleanly.
        """
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            # Drop the opening ```json or ``` line
            lines = lines[1:] if lines[0].startswith("```") else lines
            # Drop the closing ``` line
            lines = lines[:-1] if lines and lines[-1].strip() == "```" else lines
            raw = "\n".join(lines).strip()
        return raw

    async def run(self, state: DeckState) -> dict:
        """Run the content generation chain and return a state update dict.

        Args:
            state: Current DeckState; must have `deck_brief` populated.

        Returns:
            A dict with keys: slides, slide_outline, audience, industry,
            brand, status, execution_logs.  LangGraph merges this into the
            shared state.

        Raises:
            ValueError: If deck_brief is missing or the LLM response cannot
                        be parsed as valid JSON.
        """
        brief = state.deck_brief
        if not brief:
            raise ValueError("DeckState.deck_brief is required before running ContentAgent")

        logger.info("ContentAgent: generating content for '%s'", brief.title)

        # Build the prompt using the skill-loaded system message (self._system).
        prompt = ChatPromptTemplate.from_messages([
            ("system", self._system),
            ("human", _HUMAN),
        ])
        # The pipe operator (|) chains the prompt template into the LLM call.
        # prompt | self._llm produces a Runnable that formats → calls → returns a message.
        chain = prompt | self._llm

        # timer() logs how long the LLM call takes — useful for cost/latency tracking.
        with timer("ContentAgent.run", logger):
            response = await chain.ainvoke({
                "title": brief.title,
                "client": brief.client,
                "industry": brief.industry,
                "audience": brief.audience,
                "objective": brief.objective,
                "key_messages": "\n".join(f"  • {m}" for m in brief.key_messages),
                "slide_count": brief.slide_count_target,
                "tone": brief.tone,
                "additional_context": brief.additional_context or "None provided",
            })

        # response.content is the raw text string from the LLM.
        raw = self._strip_fences(response.content)

        try:
            data = json.loads(raw)
            # Some models wrap the array: {"slides": [...]} — unwrap if needed.
            if isinstance(data, dict) and "slides" in data:
                data = data["slides"]
            # Some models return the string "null" instead of JSON null for
            # optional enum fields.  Convert those to Python None before
            # passing to Pydantic so validation does not fail.
            for item in data:
                for key in ("visual_type", "subtitle"):
                    if item.get(key) == "null":
                        item[key] = None
                # Ensure new brand fields default to empty string if absent
                for key in ("eyebrow", "intro_line", "takeaway", "visual_description", "speaker_notes"):
                    if item.get(key) is None or item.get(key) == "null":
                        item[key] = ""
            # Validate each item against the SlideSpec Pydantic model.
            slides: List[SlideSpec] = [SlideSpec(**s) for s in data]
        except Exception as exc:
            logger.error("ContentAgent: JSON parse error — %s", exc)
            logger.debug("Raw response: %s", raw[:1000])
            raise ValueError(f"ContentAgent failed to parse LLM response: {exc}") from exc

        # Build a plain-text outline for logging and human review.
        outline = [
            f"{s.slide_number}. [{s.slide_type.upper()}] {s.title}"
            for s in slides
        ]
        log_entry = (
            f"ContentAgent: {len(slides)} slides generated for '{brief.title}'"
        )
        logger.info(log_entry)

        # Return only the fields we are updating; all other DeckState fields carry forward.
        return {
            "slides": slides,
            "slide_outline": outline,
            "audience": brief.audience,
            "industry": brief.industry,
            "brand": brief.brand,
            "status": "content_complete",
            # Append our log entry to the existing log list (not replace it).
            "execution_logs": state.execution_logs + [log_entry],
        }
