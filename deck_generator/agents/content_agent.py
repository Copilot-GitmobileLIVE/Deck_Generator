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
from deck_generator.utils.timing import timer

logger = logging.getLogger("deck_generator.content_agent")

_SYSTEM = """You are a senior management consultant and presentation strategist at a top-tier consulting firm (McKinsey / Deloitte calibre).

Your task: create a professional, executive-quality slide outline for a client presentation.

Rules:
- Structure narrative as consultants do: Problem → Insight → Recommendation → Value → Action
- Each slide carries ONE clear message — no slide does two jobs
- Bullets are concise (≤10 words each), insight-driven, not descriptive
- Speaker notes coach the presenter with context and transitions
- Visual recommendations are purposeful and specific, never decorative
- The deck should feel like a Deloitte or BCG deliverable — not generic AI output
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
  slide_number   — integer, starting at 1
  slide_type     — one of: title, agenda, content, section_divider, closing
  title          — string
  subtitle       — string or null
  key_message    — one crisp sentence summarising this slide's insight
  bullets        — array of strings (max 5, each ≤10 words)
  speaker_notes  — 2–4 sentences for the presenter
  visual_type    — one of: hero_image, infographic, process_diagram, architecture_diagram,
                   comparison_table, timeline, roadmap, statistics_visual,
                   executive_illustration, or null
  visual_description — what the visual should literally show (1–3 sentences)

IMPORTANT: Return ONLY valid JSON. No markdown code fences. No surrounding text.
"""


class ContentAgent:
    """Generates the full slide narrative from a :class:`DeckBrief`.

    This is the first specialist agent in the pipeline.  Its output (a list
    of SlideSpec objects) defines the structure that every subsequent agent
    builds upon.  Getting the slide count, types, and key messages right here
    is critical — downstream agents can only work with what ContentAgent gives them.
    """

    def __init__(self) -> None:
        s = get_settings()
        # ChatOpenAI is the LangChain wrapper around the OpenAI chat completions API.
        # temperature=0.4 gives creative but consistent narrative output.
        self._llm = ChatOpenAI(
            model=s.model_content,
            temperature=s.content_temperature,
            api_key=s.openai_api_key,
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

        # Build the two-message prompt: system sets the persona, human supplies the brief.
        prompt = ChatPromptTemplate.from_messages([
            ("system", _SYSTEM),
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
