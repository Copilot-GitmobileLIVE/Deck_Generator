"""
assembly_agent.py — Assembly Agent

Responsibility:
    Take all pipeline outputs that are now in DeckState (slides, layout specs,
    selected images) and produce the final .pptx file on disk.

This agent is a thin orchestration wrapper around PPTBuilder.  It is
responsible for:
    1. Deriving a descriptive output filename from the brief title + timestamp.
    2. Converting list-based state fields into the dict-keyed structures that
       PPTBuilder expects.
    3. Calling PPTBuilder.build() and storing the resulting path in state.

No LLM calls are made here — this is pure file I/O.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict

from deck_generator.config import get_settings
from deck_generator.models import DeckState, LayoutSpec, SelectedImage
from deck_generator.pptx_builder.builder import PPTBuilder

logger = logging.getLogger("deck_generator.assembly_agent")


class AssemblyAgent:
    """Drives the PPTBuilder with slides, layouts, and selected images.

    Filename convention:
        <sanitised_title>_<YYYYMMDD_HHMMSS>.pptx
    The timestamp prevents accidental overwrites when the pipeline is run
    multiple times for the same brief.
    """

    def run(self, state: DeckState) -> dict:
        """Assemble the PPTX and return a state update with the file path.

        Args:
            state: Must have `slides`, `layout_specs`, and `selected_images`
                   all populated by the preceding agents.

        Returns:
            Dict with `pptx_path` (str) and updated status/logs.
        """
        s = get_settings()
        s.ensure_dirs()  # Make sure the output directory exists before writing

        # Build a safe filename: keep only alphanumeric chars and underscores.
        brief = state.deck_brief
        base = "deck"
        if brief and brief.title:
            base = "".join(c if c.isalnum() or c == "_" else "_" for c in brief.title)
            base = base[:50]  # Limit length to avoid OS path-length issues
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(Path(s.output_dir) / f"{base}_{timestamp}.pptx")

        # Convert the state's list fields into dicts keyed by slide_number so
        # PPTBuilder can look up a layout or image in O(1) per slide.
        layouts: Dict[int, LayoutSpec] = {
            spec.slide_number: spec for spec in state.layout_specs
        }
        images: Dict[int, str] = {
            si.slide_number: si.selected_image_path
            for si in state.selected_images
            if si.selected_image_path  # Skip slides where no image was selected
        }

        builder = PPTBuilder(output_path=output_path)
        builder.build(
            slides=state.slides,
            layouts=layouts,
            images=images,
        )

        log_entry = f"AssemblyAgent: PPTX written → {output_path}"
        logger.info(log_entry)

        return {
            "pptx_path": output_path,
            "status": "assembly_complete",
            "execution_logs": state.execution_logs + [log_entry],
        }
