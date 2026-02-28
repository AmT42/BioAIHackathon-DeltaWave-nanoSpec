"use client";

import { CSSProperties, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";

type MarkdownRendererProps = {
  content: string;
  className?: string;
  streaming?: boolean;
};

type OpenFence = {
  language: string;
  code: string;
};

type SplitContent = {
  markdown: string;
  openFence: OpenFence | null;
};

const LANGUAGE_ALIASES: Record<string, string> = {
  py: "python",
  python3: "python",
  shell: "bash",
  sh: "bash",
  zsh: "bash",
  console: "bash",
  ps1: "powershell",
  yml: "yaml",
  ts: "typescript",
  js: "javascript",
};

const CODE_THEME: Record<string, CSSProperties> = {
  'code[class*="language-"]': {
    color: "#d7dee9",
    background: "transparent",
    fontFamily: "var(--font-mono)",
    fontSize: "0.84rem",
    lineHeight: "1.65",
    textShadow: "none",
    whiteSpace: "pre",
    wordBreak: "normal",
    overflowWrap: "normal",
  },
  'pre[class*="language-"]': {
    color: "#d7dee9",
    background: "transparent",
    margin: 0,
    padding: 0,
    textShadow: "none",
  },
  comment: { color: "#6f7785", fontStyle: "italic" },
  punctuation: { color: "#9aa5b5" },
  property: { color: "#8fd3ff" },
  tag: { color: "#8fd3ff" },
  boolean: { color: "#f7c989" },
  number: { color: "#f7c989" },
  constant: { color: "#f7c989" },
  symbol: { color: "#8fd3ff" },
  selector: { color: "#a6e3a1" },
  "attr-name": { color: "#a6e3a1" },
  string: { color: "#a6e3a1" },
  char: { color: "#a6e3a1" },
  builtin: { color: "#9dc3ff" },
  inserted: { color: "#a6e3a1" },
  operator: { color: "#cfd8e3" },
  entity: { color: "#9dc3ff" },
  url: { color: "#9dc3ff" },
  keyword: { color: "#c7b6ff", fontWeight: 500 },
  function: { color: "#93c5fd" },
  "class-name": { color: "#93c5fd" },
  regex: { color: "#ffb4a2" },
  important: { color: "#f9e2af", fontWeight: 600 },
  variable: { color: "#d7dee9" },
};

const CODE_CUSTOM_STYLE: CSSProperties = {
  margin: 0,
  background: "transparent",
  padding: "14px 16px 16px",
  whiteSpace: "pre",
  overflowX: "auto",
  tabSize: 4,
  wordBreak: "normal",
  overflowWrap: "normal",
};

const CODE_LINE_NUMBER_STYLE: CSSProperties = {
  minWidth: "2.4em",
  paddingRight: "1em",
  marginRight: "1em",
  borderRight: "1px solid rgba(148, 163, 184, 0.22)",
  color: "#6c7483",
  textAlign: "right",
  userSelect: "none",
};

function normalizeLanguage(raw?: string): string {
  const cleaned = (raw ?? "").trim().toLowerCase();
  if (!cleaned) return "";
  return LANGUAGE_ALIASES[cleaned] ?? cleaned;
}

function inferLanguage(code: string): string {
  const sample = code.trim();
  if (!sample) return "text";
  if (/^#!\/bin\/(ba|z)?sh/m.test(sample)) return "bash";
  if (/^\s*(\$ |sudo |cd |ls |cat |grep |awk |sed |curl |wget |npm |pnpm |yarn |pip |python )/m.test(sample)) {
    return "bash";
  }
  if (/^\s*(from\s+\w+\s+import|import\s+\w+|def\s+\w+\(|class\s+\w+|if __name__ == ["']__main__["'])/m.test(sample)) {
    return "python";
  }
  return "text";
}

function languageLabel(language: string): string {
  if (!language) return "text";
  return language.toLowerCase();
}

function splitOpenFence(content: string): SplitContent {
  const fenceMatches = [...content.matchAll(/```/g)];
  if (fenceMatches.length === 0 || fenceMatches.length % 2 === 0) {
    return { markdown: content, openFence: null };
  }

  const lastFence = fenceMatches[fenceMatches.length - 1];
  const lastFenceIndex = typeof lastFence.index === "number" ? lastFence.index : -1;
  if (lastFenceIndex < 0) return { markdown: content, openFence: null };

  const charBefore = content[lastFenceIndex - 1];
  if (lastFenceIndex > 0 && charBefore !== "\n") {
    return { markdown: content, openFence: null };
  }

  const beforeFence = content.slice(0, lastFenceIndex);
  const afterFence = content.slice(lastFenceIndex + 3);
  const firstLineBreak = afterFence.indexOf("\n");

  if (firstLineBreak === -1) {
    return {
      markdown: beforeFence,
      openFence: {
        language: afterFence.trim(),
        code: "",
      },
    };
  }

  return {
    markdown: beforeFence,
    openFence: {
      language: afterFence.slice(0, firstLineBreak).trim(),
      code: afterFence.slice(firstLineBreak + 1),
    },
  };
}

function copyTextToClipboard(value: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(value);
  }

  return new Promise((resolve, reject) => {
    if (typeof document === "undefined") {
      reject(new Error("Clipboard unavailable"));
      return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    try {
      document.execCommand("copy");
      document.body.removeChild(textarea);
      resolve();
    } catch (error) {
      document.body.removeChild(textarea);
      reject(error);
    }
  });
}

function CodeBlock({ code, language, streaming = false }: { code: string; language?: string; streaming?: boolean }) {
  const [copied, setCopied] = useState(false);

  const normalized = useMemo(() => {
    const explicit = normalizeLanguage(language);
    return explicit || inferLanguage(code);
  }, [language, code]);

  const codeBody = useMemo(() => code.replace(/\n$/, ""), [code]);
  const lineCount = useMemo(() => (codeBody ? codeBody.split("\n").length : 1), [codeBody]);

  async function handleCopy() {
    try {
      await copyTextToClipboard(codeBody);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className={`code-block ${streaming ? "code-block--streaming" : ""}`}>
      <div className="code-block__header">
        <div className="code-block__header-meta">
          <span className="code-block__lang">{languageLabel(normalized)}</span>
          {streaming && <span className="code-block__live">live</span>}
        </div>
        <button
          type="button"
          className="code-block__copy"
          onClick={handleCopy}
          aria-label="Copy code"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <SyntaxHighlighter
        language={normalized === "text" ? undefined : normalized}
        style={CODE_THEME}
        PreTag="div"
        customStyle={CODE_CUSTOM_STYLE}
        showLineNumbers={lineCount > 4}
        lineNumberStyle={CODE_LINE_NUMBER_STYLE}
        wrapLongLines={false}
      >
        {codeBody || " "}
      </SyntaxHighlighter>
    </div>
  );
}

export function MarkdownRenderer({ content, className, streaming = false }: MarkdownRendererProps) {
  const { markdown, openFence } = useMemo(() => splitOpenFence(content), [content]);

  return (
    <div className={`markdown-body ${className ?? ""}`.trim()}>
      {markdown ? (
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            code(props) {
              const { children, className: codeClassName } = props;
              const match = /language-([\w-]+)/.exec(codeClassName ?? "");
              const raw = String(children ?? "");
              const isInline = !match && !raw.includes("\n");

              if (isInline) {
                return <code className="markdown-inline-code">{children}</code>;
              }

              return <CodeBlock code={raw} language={match?.[1]} />;
            },
          }}
        >
          {markdown}
        </ReactMarkdown>
      ) : null}
      {openFence ? (
        <CodeBlock
          code={openFence.code}
          language={openFence.language}
          streaming={streaming}
        />
      ) : null}
    </div>
  );
}
