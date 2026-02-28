"use client";

import { ThreadMeta } from "@/types/threads";
import { formatRelativeTime } from "@/lib/storage";

type SidebarProps = {
  threads: ThreadMeta[];
  activeThreadId: string | null;
  onSelectThread: (threadId: string) => void;
  onNewThread: () => void;
  onDeleteThread: (threadId: string) => void;
  open: boolean;
};

export function Sidebar({
  threads,
  activeThreadId,
  onSelectThread,
  onNewThread,
  onDeleteThread,
  open,
}: SidebarProps) {
  return (
    <aside className={`sidebar ${open ? "" : "sidebar--closed"}`}>
      <div className="sidebar__header">
        <span className="sidebar__title">Threads</span>
        <button
          type="button"
          className="sidebar__new-btn"
          onClick={onNewThread}
          title="New thread"
        >
          +
        </button>
      </div>
      <div className="sidebar__list">
        {threads.length === 0 ? (
          <div className="sidebar__empty">No conversations yet</div>
        ) : (
          threads.map((thread) => (
            <div
              key={thread.id}
              className={`sidebar__item ${thread.id === activeThreadId ? "sidebar__item--active" : ""}`}
              onClick={() => onSelectThread(thread.id)}
            >
              <div className="sidebar__item-content">
                <div className="sidebar__item-title">{thread.title}</div>
                <div className="sidebar__item-time">
                  {formatRelativeTime(thread.lastActiveAt)}
                </div>
              </div>
              <button
                type="button"
                className="sidebar__item-delete"
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteThread(thread.id);
                }}
                title="Delete thread"
              >
                &times;
              </button>
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
