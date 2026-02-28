# Agent Instructions

## Worktree Bootstrap (mandatory for every new worktree)
Each time you create a new git worktree, make it runnable before starting feature work.

### Goal
Enable backend and frontend in the new worktree without reinstalling everything.

### Steps
From the **new worktree root**, run:

```bash
ln -s /Users/amt42/projects/hackathon-agent-core/backend/.env backend/.env

if [ -d /Users/amt42/projects/hackathon-agent-core/backend/.venv_local ]; then
  ln -s /Users/amt42/projects/hackathon-agent-core/backend/.venv_local backend/.venv
else
  ln -s /Users/amt42/projects/hackathon-agent-core/backend/.venv backend/.venv
fi

ln -s /Users/amt42/projects/hackathon-agent-core/frontend/node_modules frontend/node_modules
```

### Verify
Run these checks:

```bash
cd backend && .venv/bin/python -c "import app.main; print('backend_import_ok')"
cd ../frontend && ./node_modules/.bin/next --version
```

### Run services
Backend:

```bash
cd backend
./scripts/eve-up.sh
```

Frontend:

```bash
cd frontend
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000 npm run dev
```

If port 3000 is busy:

```bash
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000 npm run dev -- -p 3001
```

## Notes
- Keep these links as symlinks so worktrees stay aligned with the main local environment.
- If `main` dependencies change, re-run the verify steps in active worktrees.
- If `backend/.venv` scripts point to a missing path, use `backend/.venv_local` as the source for the `backend/.venv` symlink.
