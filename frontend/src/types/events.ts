export type WsEventType =
  | "main_agent_start"
  | "main_agent_segment_start"
  | "main_agent_segment_token"
  | "main_agent_segment_end"
  | "main_agent_thinking_start"
  | "main_agent_thinking_token"
  | "main_agent_thinking_end"
  | "main_agent_thinking_title"
  | "main_agent_tool_start"
  | "main_agent_tool_result"
  | "main_agent_complete"
  | "main_agent_error";

export type WsEvent = {
  type: WsEventType;
  thread_id?: string;
  run_id?: string;
  segment_index?: number;
  role?: string;
  token?: string;
  content?: string;
  summary?: string;
  message_id?: string;
  message?: {
    id?: string;
    thread_id?: string;
    role?: string;
    content?: string;
    metadata?: Record<string, unknown>;
    created_at?: string;
  };
  tool_calls?: Array<Record<string, unknown>>;
  tool_use_id?: string;
  tool_name?: string;
  arguments?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: string;
};

export type WorkStep = {
  id: string;
  kind: "thinking" | "tool";
  text: string;
  status: "streaming" | "done" | "error";
  segmentIndex?: number;
  toolUseId?: string;
  toolName?: string;
  toolResult?: Record<string, unknown> | null;
  title?: string;
};

export type Turn = {
  id: string;
  runId?: string;
  userText: string;
  userMessageId?: string;
  assistantText: string;
  assistantMessageId?: string;
  status: "streaming" | "done" | "error";
  workSteps: WorkStep[];
};
