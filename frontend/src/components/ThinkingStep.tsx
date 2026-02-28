"use client";

import { WorkStep } from "@/types/events";

type ThinkingStepProps = {
  step: WorkStep;
};

function StatusBadge({ status }: { status: WorkStep["status"] }) {
  const labels: Record<string, string> = {
    streaming: "thinking",
    done: "done",
    error: "error",
  };
  return (
    <span className={`status-badge status-badge--${status}`}>
      {labels[status] ?? status}
    </span>
  );
}

export function ThinkingStep({ step }: ThinkingStepProps) {
  const isStreaming = step.status === "streaming";
  const title = step.title || (isStreaming ? "Thinking..." : "Thought");

  return (
    <div className={`thinking-step ${isStreaming ? "thinking-step--streaming" : ""}`}>
      <div className="thinking-step__header">
        <span className="thinking-step__icon">&#x1F9E0;</span>
        <span className="thinking-step__title">{title}</span>
        <span className="thinking-step__status">
          <StatusBadge status={step.status} />
        </span>
      </div>
      {step.text && (
        <div className={`thinking-step__text ${isStreaming ? "streaming-cursor" : ""}`}>
          {step.text}
        </div>
      )}
    </div>
  );
}
