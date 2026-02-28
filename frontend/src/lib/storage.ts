import { ThreadMeta } from "@/types/threads";

const THREADS_KEY = "hac:threads";
const ACTIVE_KEY = "hac:activeThreadId";
const MAX_THREADS = 50;

function safeRead<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function safeWrite(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // localStorage full or unavailable
  }
}

export function getThreadList(): ThreadMeta[] {
  const threads = safeRead<ThreadMeta[]>(THREADS_KEY, []);
  return threads.sort(
    (a, b) => Date.parse(b.lastActiveAt) - Date.parse(a.lastActiveAt)
  );
}

export function saveThread(meta: ThreadMeta): void {
  const threads = safeRead<ThreadMeta[]>(THREADS_KEY, []);
  const idx = threads.findIndex((t) => t.id === meta.id);
  if (idx >= 0) {
    threads[idx] = { ...threads[idx], ...meta };
  } else {
    threads.push(meta);
  }
  // Keep only the most recent threads
  const sorted = threads
    .sort((a, b) => Date.parse(b.lastActiveAt) - Date.parse(a.lastActiveAt))
    .slice(0, MAX_THREADS);
  safeWrite(THREADS_KEY, sorted);
}

export function removeThread(threadId: string): void {
  const threads = safeRead<ThreadMeta[]>(THREADS_KEY, []);
  safeWrite(
    THREADS_KEY,
    threads.filter((t) => t.id !== threadId)
  );
}

export function updateThreadTitle(id: string, title: string): void {
  const threads = safeRead<ThreadMeta[]>(THREADS_KEY, []);
  const idx = threads.findIndex((t) => t.id === id);
  if (idx >= 0) {
    threads[idx] = { ...threads[idx], title };
    safeWrite(THREADS_KEY, threads);
  }
}

export function getActiveThreadId(): string | null {
  try {
    return localStorage.getItem(ACTIVE_KEY);
  } catch {
    return null;
  }
}

export function setActiveThreadId(threadId: string): void {
  try {
    localStorage.setItem(ACTIVE_KEY, threadId);
  } catch {
    // ignore
  }
}

export function clearActiveThreadId(): void {
  try {
    localStorage.removeItem(ACTIVE_KEY);
  } catch {
    // ignore
  }
}

export function formatRelativeTime(isoString: string): string {
  const now = Date.now();
  const then = Date.parse(isoString);
  if (isNaN(then)) return "";
  const diffMs = now - then;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHr / 24);

  if (diffSec < 60) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHr < 24) return `${diffHr}h ago`;
  if (diffDay === 1) return "yesterday";
  if (diffDay < 7) return `${diffDay}d ago`;

  const date = new Date(then);
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
