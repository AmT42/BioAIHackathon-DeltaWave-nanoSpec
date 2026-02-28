"use client";

import { useState } from "react";
import { WorkStep } from "@/types/events";
import { MarkdownRenderer } from "./MarkdownRenderer";

type ToolStepProps = {
  step: WorkStep;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

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

function fencedCode(language: string, code: string): string {
  const safe = code.replace(/```/g, "``\\`");
  return `\`\`\`${language}\n${safe}\n\`\`\``;
}

type EnvItem = {
  name: string;
  type: string;
  preview: string;
  redacted?: boolean;
};

type EnvSnapshotView = {
  afterCount: number;
  truncated: boolean;
  items: EnvItem[];
  addedCount: number;
  updatedCount: number;
  removedCount: number;
  addedNames: string[];
  updatedNames: string[];
  removedNames: string[];
};

function parseEnvItems(value: unknown): EnvItem[] {
  if (!Array.isArray(value)) return [];
  const out: EnvItem[] = [];
  for (const row of value) {
    const item = asRecord(row);
    if (!item) continue;
    const name = typeof item.name === "string" ? item.name : "";
    if (!name) continue;
    out.push({
      name,
      type: typeof item.type === "string" ? item.type : "unknown",
      preview: typeof item.preview === "string" ? item.preview : "",
      redacted: item.redacted === true,
    });
  }
  return out;
}

function parseEnvSnapshot(raw: unknown): EnvSnapshotView | null {
  const env = asRecord(raw);
  if (!env) return null;
  const after = asRecord(env.after);
  const delta = asRecord(env.delta);
  const items = parseEnvItems(after?.items);
  const addedItems = parseEnvItems(delta?.added);
  const updatedItems = parseEnvItems(delta?.updated);
  const removedItems = parseEnvItems(delta?.removed);

  return {
    afterCount: typeof after?.count === "number" ? after.count : items.length,
    truncated: after?.truncated === true,
    items: items.slice(0, 30),
    addedCount: typeof delta?.added_count === "number" ? delta.added_count : addedItems.length,
    updatedCount: typeof delta?.updated_count === "number" ? delta.updated_count : updatedItems.length,
    removedCount: typeof delta?.removed_count === "number" ? delta.removed_count : removedItems.length,
    addedNames: addedItems.slice(0, 10).map((item) => item.name),
    updatedNames: updatedItems.slice(0, 10).map((item) => item.name),
    removedNames: removedItems.slice(0, 10).map((item) => item.name),
  };
}

function extractKgGraphUpdateHint(step: WorkStep): string | null {
  if (step.toolName !== "kg_query" && step.toolName !== "kg_cypher_execute") return null;
  const result = asRecord(step.toolResult);
  if (!result) return null;
  const output = asRecord(result.output);
  const data = asRecord(output?.data);
  const subgraph = asRecord(data?.subgraph);
  const summary = asRecord(subgraph?.summary);
  const nodeCount = summary?.node_count;
  const edgeCount = summary?.edge_count;
  if (typeof nodeCount !== "number" || typeof edgeCount !== "number") return null;
  return `KG graph updated: ${nodeCount} node${nodeCount === 1 ? "" : "s"}, ${edgeCount} edge${edgeCount === 1 ? "" : "s"}`;
}

export function ToolStep({ step }: ToolStepProps) {
  const [showResult, setShowResult] = useState(false);
  const isStreaming = step.status === "streaming";
  const toolName = step.toolName || "tool";
  const isRepl = toolName === "repl_exec" || step.kind === "repl";
  const isBash = toolName === "bash_exec";
  const hasStructuredCode = Boolean(step.code || step.command || step.stdout || step.stderr || step.result);
  const useStructuredMode = isRepl || isBash || hasStructuredCode;

  const { label, result } = parseToolText(step.text);
  const { parsed, display } = result ? tryParseJson(result) : { parsed: null, display: "" };
  const summary = parsed ? extractSummary(parsed) : null;
  const output = asRecord(step.result?.output);
  const resultSummary =
    typeof output?.summary === "string"
      ? output.summary
      : typeof step.result?.status === "string"
        ? step.result.status
        : null;
  const envSnapshot = parseEnvSnapshot(step.replEnv ?? output?.env);

  if (useStructuredMode) {
    const shellCommand = step.command ?? (typeof output?.command === "string" ? output.command : "");
    const pythonCode = step.code ?? "";
    const stdout = step.stdout ?? "";
    const stderr = step.stderr ?? "";
    const rawResult = step.result ? JSON.stringify(step.result, null, 2) : "";

    return (
      <div className={`tool-step tool-step--code ${isStreaming ? "tool-step--streaming" : ""}`}>
        <div className="tool-step__header">
          <span className="tool-step__icon">&#x1F527;</span>
          <span className="tool-step__name">{toolName}</span>
          <span className="tool-step__status-badge">
            <StatusBadge status={step.status} />
          </span>
        </div>

        {step.text && <div className="tool-step__label">{step.text}</div>}

        {isRepl && pythonCode && (
          <div className="tool-step__section">
            <div className="tool-step__section-title">Python</div>
            <MarkdownRenderer content={fencedCode("python", pythonCode)} streaming={isStreaming} />
          </div>
        )}

        {isBash && shellCommand && (
          <div className="tool-step__section">
            <div className="tool-step__section-title">Command</div>
            <MarkdownRenderer content={fencedCode("bash", shellCommand)} streaming={isStreaming} />
          </div>
        )}

        {stdout && (
          <div className="tool-step__section">
            <div className="tool-step__section-title">stdout</div>
            <MarkdownRenderer content={fencedCode("text", stdout)} streaming={isStreaming} />
          </div>
        )}

        {stderr && (
          <div className="tool-step__section tool-step__section--stderr">
            <div className="tool-step__section-title">stderr</div>
            <MarkdownRenderer content={fencedCode("text", stderr)} streaming={isStreaming} />
          </div>
        )}

        {isRepl && envSnapshot && (
          <div className="tool-step__section">
            <div className="tool-step__section-title">Environment</div>
            <div className="tool-step__env-meta">
              after: {envSnapshot.afterCount} vars {envSnapshot.truncated ? "(truncated)" : ""} | delta: +
              {envSnapshot.addedCount} ~{envSnapshot.updatedCount} -{envSnapshot.removedCount}
            </div>
            {envSnapshot.items.length > 0 && (
              <div className="tool-step__env-table-wrap">
                <table className="tool-step__env-table">
                  <thead>
                    <tr>
                      <th>name</th>
                      <th>type</th>
                      <th>preview</th>
                    </tr>
                  </thead>
                  <tbody>
                    {envSnapshot.items.map((item) => (
                      <tr key={item.name}>
                        <td>{item.name}</td>
                        <td>{item.type}</td>
                        <td>{item.redacted ? "[REDACTED]" : item.preview}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {(envSnapshot.addedNames.length > 0 || envSnapshot.updatedNames.length > 0 || envSnapshot.removedNames.length > 0) && (
              <div className="tool-step__env-delta">
                {envSnapshot.addedNames.length > 0 && (
                  <div>added: {envSnapshot.addedNames.join(", ")}</div>
                )}
                {envSnapshot.updatedNames.length > 0 && (
                  <div>updated: {envSnapshot.updatedNames.join(", ")}</div>
                )}
                {envSnapshot.removedNames.length > 0 && (
                  <div>removed: {envSnapshot.removedNames.join(", ")}</div>
                )}
              </div>
            )}
          </div>
        )}

        {resultSummary && (
          <div className="tool-step__label" style={{ fontSize: "0.78rem" }}>
            {resultSummary}
          </div>
        )}

        {rawResult && (
          <>
            <button
              type="button"
              className="tool-step__result-toggle"
              onClick={() => setShowResult(!showResult)}
            >
              {showResult ? "\u25BC" : "\u25B6"} {showResult ? "Hide" : "Show"} raw tool result
            </button>
            {showResult && <div className="tool-step__result">{rawResult}</div>}
          </>
        )}

        {isStreaming && !stdout && !stderr && <div className="tool-step__shimmer" />}
      </div>
    );
  }
  const kgGraphHint = extractKgGraphUpdateHint(step);

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
      {kgGraphHint && (
        <div className="tool-step__label tool-step__label--kg">
          {kgGraphHint}
        </div>
      )}

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
