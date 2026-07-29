"""
openai_provider.py — OpenAI image generation provider

Model: gpt-image-2 (flagship as of 2026)

Why gpt-image-2 is preferred for consulting decks:
    - "Reasoning before generating": the model thinks about the prompt before
      drawing, which produces more coherent and relevant compositions
    - Excellent text rendering within images (useful for diagrams with labels)
    - Up to 4K output resolution
    - Flexible aspect ratios including 1536x1024 (3:2 landscape, close to 16:9)

Retry strategy:
    The @retry decorator from tenacity retries up to 3 times with exponential
    back-off (5s, 10s, 20s waits) on any exception.  `reraise=False` means
    if all retries fail the decorator returns None, so our own try/except
    catches it and returns success=False — the pipeline continues.

Response format:
    We request `response_format="b64_json"` so the image bytes arrive in the
    API response body rather than needing a separate download step.  This is
    more reliable than a URL that could expire.
"""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Dict

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import ImageGenerationResult, ImageProvider

logger = logging.getLogger("deck_generator.openai_provider")

# Conservative cost estimates in USD per image
_COST_TABLE: Dict[str, float] = {
    "gpt-image-2": 0.04,
    "gpt-image-1.5": 0.03,
    "gpt-image-1": 0.02,
}


class OpenAIImageProvider(ImageProvider):
    """Generates images using OpenAI's gpt-image-2 flagship model.

    gpt-image-2 advantages:
    - Reasons before generating ("thinking" mode improves coherence)
    - Strong multilingual text rendering within the image
    - Up to 4K output resolution
    - Flexible aspect ratios: 1024x1024, 1536x1024, 1024x1536
    """

    def __init__(self, api_key: str, model: str = "gpt-image-2") -> None:
        # AsyncOpenAI is the async-compatible client — required because all
        # node functions in the pipeline are async.
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    @property
    def provider_name(self) -> str:
        return "openai"

    @retry(
        stop=stop_after_attempt(3),                    # Give up after 3 attempts
        wait=wait_exponential(multiplier=1, min=5, max=30),  # Wait 5s, 10s, 20s between retries
        reraise=False,  # Don’t re-raise — return None so our except block handles it
    )
    async def generate_image(
        self,
        prompt: str,
        slide_number: int,
        output_dir: str,
        size: str = "1536x1024",  # 3:2 landscape — closest standard ratio to 16:9
    ) -> ImageGenerationResult:
        """Call the OpenAI Images API and save the result as a PNG.

        Args:
            prompt:       The image generation prompt.
            slide_number: Used to build the output filename (e.g. slide_03_openai.png).
            output_dir:   Directory where the PNG will be saved.
            size:         WxH in pixels.  Valid values: 1024x1024, 1536x1024, 1024x1536.

        Returns:
            ImageGenerationResult with success=True and a valid image_path on
            success, or success=False with an error message on failure.
        """
        start = time.perf_counter()  # Wall-clock start for duration tracking
        output_path = str(Path(output_dir) / f"slide_{slide_number:02d}_openai.png")

        try:
            response = await self._client.images.generate(
                model=self._model,
                prompt=prompt,
                size=size,  # type: ignore[arg-type]
                quality="high",       # Use "high" for best quality (costs more than "standard")
                n=1,                  # Generate exactly one image
                # response_format is not supported by gpt-image-1/gpt-image-2;
                # these models always return b64_json in response.data[0].b64_json.
            )
            b64_data = response.data[0].b64_json
            if not b64_data:
                raise ValueError("OpenAI returned empty b64_json")

            # Decode the base64 string back to raw PNG bytes and write to disk.
            image_bytes = base64.b64decode(b64_data)
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(image_bytes)

            duration = time.perf_counter() - start
            logger.info(
                "OpenAI: slide %02d generated in %.1fs → %s",
                slide_number, duration, output_path,
            )
            return ImageGenerationResult(
                success=True,
                image_path=output_path,
                provider=self.provider_name,
                prompt=prompt,
                cost_estimate=_COST_TABLE.get(self._model, 0.04),
                generation_duration_seconds=duration,
            )

        except Exception as exc:
            duration = time.perf_counter() - start
            logger.error(
                "OpenAI: slide %02d FAILED after %.1fs — %s",
                slide_number, duration, exc,
            )
            # Return a failure result — the pipeline continues without this image.
            return ImageGenerationResult(
                success=False,
                image_path="",
                provider=self.provider_name,
                prompt=prompt,
                generation_duration_seconds=duration,
                error=str(exc),
            )
