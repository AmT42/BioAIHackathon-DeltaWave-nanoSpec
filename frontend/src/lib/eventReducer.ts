import { Turn, WorkStep, WsEvent } from "@/types/events";
import { KgMergedGraph } from "@/types/kgGraph";
import {
  createEmptyKgMergedGraph,
  extractKgSubgraphFromToolResult,
  mergeSubgraphIntoThreadGraph,
  recomputeMergedImportanceStats,
} from "@/lib/kgGraph";

type HydratedMessage = {
  id: string;
  role: string;
  content: string | null;
  content_blocks?: Array<Record<string, unknown>> | null;
  provider_format?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string;
};

export type ChatState = {
  threadId: string | null;
  activeRunId: string | null;
  turns: Turn[];
  kgGraph: KgMergedGraph;
  error: string | null;
};

export type ChatAction =
  | { type: "LOCAL_USER_MESSAGE"; message: string }
  | { type: "HYDRATE_FROM_MESSAGES"; messages: HydratedMessage[] }
  | { type: "WS_EVENT"; event: WsEvent }
  | { type: "RESET"; threadId: string | null };

export const initialChatState: ChatState = {
  threadId: null,
  activeRunId: null,
  turns: [],
  kgGraph: createEmptyKgMergedGraph(),
  error: null,
};

function makeTurn(partial?: Partial<Turn>): Turn {
  return {
    id: partial?.id ?? `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    runId: partial?.runId,
    userText: partial?.userText ?? "",
    userMessageId: partial?.userMessageId,
    assistantText: partial?.assistantText ?? "",
    assistantMessageId: partial?.assistantMessageId,
    status: partial?.status ?? "streaming",
    workSteps: partial?.workSteps ?? [],
  };
}

function safeJson(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value ?? "");
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function parseToolResultObject(value: unknown): Record<string, unknown> | null {
  const direct = asRecord(value);
  if (direct) return direct;
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return asRecord(parsed);
    } catch {
      return null;
    }
  }
  return null;
}

function mergeKgGraphWithResult(
  graph: KgMergedGraph,
  toolName: string | undefined,
  toolResult: Record<string, unknown> | null
): KgMergedGraph {
  const subgraph = extractKgSubgraphFromToolResult(toolName, toolResult);
  if (!subgraph) return graph;
  const merged = mergeSubgraphIntoThreadGraph(graph, subgraph);
  return recomputeMergedImportanceStats(merged);
}

function rebuildKgGraphFromTurns(turns: Turn[]): KgMergedGraph {
  let graph = createEmptyKgMergedGraph();
  for (const turn of turns) {
    for (const step of turn.workSteps) {
      if (step.kind !== "tool") continue;
      graph = mergeKgGraphWithResult(graph, step.toolName, step.toolResult ?? null);
    }
  }
  return graph;
}

function findTurnIndexByRun(turns: Turn[], runId?: string): number {
  if (!runId) return -1;
  return turns.findIndex((turn) => turn.runId === runId);
}

function findLatestPendingTurnIndex(turns: Turn[]): number {
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const turn = turns[i];
    if (!turn.runId && turn.status === "streaming" && !turn.assistantText) {
      return i;
    }
  }
  return -1;
}

function ensureTurn(turns: Turn[], runId?: string): { turns: Turn[]; index: number } {
  const byRun = findTurnIndexByRun(turns, runId);
  if (byRun !== -1) return { turns, index: byRun };

  const pendingIdx = findLatestPendingTurnIndex(turns);
  if (pendingIdx !== -1) {
    const updated = [...turns];
    updated[pendingIdx] = {
      ...updated[pendingIdx],
      runId: runId ?? updated[pendingIdx].runId,
      status: "streaming",
    };
    return { turns: updated, index: pendingIdx };
  }

  const created = makeTurn({ runId, status: "streaming" });
  return { turns: [...turns, created], index: turns.length };
}

function upsertWorkStep(workSteps: WorkStep[], step: WorkStep): WorkStep[] {
  const idx = workSteps.findIndex((item) => item.id === step.id);
  if (idx === -1) return [...workSteps, step];
  const next = [...workSteps];
  next[idx] = { ...next[idx], ...step };
  return next;
}

function appendWorkToken(workSteps: WorkStep[], id: string, token: string): WorkStep[] {
  const idx = workSteps.findIndex((item) => item.id === id);
  if (idx === -1) return workSteps;
  const next = [...workSteps];
  next[idx] = {
    ...next[idx],
    text: `${next[idx].text}${token}`,
  };
  return next;
}

function parseTraceToSteps(message: HydratedMessage): { assistantText: string; steps: WorkStep[] } {
  const metadata = message.metadata ?? {};
  const trace = (metadata.trace_v1 ?? {}) as Record<string, unknown>;

  const traceBlocks = Array.isArray(trace.content_blocks_normalized)
    ? (trace.content_blocks_normalized as Array<Record<string, unknown>>)
    : [];

  const fallbackBlocks =
    message.provider_format === "gemini_interleaved" && Array.isArray(message.content_blocks)
      ? message.content_blocks
      : [];

  const blocks = traceBlocks.length > 0 ? traceBlocks : fallbackBlocks;
  if (!Array.isArray(blocks) || blocks.length === 0) {
    return { assistantText: message.content ?? "", steps: [] };
  }

  let assistantText = "";
  let index = 0;
  let steps: WorkStep[] = [];

  const toolIndex = new Map<string, string>();

  for (const block of blocks) {
    const blockType = String(block.type ?? "");
    const segmentIndex = typeof block.segment_index === "number" ? block.segment_index : index;

    if (blockType === "text") {
      const text = String(block.text ?? "").trim();
      if (text) assistantText = text;
    }

    if (blockType === "thinking") {
      const text = String(block.thinking ?? block.text ?? "").trim();
      if (text) {
        const stepId = `hist-thinking-${message.id}-${segmentIndex}`;
        steps = upsertWorkStep(steps, {
          id: stepId,
          kind: "thinking",
          text,
          status: "done",
          segmentIndex,
          title: typeof block.summary === "string" ? block.summary : undefined,
        });
      }
    }

    if (blockType === "tool_use") {
      const toolUseId = String(block.id ?? block.tool_use_id ?? `tool-${message.id}-${segmentIndex}`);
      const toolName = String(block.name ?? "tool");
      toolIndex.set(toolUseId, `hist-tool-${toolUseId}`);
      steps = upsertWorkStep(steps, {
        id: `hist-tool-${toolUseId}`,
        kind: "tool",
        text: `Using ${toolName}`,
        status: "done",
        segmentIndex,
        toolUseId,
        toolName,
      });
    }

    if (blockType === "tool_result") {
      const toolUseId = String(block.tool_use_id ?? block.id ?? `tool-result-${message.id}-${segmentIndex}`);
      const parsedResult = parseToolResultObject(block.content ?? block.result);
      const resultText = safeJson(parsedResult ?? block.content ?? block.result ?? "").trim();
      const targetStepId = toolIndex.get(toolUseId) ?? `hist-tool-${toolUseId}`;
      const existing = steps.find((step) => step.id === targetStepId);
      const nextText = existing
        ? `${existing.text}\n\nResult:\n${resultText}`
        : `Result:\n${resultText}`;
      steps = upsertWorkStep(steps, {
        id: targetStepId,
        kind: "tool",
        text: nextText,
        status: "done",
        segmentIndex,
        toolUseId,
        toolName: existing?.toolName,
        toolResult: parsedResult ?? existing?.toolResult ?? null,
      });
    }

    index += 1;
  }

  if (!assistantText) assistantText = message.content ?? "";
  return { assistantText, steps };
}

function hydrateTurns(messages: HydratedMessage[]): Turn[] {
  const turns: Turn[] = [];

  const sorted = [...messages].sort((a, b) => {
    const aTime = a.created_at ? Date.parse(a.created_at) : 0;
    const bTime = b.created_at ? Date.parse(b.created_at) : 0;
    return aTime - bTime;
  });

  for (const msg of sorted) {
    if (msg.role === "user") {
      turns.push(
        makeTurn({
          id: `turn-${msg.id}`,
          userText: msg.content ?? "",
          userMessageId: msg.id,
          status: "done",
          assistantText: "",
          workSteps: [],
        })
      );
      continue;
    }

    if (msg.role !== "assistant") continue;

    const runIdRaw = msg.metadata?.run_id;
    const runId = typeof runIdRaw === "string" && runIdRaw.trim() ? runIdRaw : undefined;

    let targetIdx = findTurnIndexByRun(turns, runId);
    if (targetIdx === -1) {
      for (let i = turns.length - 1; i >= 0; i -= 1) {
        if (!turns[i].assistantText) {
          targetIdx = i;
          break;
        }
      }
    }

    if (targetIdx === -1) {
      turns.push(makeTurn({ runId, status: "done" }));
      targetIdx = turns.length - 1;
    }

    const { assistantText, steps } = parseTraceToSteps(msg);
    const existing = turns[targetIdx];

    turns[targetIdx] = {
      ...existing,
      runId: runId ?? existing.runId,
      assistantText: assistantText || existing.assistantText,
      assistantMessageId: msg.id,
      workSteps: steps.length > 0 ? steps : existing.workSteps,
      status: "done",
    };
  }

  return turns;
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  if (action.type === "RESET") {
    return { ...initialChatState, threadId: action.threadId };
  }

  if (action.type === "LOCAL_USER_MESSAGE") {
    return {
      ...state,
      turns: [...state.turns, makeTurn({ userText: action.message, status: "streaming" })],
      error: null,
    };
  }

  if (action.type === "HYDRATE_FROM_MESSAGES") {
    const turns = hydrateTurns(action.messages);
    return {
      ...state,
      turns,
      kgGraph: rebuildKgGraphFromTurns(turns),
      error: null,
    };
  }

  const event = action.event;
  const runId = typeof event.run_id === "string" && event.run_id.trim() ? event.run_id : undefined;
  const baseState: ChatState = {
    ...state,
    threadId: event.thread_id ?? state.threadId,
    activeRunId: runId ?? state.activeRunId,
  };

  switch (event.type) {
    case "main_agent_error": {
      return { ...baseState, error: event.error ?? "Unknown error" };
    }

    case "main_agent_start": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      nextTurns[ensured.index] = { ...nextTurns[ensured.index], status: "streaming" };
      return { ...baseState, turns: nextTurns, error: null };
    }

    case "main_agent_thinking_start": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const stepId = `thinking-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      target.workSteps = upsertWorkStep(target.workSteps, {
        id: stepId,
        kind: "thinking",
        text: "",
        status: "streaming",
        segmentIndex: event.segment_index,
      });
      target.status = "streaming";
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_thinking_token": {
      if (!event.token) return baseState;
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const stepId = `thinking-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      target.workSteps = appendWorkToken(target.workSteps, stepId, event.token);
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_thinking_end": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const stepId = `thinking-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      const existing = target.workSteps.find((step) => step.id === stepId);
      const finalSummary =
        typeof event.summary === "string" && event.summary.trim().length > 0
          ? event.summary
          : existing?.text ?? "";
      target.workSteps = upsertWorkStep(target.workSteps, {
        id: stepId,
        kind: "thinking",
        text: finalSummary,
        status: "done",
        segmentIndex: event.segment_index,
      });
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_thinking_title": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const stepId = `thinking-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      const existing = target.workSteps.find((step) => step.id === stepId);
      if (existing) {
        target.workSteps = upsertWorkStep(target.workSteps, {
          ...existing,
          title: event.summary,
        });
      }
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_segment_start": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      target.assistantMessageId = event.message_id ?? target.assistantMessageId;
      target.status = "streaming";
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_segment_token": {
      if (!event.token) return baseState;
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      target.assistantText = `${target.assistantText}${event.token}`;
      target.status = "streaming";
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_segment_end": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      if (typeof event.content === "string" && event.content.trim().length > 0) {
        target.assistantText = event.content;
      }
      target.assistantMessageId = event.message_id ?? target.assistantMessageId;
      target.status = "streaming";
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_tool_start": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const toolUseId = event.tool_use_id ?? `tool-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      const toolLabel = event.tool_name ?? toolUseId;
      target.workSteps = upsertWorkStep(target.workSteps, {
        id: `tool-${toolUseId}`,
        kind: "tool",
        text: `Using ${toolLabel}`,
        status: "streaming",
        segmentIndex: event.segment_index,
        toolUseId,
        toolName: event.tool_name,
      });
      target.status = "streaming";
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_tool_result": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const toolUseId = event.tool_use_id ?? `tool-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      const stepId = `tool-${toolUseId}`;
      const existing = target.workSteps.find((step) => step.id === stepId);
      const resultPayload = parseToolResultObject(event.result) ?? {};
      const nextStatus =
        resultPayload.status === "success" || resultPayload.status === "completed"
          ? "done"
          : "error";
      const resolvedToolName = existing?.toolName ?? event.tool_name;
      target.workSteps = upsertWorkStep(target.workSteps, {
        id: stepId,
        kind: "tool",
        text: `${existing?.text ?? `Using ${existing?.toolName ?? event.tool_name ?? toolUseId}`}\n\nResult:\n${safeJson(resultPayload)}`,
        status: nextStatus,
        segmentIndex: event.segment_index,
        toolUseId,
        toolName: resolvedToolName,
        toolResult: resultPayload,
      });
      nextTurns[ensured.index] = target;

      const nextKgGraph = mergeKgGraphWithResult(baseState.kgGraph, resolvedToolName, resultPayload);
      return { ...baseState, turns: nextTurns, kgGraph: nextKgGraph };
    }

    case "main_agent_complete": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      if (event.message?.id) {
        target.assistantMessageId = event.message.id;
      }
      if (typeof event.message?.content === "string" && event.message.content.trim().length > 0) {
        target.assistantText = event.message.content;
      }
      target.status = "done";
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns, error: null };
    }

    default:
      return baseState;
  }
}
