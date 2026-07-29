"""
slide_renderer.py — SlideRenderer

Responsibility:
    Add all text elements (background colour, accent bar, title, key message,
    bullet points, slide number) to a single python-pptx Slide object using
    the positioning values from a LayoutSpec.

All measurements use python-pptx's Inches() helper which converts inches to
English Metric Units (EMU) internally.  Font sizes use Pt() (points).

Rendering order per slide (shapes added top-to-bottom in z-order):
    1. Background fill        — set on the slide background object (not a shape)
    2. Accent bar (rectangle) — thin coloured strip at the top
    3. Title text box
    4. Subtitle / key message text box
    5. Bullet points text box
    6. Slide number text box   — bottom-right corner
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
        """Fill the entire slide background with the layout's background colour.

        This operates on the slide's background object directly (not an added
        shape), so it stays behind all other shapes regardless of z-order.
        """
        fill = slide.background.fill
        fill.solid()                               # Switch fill type to solid colour
        fill.fore_color.rgb = _rgb(layout.background_color)

    def _add_accent_bar(self, slide: Slide, layout: LayoutSpec) -> None:
        """Add a thin coloured rectangle at the very top of the slide.

        This is the brand accent bar used on CONTENT and AGENDA slides to
        anchor the eye at the top and signal the slide template type.
        Title, section divider, and closing slides have their own visual
        treatment (full-bleed image or dark background) so the bar is skipped.
        """
        if layout.slide_type not in (SlideType.CONTENT, SlideType.AGENDA):
            return
        bar = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE,
            Inches(0), Inches(0),       # Top-left corner of the slide
            Inches(13.33), Inches(0.30), # Full width, 0.30 inch tall
        )
        bar.fill.solid()
        bar.fill.fore_color.rgb = _rgb(layout.header_bar_color)
        bar.line.width = 0  # Remove the default 1pt border so it looks clean

    def _add_title(self, slide: Slide, spec: SlideSpec, layout: LayoutSpec) -> None:
        """Add the main title text box at the position defined in the layout.

        add_textbox() takes (left, top, width, height) in EMU.  We use Inches()
        to convert from the human-readable inch values stored in LayoutSpec.
        The text frame is set to word_wrap=True so long titles reflow within
        the box rather than overflowing to the right.
        """
        box = slide.shapes.add_textbox(
            Inches(layout.title_left_inches),
            Inches(layout.title_top_inches),
            Inches(layout.title_width_inches),
            Inches(layout.title_height_inches),
        )
        tf = box.text_frame
        tf.word_wrap = True
        # Paragraphs and runs are the two text layers in python-pptx.
        # A paragraph can contain multiple runs with different formatting,
        # but here we use a single run per paragraph.
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = spec.title
        run.font.bold = True
        run.font.size = Pt(layout.title_font_size)
        run.font.color.rgb = _rgb(layout.title_color)
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
            Inches(0.58),
        )
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = spec.key_message
        run.font.bold = True
        run.font.size = Pt(layout.subtitle_font_size)
        run.font.color.rgb = _rgb(layout.accent_color)
        run.font.name = layout.font_family

    def _add_bullets(self, slide: Slide, spec: SlideSpec, layout: LayoutSpec) -> None:
        """Add the bullet-point list below the key message text box.

        The bullet top position is offset 0.68 inches below content_top_inches
        to leave room for the key message text box that sits above it.
        Each bullet uses a Unicode filled square (▪) as the bullet character
        rather than relying on PowerPoint's auto-bullet feature, which is
        harder to control precisely with python-pptx.
        """
        if not spec.bullets:
            return
        bullet_top = layout.content_top_inches + 0.68  # Offset below key message
        available_height = layout.content_height_inches - 0.68
        box = slide.shapes.add_textbox(
            Inches(layout.content_left_inches),
            Inches(bullet_top),
            Inches(layout.content_width_inches),
            Inches(max(available_height, 0.5)),  # Ensure minimum height
        )
        tf = box.text_frame
        tf.word_wrap = True

        for idx, bullet in enumerate(spec.bullets):
            # First paragraph already exists in a new text frame;
            # subsequent ones are appended with add_paragraph().
            para = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
            run = para.add_run()
            run.text = f"▪  {bullet}"
            run.font.size = Pt(layout.body_font_size)
            run.font.color.rgb = _rgb(layout.body_color)
            run.font.name = layout.font_family
            para.space_after = Pt(5)

    def _add_speaker_notes(self, slide: Slide, spec: SlideSpec) -> None:
        """Write speaker_notes into the slide's notes pane.

        python-pptx exposes the notes pane via slide.notes_slide.  The notes
        text frame already exists on every slide; we just set its text.
        If there are no notes the pane is left empty.
        """
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
        """Apply all text layers to *slide*."""
        self._set_background(slide, layout)
        self._add_accent_bar(slide, layout)
        self._add_title(slide, spec, layout)

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

        self._add_slide_number(slide, spec.slide_number, layout)
        self._add_speaker_notes(slide, spec)
