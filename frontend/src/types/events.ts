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
  | "main_agent_repl_start"
  | "main_agent_repl_code_token"
  | "main_agent_repl_stdout"
  | "main_agent_repl_stderr"
  | "main_agent_repl_env"
  | "main_agent_repl_end"
  | "main_agent_bash_command_token"
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
  parent_tool_use_id?: string;
  tool_name?: string;
  ui_visible?: boolean;
  arguments?: Record<string, unknown>;
  code?: string;
  result?: Record<string, unknown>;
  env?: Record<string, unknown>;
  error?: string;
};

export type WorkStep = {
  id: string;
  kind: "thinking" | "tool" | "repl";
  text: string;
  status: "streaming" | "done" | "error";
  segmentIndex?: number;
  toolUseId?: string;
  toolName?: string;
  title?: string;
  code?: string;
  command?: string;
  stdout?: string;
  stderr?: string;
  result?: Record<string, unknown>;
  replEnv?: Record<string, unknown>;
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
