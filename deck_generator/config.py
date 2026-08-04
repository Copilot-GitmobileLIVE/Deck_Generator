"""
config.py — Central application configuration.

All settings are read from the .env file (or real environment variables) at
startup via pydantic-settings.  Every agent, provider, and the API server
import `get_settings()` to access keys, model names, and file paths.
There is intentionally no hard-coded config anywhere else in the codebase —
all tuneable values live here.

Quick-start:
    1. Copy `.env.example` to `.env`.
    2. Fill in OPENAI_API_KEY (required) and GEMINI_API_KEY (optional).
    3. Override model names, output paths, or skill paths as needed.
    4. The singleton is cached via @lru_cache so the .env file is only
       read once for the entire lifetime of the process.

Environment variable mapping (case-insensitive):
    OPENAI_API_KEY, GEMINI_API_KEY, MODEL_CONTENT, OUTPUT_DIR, …
    Any key in Settings can be overridden via an env var of the same name.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Pydantic-settings model that maps environment variables to typed fields.

    pydantic-settings will automatically read values from:
      1. Real environment variables (highest priority)
      2. The .env file in the working directory
      3. The default values defined here (lowest priority)

    Field names are case-insensitive, so OPENAI_API_KEY and openai_api_key
    are treated identically.
    """

    # Tell pydantic-settings where the .env file lives and how to parse it.
    model_config = SettingsConfigDict(
        env_file=".env",           # path relative to the working directory
        env_file_encoding="utf-8",
        case_sensitive=False,      # OPENAI_API_KEY == openai_api_key
        extra="ignore",            # silently ignore unknown .env keys
    )

    # ── API keys ──────────────────────────────────────────────────────────────
    # These are passed directly to the OpenAI and Google SDK clients.
    # Never commit real keys — keep them in .env which is git-ignored.
    openai_api_key: str = ""   # Used by ContentAgent, ImageReviewAgent, OpenAIImageProvider
    gemini_api_key: str = ""   # Used by GeminiImageProvider

    # ── Model selection ───────────────────────────────────────────────────────
    # Changing a model here upgrades/downgrades the whole pipeline without
    # touching any agent code.
    model_content: str = "gpt-4o"                        # ContentAgent + VisualAgent (text tasks)
    model_image_openai: str = "gpt-image-2"              # OpenAI image generation flagship
    model_image_gemini: str = "gemini-2.5-flash-preview-05-20"  # Gemini Nano Banana image model
    model_review: str = "gpt-4o"                         # ImageReviewAgent (vision scoring)
    model_layout: str = "gpt-4o"                         # Reserved for future LLM-driven layout
    model_qa: str = "gpt-4o"                             # Reserved for future LLM-driven QA

    # ── Output paths ──────────────────────────────────────────────────────────
    # All generated files land here.  Relative paths are resolved from the
    # current working directory when run_demo.py is launched.
    output_dir: str = "output"          # Final .pptx files are saved here
    images_dir: str = "output/images"   # PNG images downloaded from OpenAI / Gemini

    # ── Brand skill ───────────────────────────────────────────────────────────
    # The .skill archive and the directory where it is (or will be) extracted.
    # skill_loader.py reads from skill_extract_dir first; falls back to
    # extracting the ZIP if the extracted SKILL.md is absent.
    skill_file: str = "mlarteka-pptx.skill"        # ZIP archive at project root
    skill_extract_dir: str = "skill_extracted"      # Target for auto-extraction

    # ── Workflow tuning ───────────────────────────────────────────────────────
    max_retries: int = 2          # How many times QA can send the pipeline back to ContentAgent
    image_generation_timeout: int = 120   # Per-image timeout in seconds (not yet enforced via asyncio)
    content_temperature: float = 0.4     # Slight creativity for narrative; 0 = deterministic
    review_temperature: float = 0.1      # Near-deterministic for image scoring decisions

    def ensure_dirs(self) -> None:
        """Create output_dir and images_dir on disk if they do not already exist.

        Called by AssemblyAgent and ImageGenerationAgent before writing files.
        Using `parents=True` means nested paths like 'output/images' are created
        in one call without needing to create 'output' separately first.
        """
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        Path(self.images_dir).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide singleton Settings instance.

    The `@lru_cache` decorator ensures the .env file is only read once,
    regardless of how many agents call this function.  In tests you can
    clear the cache with `get_settings.cache_clear()` before patching env vars.
    """
    return Settings()
