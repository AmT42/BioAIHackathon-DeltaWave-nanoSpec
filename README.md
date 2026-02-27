# Hackathon Agent Core

Minimal, fresh repo for a chatbot that supports:
- Claude interleaved thinking + tool use
- Gemini thinking + tool use
- WebSocket token streaming
- Canonical event persistence for stable replay ordering

This project intentionally excludes workflow engines, memory systems, and heavy app-specific logic.

## Structure

- `backend/` FastAPI + SQLAlchemy async
- `frontend/` Next.js chat UI with deterministic event reducer

## Backend Quick Start

```bash
cd backend
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn app.main:app --reload --port 8000
```

## Frontend Quick Start

```bash
cd frontend
npm install
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000 npm run dev
```

Open `http://localhost:3000`.

## API

### REST
- `POST /api/threads`
- `GET /api/threads/{thread_id}/events`
- `POST /api/chat/send`

### WebSocket
- `WS /ws/chat?thread_id=<id>&provider=claude|gemini`
- send messages with payload:

```json
{ "type": "user_message", "content": "your question" }
```

## Stream Event Contract

- `chat_start`
- `segment_start` / `segment_token` / `segment_end`
- `thinking_start` / `thinking_token` / `thinking_end`
- `tool_start` / `tool_result`
- `chat_complete`
- `chat_error`

Each event carries `thread_id`, `run_id`, and `segment_index` when applicable.

## Notes

- If API keys are missing, set `MOCK_LLM=true` to demo full UI + tool flow deterministically.
- SQLite is used by default for minimal local setup.
