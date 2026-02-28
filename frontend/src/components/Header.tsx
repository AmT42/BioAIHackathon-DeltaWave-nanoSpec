"use client";

import { StatusDot } from "./StatusDot";

type HeaderProps = {
  threadId: string | null;
  connected: boolean;
  streaming: boolean;
  onNewThread: () => void;
  onToggleSidebar: () => void;
  sidebarOpen: boolean;
};

export function Header({
  threadId,
  connected,
  streaming,
  onNewThread,
  onToggleSidebar,
  sidebarOpen,
}: HeaderProps) {
  const statusMode = streaming ? "streaming" : connected ? "connected" : "disconnected";
  const statusLabel = streaming ? "Working" : connected ? "Connected" : "Disconnected";

  return (
    <header className={`header ${streaming ? "header--streaming" : ""}`}>
      <button
        type="button"
        className="header__toggle"
        onClick={onToggleSidebar}
        title={sidebarOpen ? "Close sidebar" : "Open sidebar"}
      >
        {sidebarOpen ? "\u2715" : "\u2630"}
      </button>

      <div className="header__brand">
        <span className="header__title">Longevity Agent</span>
        {threadId && (
          <span className="header__subtitle" title={threadId}>
            {threadId.slice(0, 12)}...
          </span>
        )}
      </div>

      <div className="header__controls">
        <StatusDot status={statusMode} label={statusLabel} />
        <button type="button" className="header__btn header__btn--primary" onClick={onNewThread}>
          + New
        </button>
      </div>
    </header>
  );
}
