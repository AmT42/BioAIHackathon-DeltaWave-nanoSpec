"use client";

import { EvidencePanelData } from "@/types/evidence";

type EvidenceStrengthPanelProps = {
  evidence: EvidencePanelData;
};

const PYRAMID_ROWS = [
  { key: "level_1", label: "Meta-analyses", bucket: "human" },
  { key: "level_2", label: "RCTs", bucket: "human" },
  { key: "level_3", label: "Observational", bucket: "human" },
  { key: "level_4", label: "Animal Models", bucket: "animal" },
  { key: "level_5", label: "In Vitro", bucket: "invitro" },
  { key: "level_6", label: "In Silico", bucket: "invitro" },
] as const;

function clamp(value: number, min: number, max: number): number {
  if (value < min) return min;
  if (value > max) return max;
  return value;
}

export function EvidenceStrengthPanel({ evidence }: EvidenceStrengthPanelProps) {
  const score = Math.round(clamp(evidence.summary.score, 0, 100));
  const radius = 48;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (score / 100) * circumference;
  const counts = PYRAMID_ROWS.map((row) => evidence.pyramid[row.key]);
  const maxCount = Math.max(...counts, 1);

  return (
    <section className="evidence-card">
      <div className="evidence-card__header">
        <h3 className="evidence-card__title">Evidence Strength</h3>
        <span className="evidence-card__badge">
          {evidence.summary.label} • {evidence.summary.confidence}
        </span>
      </div>

      <div className="evidence-strength">
        <div className="evidence-gauge">
          <svg viewBox="0 0 120 120" className="evidence-gauge__svg" aria-hidden="true">
            <circle className="evidence-gauge__track" cx="60" cy="60" r={radius} />
            <circle
              className="evidence-gauge__fill"
              cx="60"
              cy="60"
              r={radius}
              strokeDasharray={circumference}
              strokeDashoffset={offset}
            />
          </svg>
          <div className="evidence-gauge__center">
            <span className="evidence-gauge__value">{score}</span>
            <span className="evidence-gauge__max">/100</span>
          </div>
        </div>

        <div className="evidence-strength__meta">
          <div className="evidence-pyramid">
            {PYRAMID_ROWS.map((row) => {
              const count = evidence.pyramid[row.key];
              const width = maxCount > 0 ? Math.max(12, (count / maxCount) * 100) : 12;
              return (
                <div key={row.key} className="evidence-pyramid__row">
                  <div
                    className={`evidence-pyramid__bar evidence-pyramid__bar--${row.bucket}`}
                    style={{ width: `${width}%` }}
                  />
                  <span className="evidence-pyramid__label">
                    {row.label} ({count})
                  </span>
                </div>
              );
            })}
          </div>

          <div className="evidence-ratio">
            <p className="evidence-ratio__title">Human / Animal / In Vitro</p>
            <div className="evidence-ratio__bar">
              <div className="evidence-ratio__segment evidence-ratio__segment--human" style={{ width: `${evidence.ratios.human}%` }} />
              <div className="evidence-ratio__segment evidence-ratio__segment--animal" style={{ width: `${evidence.ratios.animal}%` }} />
              <div className="evidence-ratio__segment evidence-ratio__segment--invitro" style={{ width: `${evidence.ratios.invitro}%` }} />
            </div>
            <div className="evidence-ratio__labels">
              <span>{evidence.ratios.human}% H</span>
              <span>{evidence.ratios.animal}% A</span>
              <span>{evidence.ratios.invitro}% IV</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
