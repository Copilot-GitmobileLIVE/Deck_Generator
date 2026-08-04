"""start_server.py — Launch the Deck Generator API server.

This is the recommended entry point when you want the frontend to submit briefs
and receive generated decks via HTTP instead of running run_demo.py directly.

What it starts:
    A uvicorn ASGI server hosting deck_generator.api:app on the specified host
    and port.  The API exposes:
        POST /api/generate      — submit a DeckBrief, get a job_id
        GET  /api/jobs/{id}     — poll for completion
        GET  /api/download/{f}  — download the finished .pptx
        GET  /docs              — interactive Swagger UI

After starting the server, open deck_frontend/index.html in your browser to
use the form-based frontend.

Usage:
    python start_server.py              # http://localhost:8000  (default)
    python start_server.py --port 9000  # custom port
    python start_server.py --reload     # hot-reload for development
"""
from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Deck Generator API server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev mode)")
    args = parser.parse_args()

    import uvicorn
    print(f"\n  Deck Generator API  →  http://localhost:{args.port}")
    print(f"  Frontend UI         →  open deck_frontend/index.html in your browser\n")
    uvicorn.run(
        "deck_generator.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
