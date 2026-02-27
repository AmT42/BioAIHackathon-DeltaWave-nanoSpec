"use client";

import { FormEvent, useEffect, useReducer, useRef, useState } from "react";

import { ChatTimeline } from "@/components/ChatTimeline";
import { chatReducer, initialChatState } from "@/lib/eventReducer";
import { connectChatSocket, WsClient } from "@/lib/wsClient";
import { WsEvent } from "@/types/events";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

type ApiMessage = {
  id: string;
  thread_id: string;
  role: string;
  content: string | null;
  content_blocks?: Array<Record<string, unknown>> | null;
  provider_format?: string | null;
  metadata?: Record<string, unknown>;
  created_at?: string;
};

export default function Page() {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);
  const wsRef = useRef<WsClient | null>(null);

  const canSend = connected && input.trim().length > 0;

  async function createThread(): Promise<string> {
    const response = await fetch(`${BACKEND_URL}/api/threads`, { method: "POST" });
    if (!response.ok) {
      throw new Error(`Failed to create thread: ${response.status}`);
    }
    const body = (await response.json()) as { thread_id: string };
    return body.thread_id;
  }

  async function loadMessages(nextThreadId: string): Promise<void> {
    const response = await fetch(`${BACKEND_URL}/api/threads/${nextThreadId}/messages`);
    if (!response.ok) return;
    const messages = (await response.json()) as ApiMessage[];
    dispatch({
      type: "HYDRATE_FROM_MESSAGES",
      messages: messages.map((message) => ({
        id: message.id,
        role: message.role,
        content: message.content,
        content_blocks: message.content_blocks ?? null,
        provider_format: message.provider_format ?? null,
        metadata: message.metadata ?? {},
        created_at: message.created_at,
      })),
    });
  }

  async function reconnect(nextThreadId?: string): Promise<void> {
    const useThreadId = nextThreadId ?? threadId ?? (await createThread());

    wsRef.current?.close();
    dispatch({ type: "RESET", threadId: useThreadId });
    setThreadId(useThreadId);

    wsRef.current = connectChatSocket({
      backendUrl: BACKEND_URL,
      threadId: useThreadId,
      provider: "gemini",
      onOpen: () => setConnected(true),
      onClose: () => setConnected(false),
      onError: (message) =>
        dispatch({ type: "WS_EVENT", event: { type: "main_agent_error", error: message } as WsEvent }),
      onEvent: (event) => dispatch({ type: "WS_EVENT", event }),
    });

    await loadMessages(useThreadId);
  }

  useEffect(() => {
    reconnect().catch((error) => {
      dispatch({ type: "WS_EVENT", event: { type: "main_agent_error", error: String(error) } as WsEvent });
    });
    return () => {
      wsRef.current?.close();
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!canSend || !wsRef.current) return;
    const msg = input.trim();
    dispatch({ type: "LOCAL_USER_MESSAGE", message: msg });
    wsRef.current.sendUserMessage(msg);
    setInput("");
  }

  return (
    <main className="shell">
      <section className="panel">
        <header className="panel-header">
          <div>
            <h1>Hackathon Agent Core</h1>
            <p>Gemini interleaved thinking + tool streaming (worklog separated from answer).</p>
          </div>
          <div className="controls">
            <span>Provider: Gemini</span>
            <button
              type="button"
              onClick={() =>
                reconnect().catch((error) =>
                  dispatch({ type: "WS_EVENT", event: { type: "main_agent_error", error: String(error) } as WsEvent })
                )
              }
            >
              Reconnect
            </button>
            <button
              type="button"
              onClick={async () => {
                const newThread = await createThread();
                await reconnect(newThread);
              }}
            >
              New Thread
            </button>
          </div>
        </header>

        <div className="meta">
          <span>Thread: {state.threadId ?? threadId ?? "-"}</span>
          <span>Status: {connected ? "connected" : "disconnected"}</span>
          {state.error ? <span className="error">Error: {state.error}</span> : null}
        </div>

        <ChatTimeline turns={state.turns} />

        <form className="composer" onSubmit={onSubmit}>
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Ask a life-science question or request a tool call..."
          />
          <button type="submit" disabled={!canSend}>
            Send
          </button>
        </form>
      </section>
    </main>
  );
}
