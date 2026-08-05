"""
content_agent.py — Content Agent

Responsibility:
    Take the raw DeckBrief and produce a structured list of SlideSpec objects
    — one per slide — that defines the full narrative arc of the presentation.

How it works:
    1. At init, loads the mlarteka-pptx brand skill and builds a system prompt
       by prepending a fixed intro to the skill's Fixed Slide Rules, Typography,
       and Content Rules sections (~12 KB).  This means brand rule changes in
       the skill file flow through automatically without touching agent code.
    2. Builds a LangChain prompt (system + human messages) with brief fields
       as template variables.
    3. Calls GPT-4o asynchronously via a LangChain chain.
    4. Parses and validates the LLM's JSON array into SlideSpec objects.
    5. Returns a state update dict that LangGraph merges into DeckState.

LLM output contract:
    The LLM returns a JSON array; each item maps to a SlideSpec.  In addition
    to the core fields (title, bullets, speaker_notes), three ML arteka brand
    fields are required on every content/agenda slide:
        eyebrow     — 2-4 word ALL CAPS theme label
        intro_line  — one framing sentence below the title
        takeaway    — one "so what" sentence for the bottom navy bar
    Title/closing/divider slides return empty strings for these three fields.

Null coercion:
    Some LLM responses return JSON null for optional string fields.  The
    parsing loop normalises null → "" for all str-typed fields before Pydantic
    validation so no ValidationError is raised for missing optional content.
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
_SYSTEM_BASE = """You are a senior management consultant and presentation strategist at ML arteka (powered by mobileLIVE), with deep expertise in McKinsey, Bain, and BCG-style executive decks.

Your task: create a professional, executive-quality slide outline following the ML arteka brand system with the density, structure, and visual richness of a strategic consulting deck.

Narrative structure: Problem → Insight → Recommendation → Value → Action
Slide sequencing: Title (dark) → Exec summary/Agenda → Section dividers (dark) → Content slides → Closing (dark, CTA).

MASTER CONSULTING RULES — override any conflicting instruction:

RULE 1 — ONE MESSAGE PER SLIDE (absolute):
- Every slide communicates EXACTLY ONE key message. The headline IS the takeaway.
- Headline formula: [Conclusion]. [Reason.] — the headline must state what the audience should now believe.
  ✓ "Traditional QA Cannot Assess Agentic Systems"
  ✓ "Governance Is the Primary Constraint to AI Adoption"
  ✓ "Rigor Must Scale With Autonomy"
  ✗ FORBIDDEN generic titles: Overview, Details, Introduction, Background, Summary, Information, Analysis, Approach, Update
- Every title must pass: "Does this headline tell the audience what to believe or do?"

RULE 2 — MANDATORY NARRATIVE SEQUENCE (follow this exact deck order):
  1. Executive Summary — the ONE message leadership must leave with
  2. The Problem — why current approaches fail (quantified, specific)
  3. Gap Analysis — specific gaps with evidence and risk consequence
  4. Framework — the governing solution architecture (operating model or capability map)
  5. Methodology — how evaluation works (process, criteria, scoring rubric)
  6. Governance — oversight hierarchy, policies, accountability, RACI
  7. Roadmap — phased delivery with milestones, owners, and success measures
  8. Operating Model — how it scales in production (team, process, tooling)
  9. Key Takeaways — three leadership actions with accountability
  Progress every section as: Why problem exists → What framework solves it → How it operates → How it scales.

RULE 3 — CONTENT DENSITY (5-15 data points per slide):
- Every content slide must contain 5-15 meaningful data points: metrics, thresholds, owners, risk ratings, percentages.
- PROHIBITED: generic bullets ("Improve governance", "Reduce risk", "Increase efficiency").
- REQUIRED: specific facts ("reduce rework from 3 weeks to 3 days"), ownership ("Rogers AI Platform Team"), thresholds ("≥7.5/10 quality score"), risk ratings ("High if below 6.0").
- Every framework must include a PRINCIPLES row: Risk-Driven | Automation-First | Governance-Centric | Continuous Evaluation.
- Every section divider must contain: section number + conclusion-led title + one-sentence summary of the section's primary finding.

RULE 4 — LAYOUT PRIORITY ORDER (choose the first that fits the content):
  1. Actionable assessment matrix: criteria × dimensions × ownership × threshold × risk (use TABLE/ROW)
  2. RACI chart: activity × stakeholder role (R/A/C/I) (use TABLE/ROW with single-letter values)
  3. Maturity model: 5-level horizontal progression, current/target state marked (use TABLE/ROW)
  4. Stage-gate process flow: 4-5 gates with criteria, owner, outcome (use TABLE/ROW or visual_dominant + process_diagram)
  5. 3-column framework with PRINCIPLES row: Pillar | Approach | Principle (use TABLE/ROW)
  6. Assessment scorecard: dimension × metric × score × threshold × status × owner (use TABLE/ROW)
  7. 2-column before/after or problem/solution comparison (use TABLE/ROW)
  8. KPI stat band (4 large metrics as stat_band layout)
  NEVER use plain bullet lists unless the content structurally cannot become any of the above.

RULE 5 — ACTIONABLE TABLE FORMAT (every TABLE must include Owner and Risk/Status):
  Standard actionable table (evaluation, governance, risk, compliance slides):
    "TABLE: Dimension | Objective | Metric | Threshold | Owner | Risk"
    "ROW: Accuracy | Measure correctness | GPT-4o score | ≥7.5/10 | AI Platform | High if <6.0"
  RACI chart format:
    "TABLE: Activity | AI Dev Team | Platform Eng | Risk & Compliance | Executive"
    "ROW: Agent Registration | R | A | C | I"  (use single letters: R=Responsible A=Accountable C=Consulted I=Informed)
  Maturity model format:
    "TABLE: Capability | Level 1: Initial | Level 2: Managed | Level 3: Defined | Level 4: Quantified | Level 5: Optimising"
    "ROW: Agent Visibility | No catalog | Partial lists | Registry live | Real-time inventory | Predictive"

RULE 6 — MANDATORY INSIGHT BOX (every content slide):
  Every dense_consulting slide must include exactly one INSIGHT bullet:
  "INSIGHT: [Panel Type] | [Actionable sentence]. [Business consequence.]"
  - BAD: "INSIGHT: Business Impact | Evaluation is important for quality."
  - GOOD: "INSIGHT: Key Risk | Deploying without evaluation exposes Rogers to 3× compliance risk. 40% of unvalidated agents may face decommissioning."
  Panel types: Business Impact | Executive Insight | Why It Matters | Key Risk | Success Criteria | Executive Recommendation

RULE 7 — CHART AND VISUAL PRIORITY (for visual_dominant slides):
  Preferred formats in order: assessment matrix → RACI chart → heatmap → maturity model → stage-gate → process flow → roadmap → 3-layer architecture.
  Every chart/diagram must include an insight statement: what the data concludes, not just what it shows.
  visual_description must name every cell value, color encoding, row/column header, and the main insight node.

DENSE_CONSULTING ENCODING — select the best pattern for the content type:

PATTERN A — Standard table + right panel (default for analysis, scorecards, risk assessments):
  "TABLE: Col1 | Col2 | Col3 | Col4"       — table header
  "ROW: val1 | val2 | val3 | val4"          — data row (4-8 rows)
  "KPI: VALUE | Label | context"            — right-panel KPI card (2-3)
  "INSIGHT: Type | Two actionable sentences." — MANDATORY

PATTERN B — Two-column comparison (before/after, current/target, problem/solution, gap analysis):
  "COMPARE_LEFT: Current State"             — left column header (ALL CAPS label)
  "COMPARE_RIGHT: Target State"             — right column header
  "COMPARE_ROW: current item | target item" — one row per comparison (4-8 rows)
  "INSIGHT: Type | Two actionable sentences." — MANDATORY
  USE FOR: gap analysis, transformation comparisons, problem vs solution, risk vs control.

PATTERN C — Three-column framework (pillars, capabilities, strategic domains):
  "COL1: Catalog"                           — column 1 header
  "COL1_ITEM: Register all 40+ agents"      — bullet for column 1 (3-6 items)
  "COL2: Evaluate"                          — column 2 header
  "COL2_ITEM: Score on 5 quality dimensions"— bullet for column 2
  "COL3: Govern"                            — column 3 header
  "COL3_ITEM: Policy enforcement"           — bullet for column 3
  "INSIGHT: Type | Two actionable sentences." — MANDATORY
  USE FOR: operating models, governance pillars, capability frameworks, strategic priorities.

PATTERN D — Horizontal process steps (lifecycle, workflow, stage-gate, evaluation sequence):
  "STEP: 1 | Register | Agent submits metadata via registration API" — (repeat 4-5 steps)
  "STEP: 2 | Evaluate | LLM-as-Judge scores 5 quality dimensions"
  "KPI: VALUE | Label | context"            — optional (0-3 KPI cards below steps)
  "INSIGHT: Type | Two actionable sentences." — MANDATORY
  USE FOR: agent lifecycle, evaluation workflow, governance process, implementation stages.

CHOOSE THE RIGHT PATTERN: vary patterns deliberately across slides — never use the same pattern more than twice in a row. Alternate between table-based analysis (Pattern A), comparison (Pattern B), framework (Pattern C), and process (Pattern D) to create visual rhythm across the deck.

STAT_BAND ENCODING (for layout_variant="stat_band"):
  Exactly 4 bullets: "VALUE | LABEL | CONTEXT" (VALUE = metric, LABEL = 2-4 word name, CONTEXT = 1 sentence).

CHOOSE layout_variant:
  "visual_dominant": diagram/process IS the content — architecture, maturity model, RACI visual, roadmap.
  "dense_consulting": DEFAULT for all analysis, comparison, scorecard, framework, and evidence slides.
  "stat_band": executive KPI overview slides with 4 large metrics only.
  "split" and "content_heavy" are DEPRECATED — remap to "dense_consulting".

CRITICAL BRAND RULES (always follow):
- Titles use the conclusion-led headline formula: [Conclusion]. [Reason.] — sentence case, one idea.
- Every content/agenda slide must have: EYEBROW (2-4 ALL CAPS section label), INTRO_LINE (one framing sentence), TAKEAWAY (one "so what" sentence). Never leave these empty.
- No hedges or scaffolding on the slide face — caveats go in speaker_notes only.
- Eyebrow labels anchor each slide to its section (e.g. "GOVERNANCE FRAMEWORK", "EVALUATION METHODOLOGY", "GAP ANALYSIS").

VISUAL STORYTELLING RULES (always follow):
- Visuals are mandatory narrative elements on every content slide.
- Every visual must support a decision, risk, outcome, or recommendation — never decorative.
- visual_description must name every entity, metric, relationship, colour encoding, and emphasis node. Minimum 5 sentences for visual_dominant slides.
- Every chart must lead to an insight statement embedded in visual_description (e.g. "The orange-highlighted node shows that 40% of agents fall below the governance threshold").
- McKinsey/BCG diagram standards: dense labels on all nodes, all arrows labeled, all metrics explicit, zero empty areas, consulting colour palette.
- RACI charts: R=dark-red fill, A=orange fill, C=navy fill, I=grey fill. All cells legible.
- Maturity models: 5 columns (Initial→Managed→Defined→Quantified→Optimising), current-state column orange-outlined, target-state column dark-orange filled.
- Heatmaps: 5-level colour scale (green→yellow→orange→red→dark-red), all cells labeled with value and interpretation.

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
  eyebrow            — 2-4 words ALL CAPS section label for content/agenda slides; empty string "" for title/closing/section_divider
  intro_line         — one plain framing sentence below the title for content/agenda slides; empty string "" for title/closing/section_divider
  takeaway           — one bold "so what" sentence for the bottom bar on content slides; empty string "" for title/closing/section_divider/agenda
  key_message        — one crisp sentence summarising this slide's single insight
  bullets            — array of strings:
                       stat_band: EXACTLY 4 strings: "VALUE | LABEL | CONTEXT"
                       dense_consulting — choose one of four patterns (vary across the deck):
                         Pattern A (standard table): TABLE: + ROW: + KPI: + INSIGHT: prefixes
                         Pattern B (two-column comparison):
                           "COMPARE_LEFT: label" + "COMPARE_RIGHT: label" + "COMPARE_ROW: left | right" (4-8 rows) + "INSIGHT: Type | text"
                         Pattern C (three-column framework):
                           "COL1: Header" + "COL1_ITEM: bullet" (×3-6) + "COL2: ..." + "COL3: ..." + "INSIGHT: Type | text"
                         Pattern D (horizontal process steps):
                           "STEP: 1 | Stage | Description" (×4-5) + "KPI: ..." (0-3) + "INSIGHT: Type | text"
                         MANDATORY on every dense_consulting slide: one "INSIGHT: Type | text" bullet
                       visual_dominant: empty array [] (diagram carries the insight)
                       all other fallback: max 4 plain text bullets each ≤20 words
  speaker_notes      — 2-6 sentences for the presenter
  layout_variant     — REQUIRED for content slides:
                       "visual_dominant": diagram fills full slide; Key Insight strip added below image.
                         USE FOR: architecture diagrams, process flows, roadmaps, timelines, frameworks, governance models, maturity models, operating models.
                       "dense_consulting": DEFAULT for all other content slides. Two-zone layout with no image.
                         LEFT 60%: analysis table + evidence bullets. RIGHT 38%: executive callout + KPI cards.
                         REQUIRES structured bullet encoding (TABLE/ROW/KPI/INSIGHT prefixes — see bullets above).
                       "stat_band": KPI/metrics overview. 4 large stat boxes. No image.
                       "" for title/agenda/section_divider/closing.
  visual_type        — one of: hero_image, infographic, process_diagram, architecture_diagram,
                       comparison_table, timeline, roadmap, statistics_visual,
                       executive_illustration — REQUIRED for content/section_divider slides; null only for title/agenda/closing
  visual_description — REQUIRED for content and section_divider slides.
                       visual_dominant (5-7 sentences): every node label, stage, layer, metric, arrow, colour. Dense McKinsey-style brief.
                       dense_consulting / stat_band (2-3 sentences): describe the thematic context for the slide.
                       null only for title/agenda/closing slides.

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
                # Normalise LLM-invented visual_type values to the valid VisualType enum.
                _VALID_VISUAL_TYPES = {
                    "hero_image", "infographic", "process_diagram",
                    "architecture_diagram", "comparison_table", "timeline",
                    "roadmap", "statistics_visual", "executive_illustration",
                }
                _VISUAL_TYPE_MAP = {
                    # RACI / governance
                    "raci chart":             "comparison_table",
                    "raci":                   "comparison_table",
                    "raci_chart":             "comparison_table",
                    "governance chart":       "architecture_diagram",
                    "governance model":       "architecture_diagram",
                    "governance framework":   "architecture_diagram",
                    "accountability matrix":  "comparison_table",
                    # Maturity
                    "maturity model":         "infographic",
                    "maturity_model":         "infographic",
                    "maturity matrix":        "infographic",
                    "capability map":         "infographic",
                    "capability matrix":      "infographic",
                    # Heatmap / matrix
                    "heatmap":                "infographic",
                    "heat map":               "infographic",
                    "risk matrix":            "comparison_table",
                    "assessment matrix":      "comparison_table",
                    "scorecard":              "statistics_visual",
                    "assessment scorecard":   "statistics_visual",
                    # Process / flow
                    "stage gate":             "process_diagram",
                    "stage-gate":             "process_diagram",
                    "lifecycle":              "process_diagram",
                    "flow chart":             "process_diagram",
                    "flowchart":              "process_diagram",
                    "swim lane":              "process_diagram",
                    "swim_lane":              "process_diagram",
                    "operating model":        "architecture_diagram",
                    # Timeline aliases
                    "gantt":                  "timeline",
                    "gantt chart":            "timeline",
                    "milestone chart":        "timeline",
                    # General fallbacks
                    "diagram":                "architecture_diagram",
                    "chart":                  "infographic",
                    "table":                  "comparison_table",
                    "matrix":                 "comparison_table",
                    "framework":              "infographic",
                    "illustration":           "executive_illustration",
                    "photo":                  "hero_image",
                    "image":                  "hero_image",
                }
                raw_vt = item.get("visual_type")
                if raw_vt is not None and str(raw_vt).lower() not in _VALID_VISUAL_TYPES:
                    normalised = _VISUAL_TYPE_MAP.get(str(raw_vt).lower().strip())
                    if normalised:
                        logger.warning(
                            "ContentAgent: normalised visual_type '%s' → '%s'", raw_vt, normalised,
                        )
                        item["visual_type"] = normalised
                    else:
                        logger.warning(
                            "ContentAgent: unknown visual_type '%s' — setting None", raw_vt,
                        )
                        item["visual_type"] = None
                # Normalise LLM-invented slide_type values to the valid enum set.
                _SLIDE_TYPE_MAP = {
                    "recommendation": "content",
                    "value": "content",
                    "action": "closing",
                    "insight": "content",
                    "problem": "content",
                    "divider": "section_divider",
                    "section": "section_divider",
                    "cover": "title",
                    "intro": "title",
                    "summary": "content",
                    "conclusion": "closing",
                }
                if item.get("slide_type") not in (
                    "title", "agenda", "content", "section_divider", "closing"
                ):
                    raw_type = str(item.get("slide_type", "")).lower().strip()
                    item["slide_type"] = _SLIDE_TYPE_MAP.get(raw_type, "content")
                    logger.warning(
                        "ContentAgent: normalised unknown slide_type '%s' → '%s'",
                        raw_type, item["slide_type"],
                )
                # Normalise layout_variant to the valid set
                _VARIANT_ALIASES = {
                    "visual_dominant": "visual_dominant",
                    "full_visual": "visual_dominant",
                    "diagram_dominant": "visual_dominant",
                    "image_dominant": "visual_dominant",
                    "visual": "visual_dominant",
                    "full": "visual_dominant",
                    "content_heavy": "content_heavy",
                    "text_heavy": "content_heavy",
                    "text_led": "content_heavy",
                    "split": "split",
                    "balanced": "split",
                    "default": "split",
                    "stat_band": "stat_band",
                    "stats": "stat_band",
                    "kpi_band": "stat_band",
                    "kpi": "stat_band",
                    "metrics_band": "stat_band",
                    "statistics": "stat_band",
                    "dense_consulting": "dense_consulting",
                    "dense": "dense_consulting",
                    "consulting": "dense_consulting",
                    "two_zone": "dense_consulting",
                    "structured": "dense_consulting",
                    "split": "dense_consulting",    # redirect deprecated split → dense
                    "balanced": "dense_consulting",
                    "content_heavy": "dense_consulting",  # redirect deprecated
                    "text_heavy": "dense_consulting",
                    "text_led": "dense_consulting",
                    "default": "dense_consulting",
                }
                lv = str(item.get("layout_variant") or "split").lower().strip()
                item["layout_variant"] = _VARIANT_ALIASES.get(lv, "split")
                # Ensure new brand fields default to empty string if absent
                for key in ("eyebrow", "intro_line", "takeaway", "visual_description", "speaker_notes"):
                    val = item.get(key)
                    if val is None or val == "null":
                        item[key] = ""
                    elif isinstance(val, list):
                        item[key] = " ".join(str(v) for v in val)
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
