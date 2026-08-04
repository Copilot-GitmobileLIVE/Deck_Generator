"""
layout_agent.py — Brand / Layout Agent

Responsibility:
    Produce one LayoutSpec per slide that encodes every positioning, sizing,
    and colour decision needed to render that slide in python-pptx.

Design system:
    All layouts follow the ML arteka brand system defined in _ML and the
    brand grid from the mlarteka-pptx skill (loaded at init via skill_loader).
    Canvas: 16:9 widescreen, 13.33 × 7.5 inches.

    Brand grid for content/agenda slides (show_brand_header=True):
        y 0.26" Eyebrow | y 0.58" Tick rule | y 0.85" Title | y 1.55" Intro
        y 2.2"–6.2" Content zone | y 6.45" Takeaway bar

This agent is PURE LOGIC — it calls no LLM and makes no API calls.
Layout decisions are made deterministically based on SlideType alone:

    TITLE           → _title_layout          full-bleed image + overlaid title
    AGENDA          → _agenda_layout          image right, numbered list left
    CONTENT         → _content_layout         text left, image right, brand header
    SECTION_DIVIDER → _section_divider_layout dark bg, image right half
    CLOSING         → _closing_layout          dark full-bleed + CTA text

    show_brand_header controls whether SlideRenderer draws the eyebrow/tick/
    intro lockup.  Title, section_divider, and closing set it to False because
    those slides use a full-bleed image or flat dark background with no top chrome.

To add a new slide type:
    1. Add an entry to the SlideType enum in schemas.py.
    2. Add a _<type>_layout() method here following the same pattern.
    3. Add the mapping to the `dispatch` dict in run().
"""
from __future__ import annotations

import logging
from typing import List

from deck_generator.models import DeckState, LayoutSpec, SlideSpec, SlideType
from deck_generator.utils.skill_loader import get_brand_skill

logger = logging.getLogger("deck_generator.layout_agent")

# ── ML arteka (powered by mobileLIVE) brand palette ─────────────────────────
_ML = {
    "bg_light": "#FEF5EE",       # Warm Peach — flat light slide background
    "bg_dark": "#0F0F37",        # Navy — flat dark slide background
    "title_light": "#0F0F37",    # Navy — headings on light slides
    "title_dark": "#FEFBF8",     # Off-White — headings/text on dark slides
    "body_light": "#3C3C5E",     # Body Navy — body text on light slides
    "body_dark": "#D8D8E4",      # Body Light — body text on dark slides
    "accent": "#E9590C",         # Primary Orange — tick rule, item accents
    "eyebrow_light": "#434E80",  # Indigo Grey — eyebrow text on light (AA-safe on peach)
    "eyebrow_dark": "#E9590C",   # Orange — eyebrow text on dark (AA-safe at any size)
    "intro_light": "#3C3C5E",    # Body Navy — intro sentence on light slides
    "intro_dark": "#D8D8E4",     # Body Light — intro sentence on dark slides
    "caption_light": "#5C5C78",  # Caption Navy
    "caption_dark": "#9F9FB5",   # Caption Light
    "takeaway_bg": "#0F0F37",    # Navy fill for bottom takeaway bar
    "takeaway_text": "#FEFBF8",  # Off-White text in takeaway bar
}


class LayoutAgent:
    """Converts slide type and content into precise LayoutSpec objects.

    Loads the mlarteka-pptx brand skill at init time to confirm brand grid
    coordinates are in sync. Layout decisions are deterministic (no LLM);
    the skill is used for logging and future dynamic coordinate extraction.
    """

    # Slide canvas: 16:9 widescreen (13.33 × 7.5 inches)
    W = 13.33
    H = 7.5

    def __init__(self) -> None:
        # Load skill to confirm it is present and log its grid section length.
        # Layout coordinates below are hardcoded from the same spec; they stay
        # stable across runs and don't need runtime parsing.
        skill = get_brand_skill()
        grid = skill.get_section("Layout Grid")
        logger.info(
            "LayoutAgent: brand skill loaded from %s — Layout Grid section: %d chars",
            skill.skill_path.name, len(grid),
        )

    def _title_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.TITLE,
            # Full-bleed background image (photographic, navy scrim from image prompt)
            image_width_inches=self.W,
            image_height_inches=self.H,
            image_left_inches=0.0,
            image_top_inches=0.0,
            # Title in the lower-left quadrant, above the navy scrim region
            title_left_inches=1.2,
            title_top_inches=2.8,
            title_width_inches=10.5,
            title_height_inches=1.4,
            # Subtitle / deck context line below the title
            content_left_inches=1.2,
            content_top_inches=4.4,
            content_width_inches=10.5,
            content_height_inches=1.2,
            background_color=_ML["bg_dark"],
            title_color=_ML["title_dark"],
            body_color=_ML["body_dark"],
            accent_color=_ML["eyebrow_dark"],
            header_bar_color=_ML["accent"],
            eyebrow_color=_ML["eyebrow_dark"],
            intro_color=_ML["intro_dark"],
            takeaway_bg_color=_ML["takeaway_bg"],
            takeaway_text_color=_ML["takeaway_text"],
            title_font_size=28,
            subtitle_font_size=11,
            body_font_size=11,
            font_family="Nunito Sans",
            show_brand_header=False,  # Full-bleed image slide; no eyebrow lockup
        )

    def _agenda_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.AGENDA,
            # Image on the right half; content on the left up to x 7.1
            image_width_inches=4.83,
            image_height_inches=5.5,
            image_left_inches=8.0,
            image_top_inches=1.5,
            # Brand header: eyebrow (0.26) → tick rule (0.58) → title (0.85)
            title_left_inches=0.5,
            title_top_inches=0.85,
            title_width_inches=7.1,
            title_height_inches=0.72,
            # Content zone: y 2.2 to 6.2
            content_left_inches=0.5,
            content_top_inches=2.2,
            content_width_inches=7.1,
            content_height_inches=4.0,
            background_color=_ML["bg_light"],
            title_color=_ML["title_light"],
            body_color=_ML["body_light"],
            accent_color=_ML["accent"],
            header_bar_color=_ML["accent"],
            eyebrow_color=_ML["eyebrow_light"],
            intro_color=_ML["intro_light"],
            takeaway_bg_color=_ML["takeaway_bg"],
            takeaway_text_color=_ML["takeaway_text"],
            title_font_size=20,
            subtitle_font_size=11,
            body_font_size=11,
            font_family="Nunito Sans",
            show_brand_header=True,
        )

    def _content_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.CONTENT,
            # Image: right 50% panel (x 7.0 to 12.83), spans the content zone
            image_width_inches=5.5,
            image_height_inches=4.7,
            image_left_inches=7.1,
            image_top_inches=1.5,
            # Title: left side; width stops before logo zone at x 11.2
            title_left_inches=0.5,
            title_top_inches=0.85,
            title_width_inches=10.7,
            title_height_inches=0.72,
            # Content zone: y 2.2 to 6.2 (per brand grid), left half
            content_left_inches=0.5,
            content_top_inches=2.2,
            content_width_inches=6.2,
            content_height_inches=4.0,
            background_color=_ML["bg_light"],
            title_color=_ML["title_light"],
            body_color=_ML["body_light"],
            accent_color=_ML["accent"],
            header_bar_color=_ML["accent"],
            eyebrow_color=_ML["eyebrow_light"],
            intro_color=_ML["intro_light"],
            takeaway_bg_color=_ML["takeaway_bg"],
            takeaway_text_color=_ML["takeaway_text"],
            title_font_size=20,
            subtitle_font_size=11,
            body_font_size=11,
            font_family="Nunito Sans",
            show_brand_header=True,
        )

    def _section_divider_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.SECTION_DIVIDER,
            # Full-height image on the right half (photographic placeholder, navy scrim)
            image_width_inches=6.0,
            image_height_inches=self.H,
            image_left_inches=7.33,
            image_top_inches=0.0,
            # Section number + title on the left dark panel, generous negative space
            title_left_inches=0.7,
            title_top_inches=2.8,
            title_width_inches=6.2,
            title_height_inches=1.4,
            content_left_inches=0.7,
            content_top_inches=4.4,
            content_width_inches=6.2,
            content_height_inches=1.8,
            background_color=_ML["bg_dark"],
            title_color=_ML["title_dark"],
            body_color=_ML["body_dark"],
            accent_color=_ML["eyebrow_dark"],
            header_bar_color=_ML["accent"],
            eyebrow_color=_ML["eyebrow_dark"],
            intro_color=_ML["intro_dark"],
            takeaway_bg_color=_ML["takeaway_bg"],
            takeaway_text_color=_ML["takeaway_text"],
            title_font_size=26,
            subtitle_font_size=11,
            body_font_size=11,
            font_family="Nunito Sans",
            show_brand_header=False,  # Dark section break; eyebrow sits mid-slide, not top
        )

    def _closing_layout(self, n: int) -> LayoutSpec:
        return LayoutSpec(
            slide_number=n,
            slide_type=SlideType.CLOSING,
            # Full-bleed dark background (flat navy or photographic with navy scrim)
            image_width_inches=self.W,
            image_height_inches=self.H,
            image_left_inches=0.0,
            image_top_inches=0.0,
            # CTA headline centered; logo centered below (per brand spec for closing)
            title_left_inches=1.5,
            title_top_inches=2.8,
            title_width_inches=10.0,
            title_height_inches=1.6,
            content_left_inches=1.5,
            content_top_inches=4.6,
            content_width_inches=10.0,
            content_height_inches=1.6,
            background_color=_ML["bg_dark"],
            title_color=_ML["title_dark"],
            body_color=_ML["body_dark"],
            accent_color=_ML["eyebrow_dark"],
            header_bar_color=_ML["accent"],
            eyebrow_color=_ML["eyebrow_dark"],
            intro_color=_ML["intro_dark"],
            takeaway_bg_color=_ML["takeaway_bg"],
            takeaway_text_color=_ML["takeaway_text"],
            title_font_size=28,
            subtitle_font_size=11,
            body_font_size=11,
            font_family="Nunito Sans",
            show_brand_header=False,  # Full-bleed CTA slide; no eyebrow header lockup
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

        log_entry = f"LayoutAgent: {len(specs)} layout specs built (brand: mlarteka-pptx)"
        logger.info(log_entry)

        return {
            "layout_specs": specs,
            "status": "layout_complete",
            "execution_logs": state.execution_logs + [log_entry],
        }
