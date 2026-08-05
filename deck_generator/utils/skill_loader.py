"""
skill_loader.py — ML arteka brand skill loader

Responsibility:
    Load the mlarteka-pptx brand skill at agent startup so all LLM prompts
    draw from the authoritative SKILL.md rather than summarised static strings.

How it works:
    1. Looks for an already-extracted SKILL.md at skill_extract_dir/mlarteka-pptx/SKILL.md.
    2. If missing, extracts the .skill ZIP archive (skill_file) into skill_extract_dir.
    3. Parses SKILL.md into a dict of section_heading → section_body using ## headings.
    4. Returns a BrandSkill instance that agents query by section name.

Usage::

    from deck_generator.utils.skill_loader import load_brand_skill

    skill = load_brand_skill()
    content_rules = skill.get_section("Content Rules")
"""
from __future__ import annotations

import logging
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("deck_generator.skill_loader")

# Sub-directory inside the extract root that the .skill archive uses as its prefix.
_SKILL_SUBFOLDER = "mlarteka-pptx"
_SKILL_MD = "SKILL.md"


class BrandSkill:
    """Parsed, queryable view of the mlarteka-pptx SKILL.md."""

    def __init__(self, sections: Dict[str, str], skill_path: Path) -> None:
        self._sections = sections
        self.skill_path = skill_path  # path to the SKILL.md that was loaded

    # ── Public API ────────────────────────────────────────────────────────────

    def get_section(self, heading: str) -> str:
        """Return the full body text of a ## section, or '' if not found.

        The match is case-insensitive and strips leading/trailing whitespace.
        """
        key = heading.strip().lower()
        for k, v in self._sections.items():
            if k.lower() == key:
                return v.strip()
        return ""

    def get_sections(self, *headings: str) -> str:
        """Return multiple sections joined by a blank line."""
        parts = [self.get_section(h) for h in headings if self.get_section(h)]
        return "\n\n".join(parts)

    @staticmethod
    def _escape_braces(text: str) -> str:
        """Escape { and } so LangChain f-string templates don't treat them as variables."""
        return text.replace("{", "{{").replace("}", "}}")

    def content_rules_prompt(self) -> str:
        """Return the brand content rules block for injection into ContentAgent."""
        return self._escape_braces(self.get_sections(
            "Fixed Slide Rules",
            "Typography",
            "Content Rules",
        ))

    def image_prompt_rules(self) -> str:
        """Return the visual storytelling and image-prompt convention block for injection into VisualAgent."""
        return self._escape_braces(self.get_section("Visual Storytelling"))

    def color_palette_text(self) -> str:
        """Return the raw color palette section for reference (unescaped)."""
        return self.get_section("Color Palette")

    def all_section_names(self) -> list[str]:
        return list(self._sections.keys())


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_sections(md: str) -> Dict[str, str]:
    """Split a markdown string on ## headings into {heading: body} dict.

    Only top-level ## headings are used as section boundaries; ### and deeper
    headings are kept as body text inside their parent section.
    """
    sections: Dict[str, str] = {}
    # Split on lines that start with exactly '## ' (not ### or deeper)
    parts = re.split(r"^## (.+)$", md, flags=re.MULTILINE)
    # parts = [pre-amble, heading1, body1, heading2, body2, ...]
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        sections[heading] = body
    return sections


# ── Extraction ────────────────────────────────────────────────────────────────

def _extract_skill(skill_file: Path, extract_dir: Path) -> Path:
    """Extract the .skill ZIP into extract_dir and return the SKILL.md path."""
    if not skill_file.exists():
        raise FileNotFoundError(f"Skill archive not found: {skill_file}")
    if not zipfile.is_zipfile(skill_file):
        raise ValueError(f"{skill_file} is not a valid ZIP / .skill archive")

    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(skill_file, "r") as zf:
        zf.extractall(extract_dir)
    logger.info("skill_loader: extracted %s → %s", skill_file.name, extract_dir)

    md_path = extract_dir / _SKILL_SUBFOLDER / _SKILL_MD
    if not md_path.exists():
        raise FileNotFoundError(
            f"SKILL.md not found inside archive at expected path: {md_path}"
        )
    return md_path


# ── Public loader ─────────────────────────────────────────────────────────────

def load_brand_skill(
    skill_file: Optional[str] = None,
    extract_dir: Optional[str] = None,
) -> BrandSkill:
    """Load and return the BrandSkill.

    Resolution order for SKILL.md:
      1. skill_extract_dir/mlarteka-pptx/SKILL.md  (pre-extracted, fastest path)
      2. Extract from skill_file (ZIP) into extract_dir, then read

    Raises FileNotFoundError if neither source is available.
    """
    from deck_generator.config import get_settings  # avoid circular at module level

    s = get_settings()
    skill_file_path = Path(skill_file or s.skill_file)
    extract_dir_path = Path(extract_dir or s.skill_extract_dir)
    md_path = extract_dir_path / _SKILL_SUBFOLDER / _SKILL_MD

    if not md_path.exists():
        logger.info("skill_loader: SKILL.md not found at %s — extracting from .skill archive", md_path)
        md_path = _extract_skill(skill_file_path, extract_dir_path)
    else:
        logger.debug("skill_loader: using pre-extracted SKILL.md at %s", md_path)

    content = md_path.read_text(encoding="utf-8")
    sections = _parse_sections(content)
    logger.info(
        "skill_loader: loaded %d sections from %s", len(sections), md_path.name
    )
    return BrandSkill(sections=sections, skill_path=md_path)


@lru_cache(maxsize=1)
def get_brand_skill() -> BrandSkill:
    """Process-wide singleton BrandSkill, loaded once and cached."""
    return load_brand_skill()
