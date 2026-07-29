"""
layout_agent.py — Brand / Layout Agent

Responsibility:
    Produce one LayoutSpec per slide that encodes every positioning, sizing,
    and colour decision needed to render that slide in python-pptx.

Design system:
    All layouts follow the mobileLIVE consulting colour palette (_BLUE dict)
    and a 16:9 widescreen canvas (13.33 × 7.5 inches).

This agent is PURE LOGIC — it calls no LLM and makes no API calls.
Layout decisions are made deterministically based on SlideType alone:

    TITLE          → _title_layout         full-bleed image + overlaid title
    AGENDA         → _agenda_layout         image right, numbered list left
    CONTENT        → _content_layout        split: text left, image right
    SECTION_DIVIDER→ _section_divider_layout dark bg, image right half
    CLOSING        → _closing_layout         branded full-bleed + CTA

To add a new slide type:
    1. Add an entry to the SlideType enum in schemas.py.
    2. Add a _<type>_layout() method here following the same pattern.
    3. Add the mapping to the `dispatch` dict in run().
"""
from __future__ import annotations

import logging
from typing import List

from deck_generator.models import DeckState, LayoutSpec, SlideSpec, SlideType

logger = logging.getLogger("deck_generator.layout_agent")

# ── mobileLIVE consulting colour palette ─────────────────────────────────────
_BLUE = {
    "bg": "#FFFFFF",
    "dark_bg": "#0A1628",
    "title": "#0A1628",
    "body": "#2D3748",
    "accent": "#0057B8",
    "bar": "#0057B8",
    "highlight": "#F6C94A",
    "light_body": "#A0AEC0",
    "white": "#FFFFFF",
    "white_soft": "#E2E8F0",
}


class LayoutAgent:
    """Converts slide type and content into precise LayoutSpec objects.

    Design philosophy:
    - Minimal text, high visual density
    - Split-screen: text left, image right for content slides
    - Full-bleed image + overlaid title for title/closing slides
    - Thin accent bar at the top of content slides for brand consistency
    - Font sizing follows executive readability standards
    """

    # Slide canvas: 16:9 widescreen (13.33 × 7.5 inches)
    W = 13.33
    H = 7.5

    def _title_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.TITLE,
            # Full-bleed background image
            image_width_inches=self.W,
            image_height_inches=self.H,
            image_left_inches=0.0,
            image_top_inches=0.0,
            # Title sits in the lower-left third
            title_left_inches=1.2,
            title_top_inches=2.6,
            title_width_inches=10.5,
            title_height_inches=1.6,
            # Subtitle / client line below title
            content_left_inches=1.2,
            content_top_inches=4.4,
            content_width_inches=10.5,
            content_height_inches=1.2,
            background_color=_BLUE["dark_bg"],
            title_color=_BLUE["white"],
            body_color=_BLUE["white_soft"],
            accent_color=_BLUE["highlight"],
            header_bar_color=_BLUE["bar"],
            title_font_size=42,
            subtitle_font_size=22,
            body_font_size=18,
        )

    def _agenda_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.AGENDA,
            image_width_inches=4.8,
            image_height_inches=6.2,
            image_left_inches=8.1,
            image_top_inches=0.75,
            title_left_inches=0.5,
            title_top_inches=0.38,
            title_width_inches=7.3,
            title_height_inches=0.9,
            content_left_inches=0.5,
            content_top_inches=1.5,
            content_width_inches=7.3,
            content_height_inches=5.6,
            background_color=_BLUE["bg"],
            title_color=_BLUE["title"],
            body_color=_BLUE["body"],
            accent_color=_BLUE["accent"],
            header_bar_color=_BLUE["bar"],
            title_font_size=30,
            subtitle_font_size=17,
            body_font_size=16,
        )

    def _content_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.CONTENT,
            # Image: right half
            image_width_inches=6.0,
            image_height_inches=5.2,
            image_left_inches=6.9,
            image_top_inches=1.15,
            # Title: full width at top (below accent bar)
            title_left_inches=0.45,
            title_top_inches=0.38,
            title_width_inches=12.5,
            title_height_inches=0.82,
            # Content: left half
            content_left_inches=0.45,
            content_top_inches=1.38,
            content_width_inches=6.2,
            content_height_inches=5.7,
            background_color=_BLUE["bg"],
            title_color=_BLUE["title"],
            body_color=_BLUE["body"],
            accent_color=_BLUE["accent"],
            header_bar_color=_BLUE["bar"],
            title_font_size=26,
            subtitle_font_size=16,
            body_font_size=14,
        )

    def _section_divider_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.SECTION_DIVIDER,
            image_width_inches=6.2,
            image_height_inches=self.H,
            image_left_inches=7.13,
            image_top_inches=0.0,
            title_left_inches=0.7,
            title_top_inches=2.7,
            title_width_inches=6.0,
            title_height_inches=1.4,
            content_left_inches=0.7,
            content_top_inches=4.3,
            content_width_inches=6.0,
            content_height_inches=2.4,
            background_color=_BLUE["dark_bg"],
            title_color=_BLUE["white"],
            body_color=_BLUE["light_body"],
            accent_color=_BLUE["highlight"],
            header_bar_color=_BLUE["highlight"],
            title_font_size=34,
            subtitle_font_size=18,
            body_font_size=15,
        )

    def _closing_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.CLOSING,
            image_width_inches=self.W,
            image_height_inches=self.H,
            image_left_inches=0.0,
            image_top_inches=0.0,
            title_left_inches=1.5,
            title_top_inches=2.7,
            title_width_inches=10.0,
            title_height_inches=1.6,
            content_left_inches=1.5,
            content_top_inches=4.5,
            content_width_inches=10.0,
            content_height_inches=1.6,
            background_color=_BLUE["accent"],
            title_color=_BLUE["white"],
            body_color=_BLUE["white_soft"],
            accent_color=_BLUE["highlight"],
            header_bar_color=_BLUE["highlight"],
            title_font_size=38,
            subtitle_font_size=20,
            body_font_size=17,
        )

    def run(self, state: DeckState) -> dict:
        """Generate a LayoutSpec for every slide in the deck.

        The dispatch dict maps each SlideType to its layout-builder method.
        Unrecognised slide types fall back to _content_layout so the pipeline
        never crashes on unexpected data from the LLM.

        Args:
            state: Must have `slides` populated by ContentAgent.

        Returns:
            Dict with `layout_specs` (List[LayoutSpec]) and updated status/logs.
        """
        # Map each SlideType to the corresponding layout-builder method.
        # Using a dict (dispatch table) is cleaner than a chain of if/elif.
        dispatch = {
            SlideType.TITLE: self._title_layout,
            SlideType.AGENDA: self._agenda_layout,
            SlideType.CONTENT: self._content_layout,
            SlideType.SECTION_DIVIDER: self._section_divider_layout,
            SlideType.CLOSING: self._closing_layout,
        }

        specs: List[LayoutSpec] = []
        for slide in state.slides:
            builder = dispatch.get(slide.slide_type, self._content_layout)
            specs.append(builder(slide.slide_number))

        log_entry = f"LayoutAgent: {len(specs)} layout specs built"
        logger.info(log_entry)

        return {
            "layout_specs": specs,
            "status": "layout_complete",
            "execution_logs": state.execution_logs + [log_entry],
        }
