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
const AUTO_SCROLL_THRESHOLD_PX = 120;
const STREAM_FLUSH_INTERVAL_MS = 18;
const MAX_NON_STREAM_EVENTS_PER_TICK = 8;
const TOKEN_EVENT_TYPES = new Set<WsEvent["type"]>([
  "main_agent_segment_token",
  "main_agent_thinking_token",
  "main_agent_repl_code_token",
  "main_agent_repl_stdout",
  "main_agent_repl_stderr",
  "main_agent_bash_command_token",
]);

function isTokenLikeEvent(event: WsEvent): boolean {
  if (TOKEN_EVENT_TYPES.has(event.type)) return true;
  if (event.type !== "main_agent_tool_result") return false;
  const status = typeof event.result?.status === "string" ? event.result.status : "";
  return status === "streaming";
}

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
  const stickToBottomRef = useRef(true);
  const eventQueueRef = useRef<WsEvent[]>([]);
  const flushTimerRef = useRef<number | null>(null);

  const isStreaming = state.turns.some((t) => t.status === "streaming");

  const handleMessagesScroll = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    stickToBottomRef.current = distanceToBottom < AUTO_SCROLL_THRESHOLD_PX;
  }, []);

  // Auto-scroll only if the user is already near the bottom.
  useEffect(() => {
    if (stickToBottomRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: isStreaming ? "auto" : "smooth" });
    }
  }, [state.turns, isStreaming]);

  const flushEventQueue = useCallback(() => {
    const queue = eventQueueRef.current;
    if (queue.length === 0) {
      flushTimerRef.current = null;
      return;
    }

    let tokenDispatched = false;
    let nonTokenCount = 0;
    while (queue.length > 0) {
      const nextEvent = queue.shift();
      if (!nextEvent) break;
      dispatch({ type: "WS_EVENT", event: nextEvent });

      if (isTokenLikeEvent(nextEvent)) {
        tokenDispatched = true;
        break;
      }

      nonTokenCount += 1;
      if (nonTokenCount >= MAX_NON_STREAM_EVENTS_PER_TICK) break;
    }

    flushTimerRef.current = null;
    if (queue.length > 0) {
      const delayMs = tokenDispatched ? STREAM_FLUSH_INTERVAL_MS : 0;
      flushTimerRef.current = window.setTimeout(flushEventQueue, delayMs);
    }
  }, []);

  const enqueueWsEvent = useCallback(
    (event: WsEvent) => {
      eventQueueRef.current.push(event);
      if (flushTimerRef.current === null) {
        flushTimerRef.current = window.setTimeout(flushEventQueue, 0);
      }
    },
    [flushEventQueue]
  );

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

    eventQueueRef.current = [];
    if (flushTimerRef.current !== null) {
      window.clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
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
        enqueueWsEvent({ type: "main_agent_error", error: message } as WsEvent),
      onEvent: enqueueWsEvent,
    });

    await loadMessages(useThreadId);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enqueueWsEvent, threadId]);

  // Mount: load persisted thread or create new one
  useEffect(() => {
    setThreads(getThreadList());
    const savedId = getActiveThreadId();
    reconnect(savedId ?? undefined).catch((error) => {
      dispatch({ type: "WS_EVENT", event: { type: "main_agent_error", error: String(error) } as WsEvent });
    });
    return () => {
      if (flushTimerRef.current !== null) {
        window.clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      eventQueueRef.current = [];
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
            <div
              className="messages-container"
              ref={messagesContainerRef}
              onScroll={handleMessagesScroll}
            >
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
