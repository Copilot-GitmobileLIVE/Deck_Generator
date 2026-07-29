"""
gemini_provider.py — Google Gemini image generation provider (Nano Banana family)

Supported models (as of 2026):
    gemini-2.5-flash-preview-05-20   — fast, cost-effective default
    gemini-3-pro-image-preview        — highest quality, 4K, no free tier
    gemini-3.1-flash-image-preview    — Nano Banana 2 balanced option

NOTE: Google's older Imagen models (imagen-3.0-generate-002 etc.) are being
retired mid-August 2026.  This provider uses the Gemini generate_content API
with IMAGE response modality — do NOT switch back to Imagen endpoints.

Why Gemini for consulting decks:
    - Better at diagram-style and infographic visuals than OpenAI
    - More stylistic variety for process flows and architecture illustrations
    - Lower cost on the flash tier (~$0.015 vs ~$0.04 for OpenAI)

Async note:
    The google-genai SDK is synchronous.  We run it in a thread pool via
    `loop.run_in_executor(None, lambda: ...)` to avoid blocking the asyncio
    event loop while the SDK makes its HTTP call.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from .base import ImageGenerationResult, ImageProvider

logger = logging.getLogger("deck_generator.gemini_provider")


class GeminiImageProvider(ImageProvider):
    """Generates images via Google Gemini generate_content with IMAGE modality.

    Gemini advantages vs. OpenAI:
    - Better for diagram-style, illustrative, and technical visuals
    - Lower cost on flash tier
    - More stylistic variety for infographics / process flows
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash-preview-05-20",
    ) -> None:
        self._api_key = api_key
        self._model = model
        # Lazy-initialise the SDK client so import errors are caught at runtime
        # (not at module import time), giving a clearer error message.
        self._client: Optional[object] = None

    def _get_client(self) -> object:
        """Lazily initialise and return the google-genai client.

        Lazy initialisation means the google-genai package is only imported
        when the first image is actually generated.  This prevents import
        errors from breaking the whole pipeline if the package isn’t installed
        or the API key is not needed in the current run.
        """
        if self._client is None:
            try:
                from google import genai  # type: ignore[import]

                self._client = genai.Client(api_key=self._api_key)
            except ImportError as exc:
                raise ImportError(
                    "google-genai is not installed. Run: pip install google-genai"
                ) from exc
        return self._client

    @property
    def provider_name(self) -> str:
        return "gemini"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=5, max=30),
        reraise=False,
    )
    async def generate_image(
        self,
        prompt: str,
        slide_number: int,
        output_dir: str,
        size: str = "1536x1024",  # not used by Gemini; kept for interface parity
    ) -> ImageGenerationResult:
        start = time.perf_counter()
        output_path = str(Path(output_dir) / f"slide_{slide_number:02d}_gemini.png")

        try:
            client = self._get_client()
            from google.genai import types  # type: ignore[import]

            # Gemini SDK is synchronous — run in a thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                    ),
                ),
            )

            # Extract first image part from response
            image_bytes: Optional[bytes] = None
            for part in response.candidates[0].content.parts:
                if getattr(part, "inline_data", None) is not None:
                    raw = part.inline_data.data
                    # SDK may return bytes or a base64 string depending on version
                    if isinstance(raw, bytes):
                        image_bytes = raw
                    else:
                        image_bytes = base64.b64decode(raw)
                    break

            if not image_bytes:
                raise ValueError("Gemini returned no image data in response parts")

            Path(output_dir).mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(image_bytes)

            duration = time.perf_counter() - start
            logger.info(
                "Gemini: slide %02d generated in %.1fs → %s",
                slide_number, duration, output_path,
            )
            return ImageGenerationResult(
                success=True,
                image_path=output_path,
                provider=self.provider_name,
                prompt=prompt,
                cost_estimate=0.015,
                generation_duration_seconds=duration,
            )

        except Exception as exc:
            duration = time.perf_counter() - start
            logger.error(
                "Gemini: slide %02d FAILED after %.1fs — %s",
                slide_number, duration, exc,
            )
            return ImageGenerationResult(
                success=False,
                image_path="",
                provider=self.provider_name,
                prompt=prompt,
                generation_duration_seconds=duration,
                error=str(exc),
            )
