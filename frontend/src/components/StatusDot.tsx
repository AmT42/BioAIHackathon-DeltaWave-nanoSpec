"use client";

type StatusDotProps = {
  status: "connected" | "disconnected" | "streaming";
  label?: string;
};

export function StatusDot({ status, label }: StatusDotProps) {
  return (
    <span className="status-dot">
      <span className={`status-dot__circle status-dot__circle--${status}`} />
      {label && <span>{label}</span>}
    </span>
  );
}
