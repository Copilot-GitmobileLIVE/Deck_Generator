"""
base.py — ImageProvider abstract base class

This module defines the contract that every image generation provider must
implement.  Adding a new provider (e.g. Stability AI, Adobe Firefly) only
requires:
    1. Create a new file that subclasses ImageProvider.
    2. Implement generate_image().
    3. Register it in ImageGenerationAgent.__init__.

The abstract interface ensures:
    - All providers return the same ImageGenerationResult dataclass so
      ImageGenerationAgent can handle results uniformly.
    - Providers never raise exceptions — they always return a result with
      success=False on failure, keeping asyncio.gather() clean.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ImageGenerationResult:
    """Normalised result returned by any image provider.

    This is a plain Python dataclass (not Pydantic) because it is only an
    internal transfer object between the provider and the agent.
    ImageGenerationAgent converts it into a Pydantic GeneratedImage before
    storing it in DeckState.
    """

    success: bool           # True if the image was generated and saved successfully
    image_path: str         # Absolute path to the saved PNG (empty string when success=False)
    provider: str           # Identifier string, e.g. "openai" or "gemini"
    prompt: str             # The exact prompt that was submitted to the API
    cost_estimate: float = 0.0                  # Approximate USD cost for this call
    generation_duration_seconds: float = 0.0   # Wall-clock time for the API call
    error: Optional[str] = None                 # Exception message when success=False


class ImageProvider(ABC):
    """Provider-agnostic interface for image generation.

    Subclasses must implement:
        provider_name  — a short identifier string (used for logging and routing)
        generate_image — the async method that calls the external API
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique identifier string for this provider (e.g. 'openai', 'gemini')."""

    @abstractmethod
    async def generate_image(
        self,
        prompt: str,
        slide_number: int,
        output_dir: str,
        size: str = "1536x1024",
    ) -> ImageGenerationResult:
        """Generate one image and persist it to *output_dir*.

        Args:
            prompt: Detailed image generation prompt.
            slide_number: Used to derive a unique filename.
            output_dir: Directory where the PNG is saved.
            size: Provider-specific size string (WxH for OpenAI; ignored by Gemini).

        Returns:
            An :class:`ImageGenerationResult` — always returned, never raised.
        """
