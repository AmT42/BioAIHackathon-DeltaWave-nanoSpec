"use client";

import { KeyboardEvent, useRef, useState } from "react";

type ComposerProps = {
  onSend: (message: string) => void;
  disabled: boolean;
  streaming: boolean;
  connected: boolean;
};

export function Composer({ onSend, disabled, streaming, connected }: ComposerProps) {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const canSend = connected && input.trim().length > 0 && !streaming;

  function handleInput(value: string) {
    setInput(value);
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 180) + "px";
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (canSend) {
        onSend(input.trim());
        setInput("");
        if (textareaRef.current) {
          textareaRef.current.style.height = "auto";
        }
      }
    }
  }

  function handleSubmit() {
    if (canSend) {
      onSend(input.trim());
      setInput("");
      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
      }
    }
  }

  return (
    <div className="composer">
      {!connected && (
        <div className="composer__disconnected">
          <span>&#9888;</span> Disconnected from server
        </div>
      )}
      {streaming && (
        <div className="composer__streaming-indicator">
          <span className="composer__dots">
            <span />
            <span />
            <span />
          </span>
          Agent is working...
        </div>
      )}
      <div className="composer__form">
        <textarea
          ref={textareaRef}
          className="composer__textarea"
          value={input}
          onChange={(e) => handleInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about an aging intervention..."
          rows={1}
          disabled={!connected}
        />
        <button
          type="button"
          className="composer__send"
          onClick={handleSubmit}
          disabled={!canSend}
          title="Send message"
        >
          &#8593;
        </button>
      </div>
      <div className="composer__hint">
        Enter to send &middot; Shift+Enter for new line
      </div>
    </div>
  );
}
