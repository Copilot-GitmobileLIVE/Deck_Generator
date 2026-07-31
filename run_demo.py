"""run_demo.py — entry point for the multi-agent deck generation pipeline.

Usage:
    python run_demo.py                                     # uses default brief
    python run_demo.py sample_briefs/ai_strategy_brief.json
    python run_demo.py path/to/your_brief.json
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

# ── Logging setup (before any deck_generator imports) ────────────────────────
try:
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.panel import Panel
    from rich.table import Table

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
        force=True,
    )
    _console = Console()
    _RICH = True
except ImportError:
    logging.basicConfig(level=logging.INFO)
    _console = None  # type: ignore[assignment]
    _RICH = False

from deck_generator.models import DeckBrief, DeckState
from deck_generator.utils.brief_validator import BriefValidationError, validate_and_fix_brief
from deck_generator.workflow.graph import build_deck_graph

logger = logging.getLogger("deck_generator.demo")

_DEFAULT_BRIEF = "sample_briefs/ai_strategy_brief.json"


def _print_header(brief: DeckBrief) -> None:
    if not _RICH:
        print(f"\n=== Deck Generator | {brief.title} ===\n")
        return
    _console.rule("[bold blue]Multi-Agent Deck Generator")
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(style="bold cyan", no_wrap=True)
    tbl.add_column()
    tbl.add_row("Title", brief.title)
    tbl.add_row("Client", brief.client)
    tbl.add_row("Industry", brief.industry)
    tbl.add_row("Audience", brief.audience)
    tbl.add_row("Slides", str(brief.slide_count_target))
    tbl.add_row("Tone", brief.tone)
    _console.print(tbl)
    _console.rule()


def _print_results(final: DeckState, elapsed: float) -> None:
    if not _RICH:
        print(f"\n--- Results ---")
        print(f"Status : {final.status}")
        print(f"PPTX   : {final.pptx_path or 'N/A'}")
        print(f"Time   : {elapsed:.1f}s")
        if final.qa_results:
            print(f"QA     : {final.qa_results.report_summary}")
        return

    _console.rule("[bold green]Pipeline Complete")
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(style="bold", no_wrap=True)
    tbl.add_column()
    tbl.add_row("[cyan]Status", final.status)
    tbl.add_row("[cyan]Total Time", f"{elapsed:.1f}s")
    if final.pptx_path:
        tbl.add_row("[green]Output PPTX", final.pptx_path)
    if final.qa_results:
        qa = final.qa_results
        color = "green" if qa.passed else "red"
        tbl.add_row(f"[{color}]QA Result", qa.report_summary)
    _console.print(tbl)

    if final.execution_logs:
        _console.print("\n[bold]Execution Log:[/bold]")
        for entry in final.execution_logs:
            _console.print(f"  [dim]•[/dim] {entry}")

    if final.selected_images:
        _console.print("\n[bold]Image Selection Summary:[/bold]")
        img_tbl = Table("Slide", "Provider", "Score", "Reason", show_header=True)
        for si in final.selected_images:
            img_tbl.add_row(
                str(si.slide_number),
                si.provider,
                f"{si.overall_score:.1f}",
                si.selection_reason[:60],
            )
        _console.print(img_tbl)


async def run_pipeline(brief_path: str) -> DeckState:
    """Load brief from JSON, run the full graph, return final DeckState."""
    data = validate_and_fix_brief(brief_path)
    brief = DeckBrief(**data)

    initial_state = DeckState(
        deck_brief=brief,
        audience=brief.audience,
        industry=brief.industry,
        brand=brief.brand,
        status="initialized",
        max_retries=2,
    )

    _print_header(brief)

    graph = build_deck_graph()
    start = time.perf_counter()
    result = await graph.ainvoke(initial_state)
    elapsed = time.perf_counter() - start
    final_state: DeckState = DeckState.model_validate(result) if isinstance(result, dict) else result

    _print_results(final_state, elapsed)
    return final_state


def main() -> None:
    brief_file = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_BRIEF

    if not Path(brief_file).exists():
        msg = f"Brief file not found: {brief_file}"
        if _RICH:
            _console.print(f"[red]ERROR:[/red] {msg}")
        else:
            print(f"ERROR: {msg}")
        sys.exit(1)

    try:
        final = asyncio.run(run_pipeline(brief_file))
    except BriefValidationError as exc:
        if _RICH:
            _console.print(f"[red]BRIEF VALIDATION ERROR:[/red] {exc}")
        else:
            print(f"BRIEF VALIDATION ERROR: {exc}")
        sys.exit(1)

    if final.pptx_path and Path(final.pptx_path).exists():
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
