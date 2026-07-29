"""
image_review_agent.py — Image Review Agent

Responsibility:
    For each slide that has generated images, use GPT-4o Vision to score
    every candidate image on four dimensions and select the best one.

Scoring formula (weighted sum, 0–10 scale):
    overall = relevance * 0.35
            + quality  * 0.25
            + professionalism * 0.25
            + brand_alignment * 0.15

Why relevance gets the highest weight (0.35):
    An irrelevant image — no matter how beautiful — undermines the slide’s
    message in a consulting deck.  C-suite audiences notice mismatch.

Fallback behaviour:
    If GPT-4o Vision scoring fails (network error, quota, etc.), the agent
    assigns default neutral scores (6.0 / 10) and still returns a SelectedImage
    so the pipeline can continue.  This avoids blocking the whole deck on a
    single image scoring failure.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Dict, List

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from deck_generator.config import get_settings
from deck_generator.models import DeckState, GeneratedImage, SelectedImage, SlideSpec

logger = logging.getLogger("deck_generator.image_review_agent")

_SCORING_PROMPT = (
    "You are a creative director evaluating an AI-generated image for a slide in an "
    "enterprise consulting presentation.\n\n"
    "Slide title: {title}\n"
    "Slide key message: {key_message}\n"
    "Provider: {provider}\n\n"
    "Score the image on four dimensions (0–10 each) and return ONLY valid JSON:\n"
    '{{"relevance": <0-10>, "quality": <0-10>, "professionalism": <0-10>, '
    '"brand_alignment": <0-10>, "reason": "<one sentence>"}}'
)

# Weighted scoring: relevance matters most for consulting decks
_WEIGHTS = {"relevance": 0.35, "quality": 0.25, "professionalism": 0.25, "brand_alignment": 0.15}


class ImageReviewAgent:
    """Uses GPT-4o Vision to score and select the best image per slide.

    The agent makes one Vision API call per candidate image.  For a 10-slide
    deck where every slide had both OpenAI and Gemini generate an image, that
    is up to 20 Vision calls.  Calls are currently sequential per slide to
    avoid rate-limit issues; parallelisation can be added if latency matters.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._llm = ChatOpenAI(
            model=s.model_review,
            temperature=s.review_temperature,
            api_key=s.openai_api_key,
            max_tokens=512,
        )

    @staticmethod
    def _encode_image(path: str) -> str:
        """Read a PNG file and return its content as a base64 string.

        The OpenAI Vision API accepts images as data URIs:
            data:image/png;base64,<encoded_string>
        This helper produces the `<encoded_string>` part.
        """
        return base64.b64encode(Path(path).read_bytes()).decode("utf-8")

    @staticmethod
    def _strip_fences(raw: str) -> str:
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = lines[1:] if lines[0].startswith("```") else lines
            lines = lines[:-1] if lines and lines[-1].strip() == "```" else lines
            raw = "\n".join(lines).strip()
        return raw

    def _compute_overall(self, scores: dict) -> float:
        """Apply the weighted scoring formula to raw dimension scores.

        Weights defined in _WEIGHTS give relevance the most importance,
        reflecting the consulting-deck requirement that every visual must
        directly support its slide’s argument.
        """
        return round(
            sum(_WEIGHTS[k] * scores.get(k, 0) for k in _WEIGHTS),
            2,
        )

    async def _score_image(
        self,
        candidate: GeneratedImage,
        slide: SlideSpec,
    ) -> dict:
        """Score one image via GPT-4o Vision and return a score dict.

        The image is encoded to base64 and sent as a multimodal message.
        GPT-4o returns a JSON object with dimension scores and a reason.
        On any failure (API error, JSON parse error), neutral scores (6.0)
        are returned so the pipeline does not stall.

        Args:
            candidate: The GeneratedImage whose PNG file is to be evaluated.
            slide:     The SlideSpec provides context (title + key_message)
                       so GPT-4o can judge relevance accurately.

        Returns:
            A dict with keys: relevance, quality, professionalism,
            brand_alignment, reason.
        """
        try:
            b64 = self._encode_image(candidate.image_path)
            text = _SCORING_PROMPT.format(
                title=slide.title,
                key_message=slide.key_message,
                provider=candidate.provider,
            )
            message = HumanMessage(content=[
                {"type": "text", "text": text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{b64}",
                        "detail": "low",
                    },
                },
            ])
            response = await self._llm.ainvoke([message])
            raw = self._strip_fences(response.content)
            return json.loads(raw)
        except Exception as exc:
            logger.warning(
                "ImageReviewAgent: scoring failed for slide %d (%s): %s",
                candidate.slide_number, candidate.provider, exc,
            )
            # Fallback neutral scores so the pipeline continues
            return {
                "relevance": 6.0, "quality": 6.0,
                "professionalism": 6.0, "brand_alignment": 6.0,
                "reason": "Scoring unavailable — default score applied",
            }

    async def _select_for_slide(
        self,
        slide: SlideSpec,
        candidates: List[GeneratedImage],
    ) -> SelectedImage:
        """Iterate over all valid candidates for one slide and pick the best.

        'Valid' means success=True and the file actually exists on disk.
        Failed generations are silently excluded from the competition.
        The candidate with the highest weighted overall score is selected;
        all others are recorded in `rejected_alternatives` for audit purposes.

        Args:
            slide:      The SlideSpec this image is for.
            candidates: List of GeneratedImage objects (may include failures).

        Returns:
            A SelectedImage with the winner’s path, scores, and reasoning.
        """
        valid = [
            c for c in candidates
            if c.success and c.image_path and Path(c.image_path).exists()
        ]

        if not valid:
            return SelectedImage(
                slide_number=slide.slide_number,
                selected_image_path="",
                provider="none",
                relevance_score=0.0,
                quality_score=0.0,
                professionalism_score=0.0,
                brand_alignment_score=0.0,
                overall_score=0.0,
                selection_reason="No valid images generated for this slide",
            )

        best_candidate = valid[0]
        best_scores: dict = {}
        best_overall = -1.0
        rejected = []

        for c in valid:
            scores = await self._score_image(c, slide)
            overall = self._compute_overall(scores)
            logger.debug(
                "Slide %02d | %s → overall=%.2f", slide.slide_number, c.provider, overall
            )
            if overall > best_overall:
                if best_candidate is not valid[0] or best_overall >= 0:
                    rejected.append({"provider": best_candidate.provider, "path": best_candidate.image_path, "score": best_overall})
                best_overall = overall
                best_candidate = c
                best_scores = scores
            else:
                rejected.append({"provider": c.provider, "path": c.image_path, "score": overall})

        return SelectedImage(
            slide_number=slide.slide_number,
            selected_image_path=best_candidate.image_path,
            provider=best_candidate.provider,
            relevance_score=best_scores.get("relevance", 6.0),
            quality_score=best_scores.get("quality", 6.0),
            professionalism_score=best_scores.get("professionalism", 6.0),
            brand_alignment_score=best_scores.get("brand_alignment", 6.0),
            overall_score=best_overall if best_overall >= 0 else 6.0,
            selection_reason=best_scores.get("reason", "Best available candidate"),
            rejected_alternatives=rejected,
        )

    async def run(self, state: DeckState) -> dict:
        """Score and select the best image for every slide that has candidates.

        Slides with no generated images (e.g. if generation failed entirely)
        are skipped — they will appear without images in the final PPTX, and
        QAAgent will flag them as warnings rather than blocking errors.

        Args:
            state: Must have `slides` and `generated_images` populated.

        Returns:
            Dict with `selected_images` (List[SelectedImage]) and updated
            status/logs.
        """
        slides = state.slides
        generated = state.generated_images

        # Group generated images by slide number
        by_slide: Dict[int, List[GeneratedImage]] = {}
        for img in generated:
            by_slide.setdefault(img.slide_number, []).append(img)

        selected: List[SelectedImage] = []
        for slide in slides:
            candidates = by_slide.get(slide.slide_number, [])
            if candidates:
                result = await self._select_for_slide(slide, candidates)
                selected.append(result)
                logger.info(
                    "ImageReviewAgent: slide %02d → %s (score %.2f)",
                    slide.slide_number, result.provider, result.overall_score,
                )

        log_entry = f"ImageReviewAgent: {len(selected)} images selected"
        logger.info(log_entry)

        return {
            "selected_images": selected,
            "status": "image_review_complete",
            "execution_logs": state.execution_logs + [log_entry],
        }
