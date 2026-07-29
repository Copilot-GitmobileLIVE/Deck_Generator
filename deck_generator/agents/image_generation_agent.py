"""
image_generation_agent.py — Image Generation Agent

Responsibility:
    Send image generation requests to OpenAI and Gemini concurrently, collect
    the resulting PNG files, and return all GeneratedImage records for review.

Parallelism strategy:
    For each slide, the agent fires up to two provider calls simultaneously
    using asyncio.gather().  Then ALL slides are also gathered in parallel, so
    for N slides and 2 providers you get up to 2N concurrent API calls.

    Provider call topology:
        slide_1 → asyncio.gather(openai_call, gemini_call)   ┬
        slide_2 → asyncio.gather(openai_call, gemini_call)   ├─ asyncio.gather
        slide_N → asyncio.gather(openai_call, gemini_call)   ┘

Error handling:
    Each provider wraps its call in a try/except and returns a GeneratedImage
    with success=False on failure (via the ImageProvider base class contract).
    asyncio.gather uses return_exceptions=True so one failed provider does not
    cancel the other.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List

from deck_generator.config import get_settings
from deck_generator.image_providers.gemini_provider import GeminiImageProvider
from deck_generator.image_providers.openai_provider import OpenAIImageProvider
from deck_generator.models import DeckState, GeneratedImage, ImageRequest

logger = logging.getLogger("deck_generator.image_generation_agent")


class ImageGenerationAgent:
    """Parallelises image generation across OpenAI and Gemini providers.

    Why generate from both providers?
    -----------------------------------
    OpenAI (gpt-image-2) excels at photorealistic, business-photography style
    images.  Gemini (Nano Banana family) produces better results for diagrams,
    infographics, and abstract conceptual art.  By generating from both and
    letting ImageReviewAgent pick the winner, the pipeline gets the best of
    each provider per slide type automatically.
    """

    def __init__(self) -> None:
        s = get_settings()
        s.ensure_dirs()  # Create output/images/ before any API calls write files there
        self._output_dir = s.images_dir
        # Instantiate both providers now so __init__ is the only place with
        # API key handling — easier to mock in tests.
        self._openai = OpenAIImageProvider(api_key=s.openai_api_key, model=s.model_image_openai)
        self._gemini = GeminiImageProvider(api_key=s.gemini_api_key, model=s.model_image_gemini)

    async def _generate_for_request(
        self, req: ImageRequest
    ) -> List[GeneratedImage]:
        """Fire one or both providers for a single image request.

        Args:
            req: The ImageRequest for one slide.

        Returns:
            A list of GeneratedImage records (0–2 items, depending on how
            many providers were called and how many succeeded).
        """
        tasks = []
        # Decide which providers to call based on the VisualAgent's preference.
        # None means "call both and let review pick the winner".
        use_openai = req.preferred_provider in (None, "openai")
        use_gemini = req.preferred_provider in (None, "gemini")

        if use_openai:
            tasks.append(
                self._openai.generate_image(
                    prompt=req.prompt,
                    slide_number=req.slide_number,
                    output_dir=self._output_dir,
                )
            )
        if use_gemini:
            tasks.append(
                self._gemini.generate_image(
                    prompt=req.prompt,
                    slide_number=req.slide_number,
                    output_dir=self._output_dir,
                )
            )

        # return_exceptions=True means a provider failure returns an Exception
        # object instead of propagating it — so the other provider's result is
        # still collected.
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        out: List[GeneratedImage] = []
        for result in raw_results:
            if isinstance(result, Exception):
                # This branch only fires if the provider's own try/except failed
                # to catch something unexpected.
                logger.error("Unhandled exception during image generation: %s", result)
                continue
            # Convert the provider's ImageGenerationResult dataclass into a
            # Pydantic GeneratedImage for the shared state.
            out.append(
                GeneratedImage(
                    slide_number=req.slide_number,
                    provider=result.provider,
                    prompt=result.prompt,
                    image_path=result.image_path,
                    cost_estimate=result.cost_estimate,
                    generation_duration_seconds=result.generation_duration_seconds,
                    success=result.success,
                    error=result.error,
                )
            )

        # Fallback: if the preferred provider produced no successful images,
        # try the other provider so slides are never left without an image
        # simply because one API key is missing or rate-limited.
        if not any(img.success for img in out):
            fallback_tasks = []
            if not use_openai:
                logger.warning(
                    "Slide %d: preferred provider 'gemini' failed — falling back to OpenAI",
                    req.slide_number,
                )
                fallback_tasks.append(
                    self._openai.generate_image(
                        prompt=req.prompt,
                        slide_number=req.slide_number,
                        output_dir=self._output_dir,
                    )
                )
            elif not use_gemini:
                logger.warning(
                    "Slide %d: preferred provider 'openai' failed — falling back to Gemini",
                    req.slide_number,
                )
                fallback_tasks.append(
                    self._gemini.generate_image(
                        prompt=req.prompt,
                        slide_number=req.slide_number,
                        output_dir=self._output_dir,
                    )
                )
            if fallback_tasks:
                fallback_results = await asyncio.gather(*fallback_tasks, return_exceptions=True)
                for result in fallback_results:
                    if isinstance(result, Exception):
                        logger.error("Fallback provider also failed: %s", result)
                        continue
                    out.append(
                        GeneratedImage(
                            slide_number=req.slide_number,
                            provider=result.provider,
                            prompt=result.prompt,
                            image_path=result.image_path,
                            cost_estimate=result.cost_estimate,
                            generation_duration_seconds=result.generation_duration_seconds,
                            success=result.success,
                            error=result.error,
                        )
                    )

        return out

    async def run(self, state: DeckState) -> dict:
        """Generate all images in parallel and return a state update.

        Args:
            state: Must have `image_requests` populated by VisualAgent.

        Returns:
            Dict with `generated_images` (List[GeneratedImage]) and updated
            status/logs.
        """
        requests = state.image_requests
        if not requests:
            logger.warning("ImageGenerationAgent: no image requests in state — skipping")
            return {
                "generated_images": [],
                "status": "image_generation_complete",
                "execution_logs": state.execution_logs + ["ImageGenerationAgent: no requests"],
            }

        logger.info(
            "ImageGenerationAgent: generating images for %d slides", len(requests)
        )

        # Outer gather: all slides run in parallel.
        # Each item in per_slide_tasks is itself a coroutine that may internally
        # gather two provider calls (inner gather in _generate_for_request).
        per_slide_tasks = [self._generate_for_request(req) for req in requests]
        per_slide_results = await asyncio.gather(*per_slide_tasks)

        # Flatten the list-of-lists into a single list.
        all_images: List[GeneratedImage] = [
            img for batch in per_slide_results for img in batch
        ]

        successful = [i for i in all_images if i.success]
        failed = [i for i in all_images if not i.success]
        total_cost = sum(i.cost_estimate for i in successful)

        log_entry = (
            f"ImageGenerationAgent: {len(successful)} succeeded, "
            f"{len(failed)} failed, ~${total_cost:.3f} estimated cost"
        )
        logger.info(log_entry)

        return {
            "generated_images": all_images,
            "status": "image_generation_complete",
            "execution_logs": state.execution_logs + [log_entry],
        }
