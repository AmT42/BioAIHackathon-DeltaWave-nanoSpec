"use client";

import { useEffect, useState } from "react";
import { Turn } from "@/types/events";
import { ThinkingStep } from "./ThinkingStep";
import { ToolStep } from "./ToolStep";
import { MarkdownRenderer } from "./MarkdownRenderer";

type ChatMessageProps = {
  turn: Turn;
};

export function ChatMessage({ turn }: ChatMessageProps) {
  const [expanded, setExpanded] = useState(turn.status === "streaming");
  const workSteps = turn.workSteps;
  const hasAnswer = turn.assistantText.trim().length > 0;
  const showAssistantPlaceholder = !hasAnswer && (turn.status === "streaming" || workSteps.length > 0);
  const thinkingCount = workSteps.filter((s) => s.kind === "thinking").length;
  const toolCount = workSteps.filter((s) => s.kind === "tool" || s.kind === "repl").length;

  // Auto-expand during streaming, auto-collapse when done
  useEffect(() => {
    if (turn.status === "streaming") {
      setExpanded(true);
    } else if (turn.status === "done" && workSteps.length > 0) {
      setExpanded(false);
    }
  }, [turn.status, workSteps.length]);

  function buildSummary(): string {
    const parts: string[] = [];
    if (thinkingCount > 0) parts.push(`${thinkingCount} thought${thinkingCount > 1 ? "s" : ""}`);
    if (toolCount > 0) parts.push(`${toolCount} tool${toolCount > 1 ? "s" : ""}`);
    return parts.join(", ") || "Work steps";
  }

  return (
    <div className="turn">
      {/* User message */}
      {turn.userText && (
        <div className="user-message">
          <div className="user-message__bubble">{turn.userText}</div>
        </div>
      )}

      {/* Work steps */}
      {workSteps.length > 0 && (
        <div className="work-steps">
          {/* Summary bar (collapsed state) */}
          {turn.status === "done" && (
            <div
              className="work-steps__summary"
              onClick={() => setExpanded(!expanded)}
            >
              <span className="work-steps__summary-icon">&#x26A1;</span>
              <span className="work-steps__summary-text">{buildSummary()}</span>
              <span
                className={`work-steps__summary-chevron ${expanded ? "work-steps__summary-chevron--open" : ""}`}
              >
                &#x25BC;
              </span>
            </div>
          )}

          {/* Expanded steps */}
          {expanded && (
            <div className="work-steps__list">
              {workSteps.map((step) =>
                step.kind === "thinking" ? (
                  <ThinkingStep key={step.id} step={step} />
                ) : (
                  <ToolStep key={step.id} step={step} />
                )
              )}
            </div>
          )}
        </div>
      )}

      {/* Assistant answer */}
      {(hasAnswer || showAssistantPlaceholder) && (
        <div className="assistant-message">
          <div className="assistant-message__header">
            <span className="assistant-message__label">Agent</span>
            <span className={`status-badge status-badge--${turn.status === "error" ? "error" : hasAnswer && turn.status === "done" ? "done" : "streaming"}`}>
              {turn.status === "error" ? "error" : hasAnswer && turn.status === "done" ? "complete" : "streaming"}
            </span>
          </div>
          <div className={`assistant-message__body ${turn.status === "streaming" ? "streaming-cursor" : ""}`}>
            {hasAnswer ? (
              <MarkdownRenderer
                content={turn.assistantText}
                streaming={turn.status === "streaming"}
              />
            ) : turn.status === "done" ? (
              <span style={{ color: "var(--text-tertiary)" }}>
                Run completed without a final assistant message.
              </span>
            ) : (
              <span style={{ color: "var(--text-tertiary)" }}>Generating response...</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
