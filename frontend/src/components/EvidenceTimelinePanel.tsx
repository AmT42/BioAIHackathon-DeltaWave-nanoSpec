"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EvidencePanelData } from "@/types/evidence";

type EvidenceTimelinePanelProps = {
  evidence: EvidencePanelData;
};

export function EvidenceTimelinePanel({ evidence }: EvidenceTimelinePanelProps) {
  return (
    <section className="evidence-card evidence-card--timeline">
      <div className="evidence-card__header">
        <h3 className="evidence-card__title">Evidence Timeline</h3>
        <span className="evidence-card__badge">{evidence.totalRecords} records</span>
      </div>

      {evidence.timeline.length === 0 ? (
        <div className="evidence-card__empty">
          No dated records found in the structured evidence payload.
        </div>
      ) : (
        <div className="evidence-timeline">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={evidence.timeline} margin={{ top: 6, right: 10, left: -16, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--evidence-grid)" />
              <XAxis dataKey="year" tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }} stroke="var(--text-tertiary)" />
              <YAxis allowDecimals={false} tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }} stroke="var(--text-tertiary)" />
              <Tooltip
                contentStyle={{
                  backgroundColor: "var(--bg-secondary)",
                  border: "1px solid var(--border-default)",
                  borderRadius: "6px",
                  fontSize: "12px",
                  fontFamily: "var(--font-mono)",
                  color: "var(--text-primary)",
                }}
              />
              <Legend iconSize={8} wrapperStyle={{ fontSize: "11px", fontFamily: "var(--font-mono)" }} />
              <Line type="monotone" dataKey="human" stroke="var(--evidence-human)" strokeWidth={1.8} dot={{ r: 2 }} name="Human" />
              <Line type="monotone" dataKey="animal" stroke="var(--evidence-animal)" strokeWidth={1.8} dot={{ r: 2 }} name="Animal" />
              <Line type="monotone" dataKey="invitro" stroke="var(--evidence-invitro)" strokeWidth={1.8} dot={{ r: 2 }} name="In Vitro" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}
