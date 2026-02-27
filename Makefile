.PHONY: backend frontend test

backend:
	cd backend && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && NEXT_PUBLIC_BACKEND_URL=http://localhost:8000 npm run dev

test:
	cd backend && pytest -q
