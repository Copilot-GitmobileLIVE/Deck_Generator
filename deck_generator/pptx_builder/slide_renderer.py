"""
slide_renderer.py — SlideRenderer

Responsibility:
    Add every text and shape element to a single python-pptx Slide object
    using the positioning values from a LayoutSpec.

All measurements use python-pptx's Inches() helper which converts inches to
English Metric Units (EMU) internally.  Font sizes use Pt() (points).

ML arteka brand header lockup (content and agenda slides only):
    The header lockup runs top-to-bottom at fixed y positions:
        y 0.26" — Eyebrow   ALL CAPS label (Indigo Grey on light, Orange on dark)
        y 0.56" — Tick rule  short orange rectangle, 0.77" wide
        y 0.85" — Title      Bold, 18-20pt, sentence case
        y 1.55" — Intro line one plain framing sentence, regular weight

Rendering order per slide (shapes added in z-order, back to front):
    1. Background fill  — applied to slide.background (always behind all shapes)
    2. Eyebrow text box — brand header lockup; skipped when show_brand_header=False
    3. Tick rule shape  — orange 0.77" accent rule; skipped with header
    4. Title text box
    5. Intro line       — skipped when show_brand_header=False
    6. Key message / subtitle text box
    7. Bullet points text box  (one text box; one paragraph per bullet)
    8. Takeaway bar    — full-width navy bar + centered text; content slides only
    9. Slide number    — bottom-right; omitted on title and closing slides
   10. Speaker notes   — written to the notes pane, not visible on the slide
"""
from __future__ import annotations

import logging
from typing import Optional

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.slide import Slide
from pptx.util import Inches, Pt

from deck_generator.models import LayoutSpec, SlideSpec, SlideType

logger = logging.getLogger("deck_generator.slide_renderer")


def _rgb(hex_color: str) -> RGBColor:
    """Convert a '#RRGGBB' hex string to a python-pptx RGBColor object.

    python-pptx requires RGBColor(r, g, b) with integer channel values 0–255.
    We store colours as hex strings in LayoutSpec so they are human-readable
    and JSON-serialisable; this helper converts them at render time.

    Example:
        _rgb("#0057B8")  →  RGBColor(0, 87, 184)
    """
    h = hex_color.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


class SlideRenderer:
    """Renders text content onto a slide using LayoutSpec positioning rules.

    All public-facing logic is in render().  The private _add_* methods each
    handle one visual layer and are separated for readability and testability.
    """

    def _set_background(self, slide: Slide, layout: LayoutSpec) -> None:
        """Fill the entire slide background with the layout's background colour."""
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = _rgb(layout.background_color)

    def _add_eyebrow(self, slide: Slide, spec: SlideSpec, layout: LayoutSpec) -> None:
        """Add the ALL CAPS eyebrow label above the title (brand header lockup).

        Light slides: Indigo Grey #434E80 (AA-safe on Warm Peach).
        Dark slides: Orange #E9590C (AA-safe at any size on navy).
        """
        if not layout.show_brand_header:
            return
        text = spec.eyebrow.upper() if spec.eyebrow else spec.title[:30].upper()
        box = slide.shapes.add_textbox(
            Inches(0.5), Inches(layout.eyebrow_top_inches),
            Inches(9.0), Inches(0.28),
        )
        tf = box.text_frame
        tf.word_wrap = False
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = text
        run.font.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = _rgb(layout.eyebrow_color)
        run.font.name = layout.font_family
        # letter spacing: charSpacing equivalent via XML is not needed; standard tracking

    def _add_tick_rule(self, slide: Slide, layout: LayoutSpec) -> None:
        """Add a short orange tick rule below the eyebrow (~0.77\" wide, 2pt tall)."""
        if not layout.show_brand_header:
            return
        rule = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(0.5), Inches(0.56),
            Inches(0.77), Inches(0.03),
        )
        rule.fill.solid()
        rule.fill.fore_color.rgb = _rgb("#E9590C")  # Always orange, per brand spec
        rule.line.width = 0

    def _add_title(self, slide: Slide, spec: SlideSpec, layout: LayoutSpec) -> None:
        """Add the main title text box at the position defined in the layout."""
        box = slide.shapes.add_textbox(
            Inches(layout.title_left_inches),
            Inches(layout.title_top_inches),
            Inches(layout.title_width_inches),
            Inches(layout.title_height_inches),
        )
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = spec.title
        run.font.bold = True
        run.font.size = Pt(layout.title_font_size)
        run.font.color.rgb = _rgb(layout.title_color)
        run.font.name = layout.font_family

    def _add_intro_line(self, slide: Slide, spec: SlideSpec, layout: LayoutSpec) -> None:
        """Add the one-line intro sentence below the title (brand header lockup)."""
        if not layout.show_brand_header:
            return
        text = spec.intro_line or spec.key_message
        if not text:
            return
        box = slide.shapes.add_textbox(
            Inches(layout.title_left_inches),
            Inches(layout.intro_top_inches),
            Inches(layout.content_width_inches),
            Inches(0.40),
        )
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = text
        run.font.size = Pt(10)
        run.font.color.rgb = _rgb(layout.intro_color)
        run.font.name = layout.font_family

    def _add_subtitle_line(
        self,
        slide: Slide,
        text: str,
        layout: LayoutSpec,
        top_offset: float = 0.0,
    ) -> None:
        box = slide.shapes.add_textbox(
            Inches(layout.content_left_inches),
            Inches(layout.content_top_inches + top_offset),
            Inches(layout.content_width_inches),
            Inches(0.55),
        )
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = text
        run.font.size = Pt(layout.subtitle_font_size)
        run.font.italic = True
        run.font.color.rgb = _rgb(layout.accent_color)
        run.font.name = layout.font_family

    def _add_key_message(self, slide: Slide, spec: SlideSpec, layout: LayoutSpec) -> None:
        if not spec.key_message:
            return
        box = slide.shapes.add_textbox(
            Inches(layout.content_left_inches),
            Inches(layout.content_top_inches),
            Inches(layout.content_width_inches),
            Inches(0.50),
        )
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = spec.key_message
        run.font.bold = True
        run.font.size = Pt(layout.subtitle_font_size)
        run.font.color.rgb = _rgb(layout.body_color)
        run.font.name = layout.font_family

    def _add_bullets(self, slide: Slide, spec: SlideSpec, layout: LayoutSpec) -> None:
        """Add bullet points as one native text box with one paragraph per bullet."""
        if not spec.bullets:
            return
        bullet_top = layout.content_top_inches + 0.58
        available_height = layout.content_height_inches - 0.58
        box = slide.shapes.add_textbox(
            Inches(layout.content_left_inches),
            Inches(bullet_top),
            Inches(layout.content_width_inches),
            Inches(max(available_height, 0.5)),
        )
        tf = box.text_frame
        tf.word_wrap = True

        for idx, bullet in enumerate(spec.bullets):
            para = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
            run = para.add_run()
            run.text = f"\u25aa  {bullet}"
            run.font.size = Pt(layout.body_font_size)
            run.font.color.rgb = _rgb(layout.body_color)
            run.font.name = layout.font_family
            para.space_after = Pt(6)

    def _add_takeaway_bar(self, slide: Slide, spec: SlideSpec, layout: LayoutSpec) -> None:
        """Add the full-width navy bottom bar with the slide's 'so what' sentence."""
        if not spec.takeaway or not layout.show_brand_header:
            return
        # Full-width navy bar
        bar = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(0.0), Inches(layout.takeaway_top_inches),
            Inches(13.33), Inches(layout.takeaway_height_inches),
        )
        bar.fill.solid()
        bar.fill.fore_color.rgb = _rgb(layout.takeaway_bg_color)
        bar.line.width = 0

        # Takeaway text centered in the bar
        box = slide.shapes.add_textbox(
            Inches(0.5), Inches(layout.takeaway_top_inches + 0.08),
            Inches(12.33), Inches(layout.takeaway_height_inches - 0.12),
        )
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        run = p.add_run()
        run.text = spec.takeaway
        run.font.bold = True
        run.font.size = Pt(10)
        run.font.color.rgb = _rgb(layout.takeaway_text_color)
        run.font.name = layout.font_family

    def _add_speaker_notes(self, slide: Slide, spec: SlideSpec) -> None:
        """Write speaker_notes into the slide's notes pane."""
        if not spec.speaker_notes:
            return
        notes_tf = slide.notes_slide.notes_text_frame
        notes_tf.text = spec.speaker_notes

    def _add_slide_number(self, slide: Slide, number: int, layout: LayoutSpec) -> None:
        if layout.slide_type in (SlideType.TITLE, SlideType.CLOSING):
            return
        box = slide.shapes.add_textbox(
            Inches(12.55), Inches(7.1),
            Inches(0.65), Inches(0.32),
        )
        tf = box.text_frame
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.RIGHT
        run = p.add_run()
        run.text = str(number)
        run.font.size = Pt(9)
        run.font.color.rgb = _rgb("#AAAAAA")

    def render(
        self,
        slide: Slide,
        spec: SlideSpec,
        layout: LayoutSpec,
    ) -> None:
        """Apply all text layers to *slide* following the ML arteka brand header lockup."""
        self._set_background(slide, layout)
        # Brand header lockup (content/agenda slides only): eyebrow → tick rule → title → intro
        self._add_eyebrow(slide, spec, layout)
        self._add_tick_rule(slide, layout)
        self._add_title(slide, spec, layout)
        self._add_intro_line(slide, spec, layout)

        if spec.slide_type in (SlideType.TITLE, SlideType.CLOSING):
            if spec.subtitle:
                self._add_subtitle_line(slide, spec.subtitle, layout)
            elif spec.key_message:
                self._add_subtitle_line(slide, spec.key_message, layout)
        elif spec.slide_type in (SlideType.AGENDA, SlideType.SECTION_DIVIDER):
            self._add_key_message(slide, spec, layout)
            self._add_bullets(slide, spec, layout)
        else:
            # CONTENT
            self._add_key_message(slide, spec, layout)
            self._add_bullets(slide, spec, layout)
            self._add_takeaway_bar(slide, spec, layout)

        self._add_slide_number(slide, spec.slide_number, layout)
        self._add_speaker_notes(slide, spec)
