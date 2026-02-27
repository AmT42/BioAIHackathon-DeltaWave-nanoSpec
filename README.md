# Hackathon Agent Core

Minimal Gemini-first chat stack with:
- interleaved thinking + tool calls
- streaming over WebSocket
- canonical DB event persistence
- mono-eve-style per-run logs under `backend/logs/<run_index>`

## Structure

- `backend/`: FastAPI + SQLAlchemy async
- `frontend/`: Next.js chat UI with separated worklog vs final answer

## Backend Quick Start

```bash
cd backend
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Run with log capture (recommended):

```bash
./scripts/eve-up.sh
```

Or run directly:

```bash
uvicorn app.main:app --reload --port 8000
```

## Frontend Quick Start

```bash
cd frontend
npm install
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000 npm run dev
```

If port `3000` is already in use:

```bash
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000 npm run dev -- -p 3001
```

Open `http://localhost:3000` (or `3001` if changed).

## API

### REST

- `POST /api/threads`
- `GET /api/threads/{thread_id}/messages`
- `GET /api/threads/{thread_id}/events`
- `POST /api/chat/send`

### WebSocket

- `WS /ws/chat?thread_id=<id>&provider=gemini`
- send:

```json
{ "type": "main_agent_chat", "content": "your question" }
```

## Stream Event Contract

- `main_agent_start`
- `main_agent_thinking_start` / `main_agent_thinking_token` / `main_agent_thinking_end` / `main_agent_thinking_title`
- `main_agent_segment_start` / `main_agent_segment_token` / `main_agent_segment_end`
- `main_agent_tool_start` / `main_agent_tool_result`
- `main_agent_complete`
- `main_agent_error`

## Log Layout

Each `./backend/scripts/eve-up.sh` run creates:

- `backend/logs/<run_index>/run_metadata.json`
- `backend/logs/<run_index>/backend.log`
- `backend/logs/<run_index>/threads/<thread_id>/db_thread.json`
- `backend/logs/<run_index>/threads/<thread_id>/user_msg_<N>/request_<M>/request_payload.json`
- `backend/logs/<run_index>/threads/<thread_id>/user_msg_<N>/request_<M>/answer.json`
- `backend/logs/<run_index>/threads/<thread_id>/user_msg_<N>/request_<M>/answer.txt`
- `backend/logs/<run_index>/threads/<thread_id>/user_msg_<N>/request_<M>/tools/normal_tool/*.json`

## Notes

- `MOCK_LLM=false` requires `GEMINI_API_KEY`.
- `GEMINI_REASONING_EFFORT` controls Gemini thinking effort (`minimal|low|medium|high|disable|none`), default `medium`.
- `GEMINI_INCLUDE_THOUGHTS=true` enables thought-summary streaming to the frontend.
- Optional `GEMINI_THINKING_BUDGET` overrides the per-turn thinking token budget.
- Gemini requests use Google GenAI SDK streaming with thought-signature-aware tool replay.
- If Gemini model naming fails (404 model not found), switch `GEMINI_MODEL` to a supported model for your key/project (for example `gemini/gemini-3-pro` or `gemini/gemini-3-flash`).
