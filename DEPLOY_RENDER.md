# Deploy on Render (MVP Fast Path)

This repo includes a Render Blueprint at `render.yaml` to deploy both services:

- `hackathon-agent-core-backend` (FastAPI)
- `hackathon-agent-core-frontend` (Next.js)

## 1. Create services from the blueprint

1. Push this repo to GitHub.
2. In Render, choose **New +** -> **Blueprint**.
3. Select this repository.
4. Render will detect `render.yaml` and propose both services.
5. Create the stack.

## 2. Set required environment variables

Backend:

- `GEMINI_API_KEY`: required.
- `OPENALEX_MAILTO`: optional but recommended for OpenAlex API etiquette/rate handling.

Frontend:

- `NEXT_PUBLIC_BACKEND_URL` is auto-wired from backend `RENDER_EXTERNAL_URL` by `render.yaml`.

### Fast path: sync from local `backend/.env` via API

If you have a Render API key, run:

```bash
export RENDER_API_KEY=your_render_api_key
./scripts/render_sync_env_from_backend_env.sh
```

This script reads `GEMINI_API_KEY` and `OPENALEX_MAILTO` from `backend/.env`,
sets them on Render, updates frontend `NEXT_PUBLIC_BACKEND_URL`, and triggers deploys.

## 3. Verify

1. Open backend health endpoint: `https://<backend-url>/health`
2. Open frontend URL.
3. Send a chat message and confirm streaming works.

## Notes

- Current default DB is SQLite (`DATABASE_URL=sqlite+aiosqlite:///./chat.db`), which is fine for MVP/demo usage.
- SQLite file storage is not durable across all deploy/restart scenarios unless you add persistent storage.
- If you need durable production data, move to a managed DB and corresponding SQLAlchemy driver.
