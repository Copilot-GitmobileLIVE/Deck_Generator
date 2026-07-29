"""
graph.py — LangGraph workflow definition

This file is the brain of the system.  It wires all agents together into a
directed graph and controls the execution order and branching logic.

How LangGraph works (key concepts):
    StateGraph   — A graph whose nodes share a single Pydantic state object
                   (DeckState).  Every node reads the full state, does its
                   work, and returns a dict of fields to update.

    Node         — A Python function (sync or async) that takes DeckState
                   and returns Dict[str, Any].  LangGraph calls model_copy()
                   to merge the returned dict into the current state.

    Edge         — A directed connection between two nodes.  add_edge(A, B)
                   means A always runs before B.

    Conditional edge — A function inspects the state and returns a string
                   key that LangGraph maps to the next node.  Used here to
                   route back to ContentAgent when QA fails.

Pipeline topology:
    orchestrator → content → visual → image_generation
        → image_review → layout → assembly → qa
        → route_after_qa() → {"retry": content, "finish": END}
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from langgraph.graph import END, StateGraph

from deck_generator.agents.assembly_agent import AssemblyAgent
from deck_generator.agents.content_agent import ContentAgent
from deck_generator.agents.image_generation_agent import ImageGenerationAgent
from deck_generator.agents.image_review_agent import ImageReviewAgent
from deck_generator.agents.layout_agent import LayoutAgent
from deck_generator.agents.qa_agent import QAAgent
from deck_generator.agents.visual_agent import VisualAgent
from deck_generator.models import DeckState

logger = logging.getLogger("deck_generator.workflow")


# ── Node functions ────────────────────────────────────────────────────────────
# Each node function follows the same signature:
#     async def <name>_node(state: DeckState) -> Dict[str, Any]
#
# The function receives the full current state, does its work (possibly calling
# an LLM or external API), and returns a dict containing ONLY the fields it
# wants to change.  LangGraph merges that dict into the state via model_copy().
# Fields not mentioned in the returned dict are left unchanged.

async def orchestrator_node(state: DeckState) -> Dict[str, Any]:
    """Entry point — logs the brief title and initialises the pipeline status.

    The orchestrator does not call any LLM or external service.  Its sole
    job is to set the initial status tag and append a log entry so the
    execution trace starts cleanly.
    """
    brief = state.deck_brief
    title = brief.title if brief else "unknown"
    log_entry = f"Orchestrator: pipeline started for '{title}'"
    logger.info(log_entry)
    return {
        "status": "orchestrator_initialized",
        "execution_logs": state.execution_logs + [log_entry],
    }


async def content_node(state: DeckState) -> Dict[str, Any]:
    """Run ContentAgent to produce the slide narrative (SlideSpec list)."""
    return await ContentAgent().run(state)


async def visual_node(state: DeckState) -> Dict[str, Any]:
    """Run VisualAgent to convert SlideSpecs into ImageRequest prompts."""
    return await VisualAgent().run(state)


async def image_generation_node(state: DeckState) -> Dict[str, Any]:
    """Run ImageGenerationAgent to call OpenAI + Gemini APIs in parallel."""
    return await ImageGenerationAgent().run(state)


async def image_review_node(state: DeckState) -> Dict[str, Any]:
    """Run ImageReviewAgent (GPT-4o Vision) to select the best image per slide."""
    return await ImageReviewAgent().run(state)


def layout_node(state: DeckState) -> Dict[str, Any]:
    """Run LayoutAgent (pure logic) to produce LayoutSpec for every slide."""
    return LayoutAgent().run(state)


def assembly_node(state: DeckState) -> Dict[str, Any]:
    """Run AssemblyAgent to render all slides into a .pptx file via PPTBuilder."""
    return AssemblyAgent().run(state)


def qa_node(state: DeckState) -> Dict[str, Any]:
    """Run QAAgent to validate the deck and set retry_count if issues are found."""
    return QAAgent().run(state)


# ── Conditional routing ───────────────────────────────────────────────────────

def route_after_qa(state: DeckState) -> str:
    """Decide what happens after the QA check.

    LangGraph calls this function with the current state after qa_node runs.
    The function returns a string key that LangGraph maps to the next node
    via the conditional edges dict:
        {"retry": "content", "finish": END}

    Retry logic:
        - If QA failed AND we have not exhausted max_retries, return "retry".
          The graph routes back to content_node for a full regeneration pass.
        - If QA failed AND retries are exhausted, log an error and return
          "finish" anyway so the caller receives whatever PPTX was produced.
        - If QA passed, return "finish" immediately.

    Args:
        state: The full DeckState after qa_node has updated qa_results
               and retry_count.

    Returns:
        "retry" → routes to content_node
        "finish" → routes to END (graph terminates)
    """
    qa = state.qa_results
    if qa and not qa.passed and state.retry_count < state.max_retries:
        logger.warning(
            "QA failed (attempt %d/%d) — routing back to content stage",
            state.retry_count, state.max_retries,
        )
        return "retry"
    if qa and not qa.passed:
        logger.error(
            "QA failed after %d/%d retries — finishing with unresolved issues",
            state.retry_count, state.max_retries,
        )
    return "finish"


# ── Graph factory ─────────────────────────────────────────────────────────────

def build_deck_graph():
    """Construct and compile the LangGraph StateGraph.

    Pipeline topology::

        orchestrator → content → visual → image_generation
            → image_review → layout → assembly → qa
            → [retry → content | finish → END]

    Returns a compiled graph that accepts and returns :class:`DeckState`.
    """
    graph = StateGraph(DeckState)

    # Register nodes
    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("content", content_node)
    graph.add_node("visual", visual_node)
    graph.add_node("image_generation", image_generation_node)
    graph.add_node("image_review", image_review_node)
    graph.add_node("layout", layout_node)
    graph.add_node("assembly", assembly_node)
    graph.add_node("qa", qa_node)

    # Linear edges
    graph.set_entry_point("orchestrator")
    graph.add_edge("orchestrator", "content")
    graph.add_edge("content", "visual")
    graph.add_edge("visual", "image_generation")
    graph.add_edge("image_generation", "image_review")
    graph.add_edge("image_review", "layout")
    graph.add_edge("layout", "assembly")
    graph.add_edge("assembly", "qa")

    # Conditional exit after QA
    graph.add_conditional_edges(
        "qa",
        route_after_qa,
        {"retry": "content", "finish": END},
    )

    return graph.compile()
