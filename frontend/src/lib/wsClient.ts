import { WsEvent } from "@/types/events";

export type WsClient = {
  socket: WebSocket;
  sendUserMessage: (content: string) => void;
  close: () => void;
};

export function connectChatSocket(params: {
  backendUrl: string;
  threadId: string;
  provider: "gemini";
  onEvent: (event: WsEvent) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (message: string) => void;
}): WsClient {
  const wsBase = params.backendUrl.replace(/^http/, "ws");
  const url = `${wsBase}/ws/chat?thread_id=${encodeURIComponent(params.threadId)}&provider=${params.provider}`;
  const socket = new WebSocket(url);

  socket.addEventListener("open", () => params.onOpen?.());
  socket.addEventListener("close", () => params.onClose?.());
  socket.addEventListener("error", () => params.onError?.("WebSocket error"));
  socket.addEventListener("message", (raw) => {
    try {
      const parsed = JSON.parse(String(raw.data)) as WsEvent;
      params.onEvent(parsed);
    } catch {
      params.onError?.("Invalid websocket payload");
    }
  });

  return {
    socket,
    sendUserMessage(content: string) {
      socket.send(JSON.stringify({ type: "main_agent_chat", content }));
    },
    close() {
      socket.close();
    },
  };
}
