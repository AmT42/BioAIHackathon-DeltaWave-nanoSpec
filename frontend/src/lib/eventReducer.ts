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

const DISPLAYABLE_TOP_LEVEL_TOOLS = new Set(["repl_exec", "bash_exec"]);

function normalizeToolName(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value.trim();
  return normalized.length > 0 ? normalized : undefined;
}

function isDisplayableTopLevelToolName(value: unknown): boolean {
  const toolName = normalizeToolName(value);
  return toolName ? DISPLAYABLE_TOP_LEVEL_TOOLS.has(toolName) : false;
}

function isUiVisible(value: unknown): boolean {
  return value !== false;
}

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

function appendChunk(existing: string | undefined, incoming: string | undefined): string {
  const current = existing ?? "";
  const chunk = incoming ?? "";
  if (!chunk) return current;
  if (!current) return chunk;
  if (chunk.startsWith(current)) return chunk;
  if (current.endsWith(chunk)) return current;
  return `${current}${chunk}`;
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
      const toolName = normalizeToolName(block.name) ?? "tool";
      const blockVisible = isUiVisible(block.ui_visible);
      const parentToolUseId =
        typeof block.parent_tool_use_id === "string" && block.parent_tool_use_id.trim().length > 0
          ? block.parent_tool_use_id
          : undefined;
      if (!blockVisible) {
        index += 1;
        continue;
      }
      if (parentToolUseId) {
        const parentStepId = toolIndex.get(parentToolUseId) ?? `hist-tool-${parentToolUseId}`;
        const parent = steps.find((step) => step.id === parentStepId);
        if (parent?.toolName === "repl_exec") {
          index += 1;
          continue;
        }
      } else if (!isDisplayableTopLevelToolName(toolName)) {
        index += 1;
        continue;
      }
      const input = asRecord(block.input);
      const code = toolName === "repl_exec" && typeof input?.code === "string" ? input.code : undefined;
      const command = toolName === "bash_exec" && typeof input?.command === "string" ? input.command : undefined;
      const stepKind: WorkStep["kind"] = toolName === "repl_exec" ? "repl" : "tool";
      const label =
        toolName === "repl_exec"
          ? "Running Python REPL"
          : toolName === "bash_exec"
            ? "Running bash command"
            : `Using ${toolName}`;
      toolIndex.set(toolUseId, `hist-tool-${toolUseId}`);
      steps = upsertWorkStep(steps, {
        id: `hist-tool-${toolUseId}`,
        kind: stepKind,
        text: label,
        status: "done",
        segmentIndex,
        toolUseId,
        toolName,
        code,
        command,
      });
    }

    if (blockType === "tool_result") {
      const toolUseId = String(block.tool_use_id ?? block.id ?? `tool-result-${message.id}-${segmentIndex}`);
      const parsedResult = parseToolResultObject(block.content ?? block.result);
      const blockVisible = isUiVisible(block.ui_visible);
      const blockToolName = normalizeToolName(parsedResult ?? block.name);
      const parentToolUseId =
        typeof block.parent_tool_use_id === "string" && block.parent_tool_use_id.trim().length > 0
          ? block.parent_tool_use_id
          : undefined;
      if (!blockVisible) {
        index += 1;
        continue;
      }
      if (parentToolUseId) {
        const parentStepId = toolIndex.get(parentToolUseId) ?? `hist-tool-${parentToolUseId}`;
        const parent = steps.find((step) => step.id === parentStepId);
        if (parent?.toolName === "repl_exec") {
          index += 1;
          continue;
        }
      }
      const resultPayload = asRecord(block.content) ?? asRecord(block.result);
      const targetStepId = toolIndex.get(toolUseId) ?? `hist-tool-${toolUseId}`;
      const existing = steps.find((step) => step.id === targetStepId);
      const toolName = existing?.toolName ?? blockToolName;
      if (!toolName || !isDisplayableTopLevelToolName(toolName)) {
        index += 1;
        continue;
      }

      if (toolName === "repl_exec" || toolName === "bash_exec") {
        const output = asRecord(resultPayload?.output);
        const stdout = appendChunk(existing?.stdout, typeof output?.stdout === "string" ? output.stdout : undefined);
        const stderr = appendChunk(existing?.stderr, typeof output?.stderr === "string" ? output.stderr : undefined);
        const replEnv =
          toolName === "repl_exec"
            ? asRecord(output?.env) ?? existing?.replEnv
            : existing?.replEnv;
        const command =
          toolName === "bash_exec"
            ? typeof output?.command === "string"
              ? output.command
              : existing?.command
            : existing?.command;
        const code = toolName === "repl_exec" ? existing?.code : undefined;
        const statusRaw = String(resultPayload?.status ?? "");
        const nextStatus: WorkStep["status"] =
          statusRaw === "error" ? "error" : "done";

        steps = upsertWorkStep(steps, {
          id: targetStepId,
          kind: toolName === "repl_exec" ? "repl" : "tool",
          text: existing?.text ?? (toolName === "repl_exec" ? "Running Python REPL" : "Running bash command"),
          status: nextStatus,
          segmentIndex,
          toolUseId,
          toolName,
          code,
          command,
          stdout,
          stderr,
          result: resultPayload ?? undefined,
          replEnv,
        });
        continue;
      }

      const resultText = safeJson(resultPayload ?? block.content ?? block.result ?? "").trim();
      const nextText = existing ? `${existing.text}\n\nResult:\n${resultText}` : `Result:\n${resultText}`;
      steps = upsertWorkStep(steps, {
        id: targetStepId,
        kind: "tool",
        text: nextText,
        status: "done",
        segmentIndex,
        toolUseId,
        toolName: existing?.toolName,
      });
    }

    if (blockType === "repl_env") {
      const toolUseId = String(block.tool_use_id ?? `repl-env-${message.id}-${segmentIndex}`);
      const targetStepId = toolIndex.get(toolUseId) ?? `hist-tool-${toolUseId}`;
      const existing = steps.find((step) => step.id === targetStepId);
      const envPayload = asRecord(block.env);
      if (!envPayload) {
        index += 1;
        continue;
      }
      steps = upsertWorkStep(steps, {
        id: targetStepId,
        kind: "repl",
        text: existing?.text ?? "Running Python REPL",
        status: existing?.status ?? "done",
        segmentIndex,
        toolUseId,
        toolName: "repl_exec",
        code: existing?.code,
        stdout: existing?.stdout,
        stderr: existing?.stderr,
        result: existing?.result,
        replEnv: envPayload,
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
      const token = typeof event.token === "string" ? event.token : typeof event.content === "string" ? event.content : "";
      if (!token) return baseState;
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const stepId = `thinking-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      target.workSteps = appendWorkToken(target.workSteps, stepId, token);
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
      const token = typeof event.token === "string" ? event.token : typeof event.content === "string" ? event.content : "";
      if (!token) return baseState;
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      target.assistantText = `${target.assistantText}${token}`;
      target.status = "streaming";
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_segment_end": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      if (typeof event.content === "string" && event.content.trim().length > 0) {
        const current = target.assistantText;
        const incoming = event.content;
        if (!current) {
          target.assistantText = incoming;
        } else if (incoming.startsWith(current)) {
          target.assistantText = incoming;
        } else if (!current.endsWith(incoming)) {
          target.assistantText = `${current}${incoming}`;
        }
      }
      target.assistantMessageId = event.message_id ?? target.assistantMessageId;
      target.status = "streaming";
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_tool_start": {
      if (event.parent_tool_use_id) return baseState;
      if (!isUiVisible(event.ui_visible)) return baseState;
      if (!isDisplayableTopLevelToolName(event.tool_name)) return baseState;
      if (event.tool_name === "repl_exec") return baseState;
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const toolUseId = event.tool_use_id ?? `tool-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      const toolLabel = event.tool_name ?? toolUseId;
      const command = event.tool_name === "bash_exec" ? "" : undefined;
      const label = event.tool_name === "bash_exec" ? "Running bash command" : `Using ${toolLabel}`;
      target.workSteps = upsertWorkStep(target.workSteps, {
        id: `tool-${toolUseId}`,
        kind: "tool",
        text: label,
        status: "streaming",
        segmentIndex: event.segment_index,
        toolUseId,
        toolName: event.tool_name,
        command,
      });
      target.status = "streaming";
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_bash_command_token": {
      if (!isUiVisible(event.ui_visible)) return baseState;
      const token = typeof event.token === "string" ? event.token : typeof event.content === "string" ? event.content : "";
      if (!token) return baseState;
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const toolUseId = event.tool_use_id ?? `tool-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      const stepId = `tool-${toolUseId}`;
      const existing = target.workSteps.find((step) => step.id === stepId);
      target.workSteps = upsertWorkStep(target.workSteps, {
        id: stepId,
        kind: "tool",
        text: existing?.text ?? "Running bash command",
        status: existing?.status ?? "streaming",
        segmentIndex: event.segment_index,
        toolUseId,
        toolName: "bash_exec",
        command: appendChunk(existing?.command, token),
        stdout: existing?.stdout ?? "",
        stderr: existing?.stderr ?? "",
        result: existing?.result,
      });
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_repl_start": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const toolUseId = event.tool_use_id ?? `repl-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      const code = typeof event.code === "string" ? event.code : "";
      target.workSteps = upsertWorkStep(target.workSteps, {
        id: `repl-${toolUseId}`,
        kind: "repl",
        text: "Running Python REPL",
        status: "streaming",
        segmentIndex: event.segment_index,
        toolUseId,
        toolName: "repl_exec",
        code,
        stdout: "",
        stderr: "",
        replEnv: undefined,
      });
      target.status = "streaming";
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_repl_code_token": {
      const token = typeof event.token === "string" ? event.token : typeof event.content === "string" ? event.content : "";
      if (!token) return baseState;
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const toolUseId = event.tool_use_id ?? `repl-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      const stepId = `repl-${toolUseId}`;
      const existing = target.workSteps.find((step) => step.id === stepId);
      target.workSteps = upsertWorkStep(target.workSteps, {
        id: stepId,
        kind: "repl",
        text: existing?.text ?? "Running Python REPL",
        status: existing?.status ?? "streaming",
        segmentIndex: event.segment_index,
        toolUseId,
        toolName: "repl_exec",
        code: appendChunk(existing?.code, token),
        stdout: existing?.stdout ?? "",
        stderr: existing?.stderr ?? "",
        replEnv: existing?.replEnv,
      });
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_repl_stdout": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const toolUseId = event.tool_use_id ?? `repl-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      const stepId = `repl-${toolUseId}`;
      const existing = target.workSteps.find((step) => step.id === stepId);
      const content = typeof event.content === "string" ? event.content : typeof event.token === "string" ? event.token : "";
      target.workSteps = upsertWorkStep(target.workSteps, {
        id: stepId,
        kind: "repl",
        text: existing?.text ?? "Running Python REPL",
        status: "streaming",
        segmentIndex: event.segment_index,
        toolUseId,
        toolName: "repl_exec",
        code: existing?.code,
        stdout: appendChunk(existing?.stdout, content),
        stderr: existing?.stderr ?? "",
        replEnv: existing?.replEnv,
      });
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_repl_stderr": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const toolUseId = event.tool_use_id ?? `repl-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      const stepId = `repl-${toolUseId}`;
      const existing = target.workSteps.find((step) => step.id === stepId);
      const content = typeof event.content === "string" ? event.content : typeof event.token === "string" ? event.token : "";
      target.workSteps = upsertWorkStep(target.workSteps, {
        id: stepId,
        kind: "repl",
        text: existing?.text ?? "Running Python REPL",
        status: "streaming",
        segmentIndex: event.segment_index,
        toolUseId,
        toolName: "repl_exec",
        code: existing?.code,
        stdout: existing?.stdout ?? "",
        stderr: appendChunk(existing?.stderr, content),
        replEnv: existing?.replEnv,
      });
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_repl_env": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const toolUseId = event.tool_use_id ?? `repl-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      const stepId = `repl-${toolUseId}`;
      const existing = target.workSteps.find((step) => step.id === stepId);
      const envPayload = asRecord(event.env);
      if (!envPayload) return baseState;
      target.workSteps = upsertWorkStep(target.workSteps, {
        id: stepId,
        kind: "repl",
        text: existing?.text ?? "Running Python REPL",
        status: existing?.status ?? "streaming",
        segmentIndex: event.segment_index,
        toolUseId,
        toolName: "repl_exec",
        code: existing?.code,
        stdout: existing?.stdout ?? "",
        stderr: existing?.stderr ?? "",
        result: existing?.result,
        replEnv: envPayload,
      });
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_repl_end": {
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const toolUseId = event.tool_use_id ?? `repl-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      const stepId = `repl-${toolUseId}`;
      const existing = target.workSteps.find((step) => step.id === stepId);
      const resultPayload = event.result ?? {};
      const nextStatus = resultPayload.status === "error" ? "error" : "done";
      const output = asRecord(resultPayload.output);
      const replEnv = asRecord(output?.env) ?? existing?.replEnv;
      target.workSteps = upsertWorkStep(target.workSteps, {
        id: stepId,
        kind: "repl",
        text: existing?.text ?? "Running Python REPL",
        status: nextStatus,
        segmentIndex: event.segment_index,
        toolUseId,
        toolName: "repl_exec",
        code: existing?.code,
        stdout: appendChunk(existing?.stdout, typeof output?.stdout === "string" ? output.stdout : undefined),
        stderr: appendChunk(existing?.stderr, typeof output?.stderr === "string" ? output.stderr : undefined),
        result: resultPayload,
        replEnv,
      });
      nextTurns[ensured.index] = target;
      return { ...baseState, turns: nextTurns };
    }

    case "main_agent_tool_result": {
      if (event.parent_tool_use_id) return baseState;
      if (!isUiVisible(event.ui_visible)) return baseState;
      if (event.tool_name === "repl_exec") return baseState;
      const ensured = ensureTurn(baseState.turns, runId);
      const nextTurns = [...ensured.turns];
      const target = nextTurns[ensured.index];
      const toolUseId = event.tool_use_id ?? `tool-${runId ?? "norun"}-${event.segment_index ?? 0}`;
      const stepId = `tool-${toolUseId}`;
      const existing = target.workSteps.find((step) => step.id === stepId);
      const resultPayload = parseToolResultObject(event.result) ?? {};
      const toolName = existing?.toolName ?? event.tool_name;
      if (!isDisplayableTopLevelToolName(toolName)) return baseState;
      if (toolName === "bash_exec") {
        const output = asRecord(resultPayload.output);
        const statusRaw = String(resultPayload.status ?? "");
        const nextStatus: WorkStep["status"] =
          statusRaw === "success" || statusRaw === "completed"
            ? "done"
            : statusRaw === "streaming"
              ? "streaming"
              : "error";
        target.workSteps = upsertWorkStep(target.workSteps, {
          id: stepId,
          kind: "tool",
          text: existing?.text ?? "Running bash command",
          status: nextStatus,
          segmentIndex: event.segment_index,
          toolUseId,
          toolName: "bash_exec",
          command: typeof output?.command === "string" ? output.command : existing?.command,
          stdout: appendChunk(existing?.stdout, typeof output?.stdout === "string" ? output.stdout : undefined),
          stderr: appendChunk(existing?.stderr, typeof output?.stderr === "string" ? output.stderr : undefined),
          result: resultPayload,
        });
        nextTurns[ensured.index] = target;
        return { ...baseState, turns: nextTurns };
      }
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
        toolName,
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
