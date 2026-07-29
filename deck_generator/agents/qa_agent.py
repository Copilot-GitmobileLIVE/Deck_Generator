"""
qa_agent.py — QA Agent

Responsibility:
    Validate the assembled deck against a fixed rule set and decide whether
    the pipeline output is acceptable or needs to be regenerated.

This agent is PURE LOGIC — no LLM, no file writes.  It only reads state
fields that were populated by earlier agents.

Rule categories:
    1. Structural  — deck-wide rules (min slide count, required slide types)
    2. Per-slide   — rules applied to every individual slide
    3. Output      — verifies the .pptx file was actually created on disk

Retry loop integration:
    When any 'error'-severity issue is found, qa_agent increments
    DeckState.retry_count.  graph.py's route_after_qa() then checks
    whether retry_count < max_retries and routes back to ContentAgent
    for a regeneration attempt.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from deck_generator.models import DeckState, QAIssue, QAResult, SlideType

logger = logging.getLogger("deck_generator.qa_agent")

_MIN_SLIDES = 3


class QAAgent:
    """Validates the generated deck against a structured rule set.

    Severity levels:
    - error   → deck is unusable; triggers retry if retries remain
    - warning → deck is suboptimal but deliverable
    - info    → informational note only
    """

    def run(self, state: DeckState) -> dict:
        """Run all validation checks and return a QAResult + state update.

        Args:
            state: Must have `slides`, `selected_images`, and `pptx_path`
                   all populated by the preceding agents.

        Returns:
            Dict containing `qa_results` (QAResult), updated `status`, and
            `retry_count` (incremented only on failure).
        """
        issues: List[QAIssue] = []
        slides = state.slides
        # Build a dict for O(1) image lookup per slide instead of scanning the list
        images_by_slide = {si.slide_number: si for si in state.selected_images}

        # ── Structural checks ─────────────────────────────────────────────────
        if len(slides) < _MIN_SLIDES:
            issues.append(QAIssue(
                issue_type="slide_count_too_low",
                description=f"Deck has {len(slides)} slides; minimum is {_MIN_SLIDES}",
                severity="error",
            ))

        slide_types = {s.slide_type for s in slides}
        if SlideType.TITLE not in slide_types:
            issues.append(QAIssue(
                issue_type="missing_title_slide",
                description="No title slide found in deck",
                severity="error",
            ))
        if SlideType.CLOSING not in slide_types:
            issues.append(QAIssue(
                issue_type="missing_closing_slide",
                description="No closing slide found",
                severity="warning",
            ))

        # ── Per-slide checks ──────────────────────────────────────────────────
        for s in slides:
            # Every slide must have a title
            if not (s.title or "").strip():
                issues.append(QAIssue(
                    slide_number=s.slide_number,
                    issue_type="missing_title",
                    description=f"Slide {s.slide_number} has an empty title",
                    severity="error",
                ))

            # Content slides must have bullets
            if s.slide_type == SlideType.CONTENT and not s.bullets:
                issues.append(QAIssue(
                    slide_number=s.slide_number,
                    issue_type="empty_content",
                    description=f"Slide {s.slide_number} '{s.title}' has no bullet points",
                    severity="warning",
                ))

            # Visual slides should have images
            if s.slide_type in (SlideType.CONTENT, SlideType.SECTION_DIVIDER):
                si = images_by_slide.get(s.slide_number)
                if not si or not si.selected_image_path:
                    issues.append(QAIssue(
                        slide_number=s.slide_number,
                        issue_type="missing_image",
                        description=f"Slide {s.slide_number} has no selected image",
                        severity="warning",
                    ))
                elif not Path(si.selected_image_path).exists():
                    issues.append(QAIssue(
                        slide_number=s.slide_number,
                        issue_type="broken_image_path",
                        description=f"Slide {s.slide_number} image file not found: {si.selected_image_path}",
                        severity="warning",
                    ))

        # ── Output check ──────────────────────────────────────────────────────
        if not state.pptx_path:
            issues.append(QAIssue(
                issue_type="no_pptx_path",
                description="PPTX path not set in state",
                severity="error",
            ))
        elif not Path(state.pptx_path).exists():
            issues.append(QAIssue(
                issue_type="pptx_file_missing",
                description=f"PPTX file not found on disk: {state.pptx_path}",
                severity="error",
            ))

        # ── Aggregate result ──────────────────────────────────────────────────
        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]
        passed = len(errors) == 0

        summary = f"Slides: {len(slides)}"
        if issues:
            summary += f" | {len(errors)} error(s), {len(warnings)} warning(s)"
        else:
            summary += " | All checks passed ✓"

        qa_result = QAResult(
            passed=passed,
            slide_count=len(slides),
            issues=issues,
            report_summary=summary,
        )

        status = "qa_complete" if passed else "qa_failed"
        log_entry = f"QAAgent: {'PASS' if passed else 'FAIL'} — {summary}"
        logger.info(log_entry)

        update: dict = {
            "qa_results": qa_result,
            "status": status,
            "execution_logs": state.execution_logs + [log_entry],
        }
        # Increment retry counter here so the orchestrator can check it
        if not passed:
            update["retry_count"] = state.retry_count + 1

        return update
