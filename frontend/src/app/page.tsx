"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import dynamic from "next/dynamic";

import { Header } from "@/components/Header";
import { Sidebar } from "@/components/Sidebar";
import { ChatMessage } from "@/components/ChatMessage";
import { Composer } from "@/components/Composer";
import { chatReducer, initialChatState } from "@/lib/eventReducer";
import { connectChatSocket, WsClient } from "@/lib/wsClient";
import { WsEvent } from "@/types/events";
import { ThreadMeta } from "@/types/threads";
import {
  getThreadList,
  saveThread,
  removeThread,
  updateThreadTitle,
  getActiveThreadId,
  setActiveThreadId,
} from "@/lib/storage";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";
const KgGraphPanel = dynamic(
  () => import("@/components/KgGraphPanel").then((mod) => mod.KgGraphPanel),
  { ssr: false }
);

const SUGGESTIONS = [
  "Rapamycin",
  "Metformin (TAME trial)",
  "NAD+ precursors (NMN/NR)",
  "Senolytics (dasatinib + quercetin)",
  "Hyperbaric oxygen therapy",
  "Epigenetic reprogramming",
];

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
  const [connected, setConnected] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [threads, setThreads] = useState<ThreadMeta[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const wsRef = useRef<WsClient | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);

  const isStreaming = state.turns.some((t) => t.status === "streaming");

  // Auto-scroll during streaming
  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    // Only auto-scroll if user is near the bottom
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 150;
    if (nearBottom || isStreaming) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [state.turns, isStreaming]);

  async function createThread(): Promise<string> {
    const response = await fetch(`${BACKEND_URL}/api/threads`, { method: "POST" });
    if (!response.ok) throw new Error(`Failed to create thread: ${response.status}`);
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

  const reconnect = useCallback(async (nextThreadId?: string): Promise<void> => {
    const useThreadId = nextThreadId ?? threadId ?? (await createThread());

    wsRef.current?.close();
    dispatch({ type: "RESET", threadId: useThreadId });
    setThreadId(useThreadId);
    setActiveThreadId(useThreadId);

    // Save to thread list
    const existing = getThreadList().find((t) => t.id === useThreadId);
    if (!existing) {
      saveThread({
        id: useThreadId,
        title: "New conversation",
        createdAt: new Date().toISOString(),
        lastActiveAt: new Date().toISOString(),
      });
      setThreads(getThreadList());
    }

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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

  // Mount: load persisted thread or create new one
  useEffect(() => {
    setThreads(getThreadList());
    const savedId = getActiveThreadId();
    reconnect(savedId ?? undefined).catch((error) => {
      dispatch({ type: "WS_EVENT", event: { type: "main_agent_error", error: String(error) } as WsEvent });
    });
    return () => {
      wsRef.current?.close();
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleSend(message: string) {
    if (!connected || !wsRef.current) return;
    dispatch({ type: "LOCAL_USER_MESSAGE", message });
    wsRef.current.sendUserMessage(message);

    // Update thread title on first user message
    if (threadId) {
      const thread = getThreadList().find((t) => t.id === threadId);
      if (thread?.title === "New conversation") {
        updateThreadTitle(threadId, message.slice(0, 60));
      }
      saveThread({
        id: threadId,
        title: thread?.title === "New conversation" ? message.slice(0, 60) : thread?.title ?? message.slice(0, 60),
        createdAt: thread?.createdAt ?? new Date().toISOString(),
        lastActiveAt: new Date().toISOString(),
      });
      setThreads(getThreadList());
    }
  }

  async function handleNewThread() {
    try {
      const newId = await createThread();
      await reconnect(newId);
    } catch (error) {
      dispatch({ type: "WS_EVENT", event: { type: "main_agent_error", error: String(error) } as WsEvent });
    }
  }

  async function handleSelectThread(selectedId: string) {
    if (selectedId === threadId) return;
    try {
      await reconnect(selectedId);
    } catch (error) {
      dispatch({ type: "WS_EVENT", event: { type: "main_agent_error", error: String(error) } as WsEvent });
    }
  }

  function handleDeleteThread(deleteId: string) {
    removeThread(deleteId);
    setThreads(getThreadList());
    if (deleteId === threadId) {
      handleNewThread();
    }
  }

  function handleSuggestion(suggestion: string) {
    handleSend(`Analyze the evidence for: ${suggestion}`);
  }

  const hasTurns = state.turns.length > 0;

  return (
    <div className="app-layout">
      {/* Mobile backdrop */}
      {sidebarOpen && (
        <div
          className="sidebar-backdrop"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <Sidebar
        threads={threads}
        activeThreadId={threadId}
        onSelectThread={handleSelectThread}
        onNewThread={handleNewThread}
        onDeleteThread={handleDeleteThread}
        open={sidebarOpen}
      />

      <main className="main-panel">
        <Header
          threadId={threadId}
          connected={connected}
          streaming={isStreaming}
          onNewThread={handleNewThread}
          onToggleSidebar={() => setSidebarOpen(!sidebarOpen)}
          sidebarOpen={sidebarOpen}
        />

        <div className="content-layout">
          <section className="chat-column">
            <div className="messages-container" ref={messagesContainerRef}>
              {!hasTurns ? (
                <div className="welcome">
                  <div className="welcome__icon">&#x1F9EC;</div>
                  <h2 className="welcome__title">Longevity Evidence Agent</h2>
                  <p className="welcome__subtitle">
                    AI-powered evidence grading for aging interventions. Ask about any compound
                    or therapy to get a structured evidence report with confidence scores.
                  </p>
                  <div className="welcome__suggestions">
                    {SUGGESTIONS.map((s) => (
                      <button
                        key={s}
                        type="button"
                        className="welcome__suggestion"
                        onClick={() => handleSuggestion(s)}
                        disabled={!connected}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                state.turns.map((turn) => <ChatMessage key={turn.id} turn={turn} />)
              )}
              <div ref={messagesEndRef} />
            </div>

            <Composer
              onSend={handleSend}
              disabled={!connected}
              streaming={isStreaming}
              connected={connected}
            />
          </section>

          <KgGraphPanel graph={state.kgGraph} />
        </div>
      </main>
    </div>
  );
}
