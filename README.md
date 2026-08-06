# Deck Generator Agent

A multi-agent AI pipeline that takes a structured JSON brief and autonomously produces a polished, enterprise-quality PowerPoint `.pptx` file. Built with LangGraph, LangChain, OpenAI, and Google Gemini.

---

## Prerequisites

| Requirement | Minimum version |
|---|---|
| Python | 3.10+ |
| OpenAI API key | Required |
| Google Gemini API key | Optional (enables dual image generation) |

---

## 1. Get the Repository

```bash
git clone <repository-url>
cd Deck_Generator_Agent
```

---

## 2. Create a Virtual Environment

```bash
python -m venv venv
```

Activate it:

- **Windows**
  ```powershell
  venv\Scripts\activate
  ```
- **macOS / Linux**
  ```bash
  source venv/bin/activate
  ```

---

## 3. Install Requirements

```bash
pip install -r requirements.txt
```

---

## 4. Configure API Keys

Create a `.env` file in the project root (same folder as `start_server.py`):

```env
OPENAI_API_KEY=sk-...your-openai-key...
GEMINI_API_KEY=...your-gemini-key...   # optional — enables Google image generation
```

All other settings have sensible defaults. The full list of configurable values is documented in `deck_generator/config.py`.

> **Never commit your `.env` file.** Add it to `.gitignore`.

---

## 5. Start the API Server

```bash
python start_server.py
```

The server starts on **http://localhost:8000** by default.

Additional options:

```bash
python start_server.py --port 9000         # use a different port
python start_server.py --reload            # hot-reload for development
```

Verify the server is running by opening the interactive API docs:
**http://localhost:8000/docs**

---

## 6. Open the Frontend

After the server is running, open `deck_frontend/index.html` directly in your browser:

- **Windows**: double-click the file, or drag it into a browser window
- **Any OS**: `File → Open File` in your browser and navigate to `deck_frontend/index.html`

The frontend connects to `http://localhost:8000` automatically.

---

## 7. Generate a PowerPoint

### Option A — Frontend (recommended for new users)

1. Open `deck_frontend/index.html` in your browser.
2. Fill in the brief form:
   - **Title** — the presentation title
   - **Client** — client or company name
   - **Industry** — e.g. Telecom, Finance, Healthcare
   - **Audience** — e.g. C-Suite, Engineering Team
   - **Objective** — what the deck should achieve
   - **Key Messages** — bullet points (one per line)
   - **Tone** — e.g. executive, technical, conversational
   - **Slide count** — target number of slides
   - **Brand** — brand identifier (default: `mobilelive`)
   - **Additional context** — any extra guidance for the AI
3. Click **Generate Deck**.
4. Wait for the pipeline to complete (typically 2–5 minutes depending on slide count).
5. A download button appears when the `.pptx` is ready.

### Option B — CLI (no server required)

Run with the included sample brief:

```bash
python run_demo.py
```

Run with your own brief:

```bash
python run_demo.py path/to/your_brief.json
```

The generated `.pptx` is saved to the `output/` folder.

---

## 8. Writing a Brief JSON

A brief is a JSON file with the following fields. See `sample_briefs/ai_strategy_brief.json` for a complete example.

```json
{
  "title": "AI Strategy & Transformation Roadmap",
  "client": "Rogers Communications",
  "industry": "Telecom",
  "audience": "C-Suite and Technology Leadership",
  "objective": "Present a comprehensive AI transformation strategy ...",
  "key_messages": [
    "Message one",
    "Message two"
  ],
  "tone": "executive",
  "slide_count_target": 10,
  "brand": "mobilelive",
  "additional_context": "Optional extra guidance for the agents."
}
```

---

## Project Structure

```
Deck_Generator_Agent/
├── run_demo.py              ← CLI entry point
├── start_server.py          ← FastAPI server launcher
├── requirements.txt
├── mlarteka-pptx.skill      ← Brand rules archive (ZIP)
├── sample_briefs/           ← Example brief JSON files
├── output/                  ← Generated .pptx and images land here
├── deck_frontend/
│   └── index.html           ← Browser UI
└── deck_generator/
    ├── config.py            ← All settings (env vars)
    ├── api.py               ← FastAPI REST endpoints
    ├── agents/              ← ContentAgent, VisualAgent, ImageGenerationAgent, …
    ├── workflow/graph.py    ← LangGraph pipeline definition
    ├── pptx_builder/        ← python-pptx rendering layer
    └── utils/               ← Skill loader, validator, logging helpers
```

For a deep-dive into architecture, agent roles, and the brand skill system, read [AGENT_GUIDE.md](AGENT_GUIDE.md).

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/generate` | Submit a `DeckBrief` JSON, returns `job_id` |
| `GET` | `/api/jobs/{job_id}` | Poll for job status and progress |
| `GET` | `/api/download/{filename}` | Download the finished `.pptx` |
| `GET` | `/docs` | Interactive Swagger UI |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `OPENAI_API_KEY not set` error | Create `.env` with your key at the project root |
| Server not reachable from frontend | Ensure the server is running on port 8000 before opening the HTML file |
| Images fail to generate | Check your OpenAI quota; Gemini key is optional but improves image quality |
| `.pptx` not found after CLI run | Check the `output/` directory; ensure you ran from the project root |
| Brand skill not loading | Ensure `mlarteka-pptx.skill` is present at the project root |
