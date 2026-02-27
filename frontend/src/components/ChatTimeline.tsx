import { Turn, WorkStep } from "@/types/events";

type Props = {
  turns: Turn[];
};

function statusLabel(status: WorkStep["status"]): string {
  if (status === "streaming") return "streaming";
  if (status === "error") return "error";
  return "done";
}

function renderStep(step: WorkStep) {
  const heading =
    step.kind === "thinking"
      ? step.title || "Thinking"
      : step.toolName
        ? `Tool: ${step.toolName}`
        : "Tool";

  return (
    <article key={step.id} className={`entry entry-${step.kind === "thinking" ? "thinking" : "tool"}`}>
      <header>
        <strong>{heading}</strong>
        <span className={`status status-${step.status}`}>{statusLabel(step.status)}</span>
      </header>
      <pre>{step.text || "..."}</pre>
    </article>
  );
}

export function ChatTimeline({ turns }: Props) {
  return (
    <div className="timeline">
      {turns.map((turn) => {
        const hasAnswer = turn.assistantText.trim().length > 0;
        const showAssistantPlaceholder = !hasAnswer && turn.status === "streaming";
        const workSteps = turn.workSteps;

        return (
          <div key={turn.id} className="turn-group">
            {turn.userText ? (
              <article className="entry entry-user">
                <header>
                  <strong>You</strong>
                  <span className="status status-done">done</span>
                </header>
                <pre>{turn.userText}</pre>
              </article>
            ) : null}

            {workSteps.length > 0 ? (
              <details className="worklog" open={turn.status === "streaming"}>
                <summary>
                  <span>Finished working</span>
                  <span>{workSteps.length} steps</span>
                </summary>
                <div className="worklog-list">{workSteps.map((step) => renderStep(step))}</div>
              </details>
            ) : null}

            {hasAnswer || showAssistantPlaceholder ? (
              <article className="entry entry-assistant">
                <header>
                  <strong>Assistant</strong>
                  <span className={`status status-${turn.status === "error" ? "error" : hasAnswer ? "done" : "streaming"}`}>
                    {turn.status === "error" ? "error" : hasAnswer ? "done" : "streaming"}
                  </span>
                </header>
                <pre>{hasAnswer ? turn.assistantText : "..."}</pre>
              </article>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
