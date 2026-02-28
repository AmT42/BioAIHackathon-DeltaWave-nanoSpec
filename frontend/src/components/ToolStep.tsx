"use client";

import { useState } from "react";
import { WorkStep } from "@/types/events";

type ToolStepProps = {
  step: WorkStep;
};

function StatusBadge({ status }: { status: WorkStep["status"] }) {
  const labels: Record<string, string> = {
    streaming: "running",
    done: "done",
    error: "error",
  };
  return (
    <span className={`status-badge status-badge--${status}`}>
      {labels[status] ?? status}
    </span>
  );
}

function parseToolText(text: string): { label: string; result: string | null } {
  const separator = "\n\nResult:\n";
  const idx = text.indexOf(separator);
  if (idx === -1) return { label: text, result: null };
  return {
    label: text.slice(0, idx),
    result: text.slice(idx + separator.length),
  };
}

function tryParseJson(raw: string): { parsed: Record<string, unknown> | null; display: string } {
  try {
    const obj = JSON.parse(raw);
    if (typeof obj === "object" && obj !== null) {
      return { parsed: obj as Record<string, unknown>, display: JSON.stringify(obj, null, 2) };
    }
    return { parsed: null, display: raw };
  } catch {
    return { parsed: null, display: raw };
  }
}

function extractSummary(parsed: Record<string, unknown>): string | null {
  // Try to extract a meaningful one-line summary from the result
  const status = parsed.status;
  const message = parsed.message;
  const data = parsed.data;

  const parts: string[] = [];
  if (typeof status === "string") parts.push(status);
  if (typeof message === "string") parts.push(message);
  if (!message && typeof data === "string") parts.push(data.slice(0, 120));
  if (!message && !data && typeof parsed.error === "string") parts.push(parsed.error);

  if (parts.length > 0) return parts.join(" - ");

  // Fallback: show first key-value pairs
  const keys = Object.keys(parsed).slice(0, 3);
  if (keys.length > 0) {
    return keys.map((k) => `${k}: ${String(parsed[k]).slice(0, 50)}`).join(", ");
  }
  return null;
}

export function ToolStep({ step }: ToolStepProps) {
  const [showResult, setShowResult] = useState(false);
  const isStreaming = step.status === "streaming";
  const { label, result } = parseToolText(step.text);
  const { parsed, display } = result ? tryParseJson(result) : { parsed: null, display: "" };
  const summary = parsed ? extractSummary(parsed) : null;
  const toolName = step.toolName || "tool";

  return (
    <div className={`tool-step ${isStreaming ? "tool-step--streaming" : ""}`}>
      <div className="tool-step__header">
        <span className="tool-step__icon">&#x1F527;</span>
        <span className="tool-step__name">{toolName}</span>
        <span className="tool-step__status-badge">
          <StatusBadge status={step.status} />
        </span>
      </div>

      {label && <div className="tool-step__label">{label}</div>}

      {isStreaming && !result && <div className="tool-step__shimmer" />}

      {result && (
        <>
          {summary && (
            <div className="tool-step__label" style={{ fontSize: "0.78rem" }}>
              {summary}
            </div>
          )}
          <button
            type="button"
            className="tool-step__result-toggle"
            onClick={() => setShowResult(!showResult)}
          >
            {showResult ? "\u25BC" : "\u25B6"} {showResult ? "Hide" : "Show"} full result
          </button>
          {showResult && <div className="tool-step__result">{display}</div>}
        </>
      )}
    </div>
  );
}
