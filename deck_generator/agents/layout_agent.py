"""
layout_agent.py — Brand / Layout Agent

Responsibility:
    Produce one LayoutSpec per slide that encodes every positioning, sizing,
    and colour decision needed to render that slide in python-pptx.

Design system:
    All layouts follow the ML arteka (powered by mobileLIVE) brand system (_ML dict)
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

# ── ML arteka (powered by mobileLIVE) brand palette ─────────────────────────
_ML = {
    "bg_light": "#FEF5EE",      # Warm Peach — light slide background
    "bg_dark": "#0F0F37",       # Navy — dark slide background
    "title_light": "#0F0F37",   # Navy — headings on light slides
    "title_dark": "#FEFBF8",    # Off-White — headings/text on dark slides
    "body_light": "#3C3C5E",    # Body Navy — body text on light slides
    "body_dark": "#D8D8E4",     # Body Light — body text on dark slides
    "accent": "#E9590C",        # Primary Orange — separator bar, eyebrow, accents
    "accent_dark_text": "#E9590C",  # Orange text is AA-safe at any size on dark
    "accent_light_text": "#3C3C5E", # Body navy for small text on light (AA-safe)
    "caption_light": "#5C5C78", # Caption Navy
    "caption_dark": "#9F9FB5",  # Caption Light
}


class LayoutAgent:
    """Converts slide type and content into precise LayoutSpec objects.

    Design philosophy:
    - ML arteka brand: Warm Peach (#FEF5EE) light slides, Navy (#0F0F37) dark slides
    - Orange (#E9590C) accent separator bar at top of content/agenda slides
    - Nunito Sans typeface; medium-density type scale (titles 20-28pt, body 11pt)
    - Split-screen: text left, image right for content slides (50/50)
    - Full-bleed image + overlaid text for title/closing slides
    """

    # Slide canvas: 16:9 widescreen (13.33 × 7.5 inches)
    W = 13.33
    H = 7.5

    def _title_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.TITLE,
            # Full-bleed background image (photographic, navy scrim applied by image prompt)
            image_width_inches=self.W,
            image_height_inches=self.H,
            image_left_inches=0.0,
            image_top_inches=0.0,
            # Title in the lower-left area, clear of the full-bleed image scrim
            title_left_inches=1.2,
            title_top_inches=2.8,
            title_width_inches=10.5,
            title_height_inches=1.6,
            # Subtitle / eyebrow / client line below title
            content_left_inches=1.2,
            content_top_inches=4.6,
            content_width_inches=10.5,
            content_height_inches=1.2,
            background_color=_ML["bg_dark"],
            title_color=_ML["title_dark"],
            body_color=_ML["body_dark"],
            accent_color=_ML["accent_dark_text"],
            header_bar_color=_ML["accent"],
            title_font_size=28,
            subtitle_font_size=11,
            body_font_size=11,
            font_family="Nunito Sans",
        )

    def _agenda_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.AGENDA,
            # Image on the right; content zone x 0.5 to 7.6
            image_width_inches=4.83,
            image_height_inches=5.8,
            image_left_inches=8.0,
            image_top_inches=1.0,
            # Title below the orange separator bar (bar is 0.30" at top)
            title_left_inches=0.5,
            title_top_inches=0.38,
            title_width_inches=7.1,
            title_height_inches=0.72,
            content_left_inches=0.5,
            content_top_inches=1.25,
            content_width_inches=7.1,
            content_height_inches=4.95,
            background_color=_ML["bg_light"],
            title_color=_ML["title_light"],
            body_color=_ML["body_light"],
            accent_color=_ML["accent_light_text"],
            header_bar_color=_ML["accent"],
            title_font_size=20,
            subtitle_font_size=11,
            body_font_size=11,
            font_family="Nunito Sans",
        )

    def _content_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.CONTENT,
            # Image: right 50% (x 7.0 to 12.83)
            image_width_inches=5.83,
            image_height_inches=5.0,
            image_left_inches=7.0,
            image_top_inches=1.25,
            # Title: left portion, width stops before logo zone (x > 11.2)
            title_left_inches=0.5,
            title_top_inches=0.38,
            title_width_inches=10.8,
            title_height_inches=0.72,
            # Content: left half, content zone y 1.25 to 6.2
            content_left_inches=0.5,
            content_top_inches=1.25,
            content_width_inches=6.2,
            content_height_inches=4.95,
            background_color=_ML["bg_light"],
            title_color=_ML["title_light"],
            body_color=_ML["body_light"],
            accent_color=_ML["accent_light_text"],
            header_bar_color=_ML["accent"],
            title_font_size=20,
            subtitle_font_size=11,
            body_font_size=11,
            font_family="Nunito Sans",
        )

    def _section_divider_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.SECTION_DIVIDER,
            # Full-height image on the right half (photographic, navy scrim)
            image_width_inches=6.0,
            image_height_inches=self.H,
            image_left_inches=7.33,
            image_top_inches=0.0,
            title_left_inches=0.7,
            title_top_inches=2.7,
            title_width_inches=6.0,
            title_height_inches=1.4,
            content_left_inches=0.7,
            content_top_inches=4.3,
            content_width_inches=6.0,
            content_height_inches=2.0,
            background_color=_ML["bg_dark"],
            title_color=_ML["title_dark"],
            body_color=_ML["body_dark"],
            accent_color=_ML["accent_dark_text"],
            header_bar_color=_ML["accent"],
            title_font_size=26,
            subtitle_font_size=11,
            body_font_size=11,
            font_family="Nunito Sans",
        )

    def _closing_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.CLOSING,
            # Full-bleed background (flat navy or photographic with navy scrim)
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
            background_color=_ML["bg_dark"],
            title_color=_ML["title_dark"],
            body_color=_ML["body_dark"],
            accent_color=_ML["accent_dark_text"],
            header_bar_color=_ML["accent"],
            title_font_size=28,
            subtitle_font_size=11,
            body_font_size=11,
            font_family="Nunito Sans",
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
