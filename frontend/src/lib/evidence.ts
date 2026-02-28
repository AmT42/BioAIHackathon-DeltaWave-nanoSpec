import {
  EvidencePanelData,
  EvidencePyramid,
  EvidenceRecord,
  EvidenceSummary,
  EvidenceTimelinePoint,
} from "@/types/evidence";

const EVIDENCE_REPORT_TOOL = "evidence_render_report";

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function normalizeToolName(value: unknown): string {
  if (typeof value !== "string") return "";
  return value.trim();
}

function parseJsonObject(value: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(value);
    return asRecord(parsed);
  } catch {
    return null;
  }
}

function parseObject(value: unknown): Record<string, unknown> | null {
  const record = asRecord(value);
  if (record) return record;
  if (typeof value === "string") return parseJsonObject(value);
  return null;
}

function toNumber(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function toIntOrNull(value: unknown): number | null {
  if (typeof value === "number" && Number.isInteger(value)) return value;
  if (typeof value === "string") {
    const match = value.match(/(19|20)\d{2}/);
    if (!match) return null;
    const parsed = Number(match[0]);
    return Number.isInteger(parsed) ? parsed : null;
  }
  return null;
}

function toStringValue(value: unknown, fallback = ""): string {
  if (typeof value === "string") return value.trim() || fallback;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return fallback;
}

function clamp(value: number, min: number, max: number): number {
  if (value < min) return min;
  if (value > max) return max;
  return value;
}

function parseSummary(raw: Record<string, unknown> | null): EvidenceSummary | null {
  if (!raw) return null;
  const score = clamp(toNumber(raw.score, Number.NaN), 0, 100);
  if (!Number.isFinite(score)) return null;
  return {
    score,
    label: toStringValue(raw.label, "N/A"),
    confidence: toStringValue(raw.confidence, "unknown"),
    notes: asArray(raw.notes).map((note) => toStringValue(note)).filter((note) => note.length > 0),
  };
}

function parsePyramid(raw: Record<string, unknown> | null): EvidencePyramid {
  return {
    level_1: Math.max(0, Math.round(toNumber(raw?.level_1))),
    level_2: Math.max(0, Math.round(toNumber(raw?.level_2))),
    level_3: Math.max(0, Math.round(toNumber(raw?.level_3))),
    level_4: Math.max(0, Math.round(toNumber(raw?.level_4))),
    level_5: Math.max(0, Math.round(toNumber(raw?.level_5))),
    level_6: Math.max(0, Math.round(toNumber(raw?.level_6))),
  };
}

function parseRecord(raw: unknown): EvidenceRecord | null {
  const row = asRecord(raw);
  if (!row) return null;
  return {
    studyKey: toStringValue(row.study_key, "unknown"),
    year: toIntOrNull(row.year),
    evidenceLevel: toIntOrNull(row.evidence_level),
    populationClass: toStringValue(row.population_class, "unknown").toLowerCase(),
    source: toStringValue(row.source, "unknown"),
    endpointClass: toStringValue(row.endpoint_class, "unknown"),
  };
}

function bucketForRecord(record: EvidenceRecord): "human" | "animal" | "invitro" | "unknown" {
  const population = record.populationClass.toLowerCase();
  if (population.includes("human")) return "human";
  if (population.includes("animal")) return "animal";
  if (population.includes("cell")) return "invitro";
  if (population.includes("vitro")) return "invitro";
  if (population.includes("comput")) return "invitro";
  if (population.includes("silico")) return "invitro";

  const level = record.evidenceLevel;
  if (level === 1 || level === 2 || level === 3) return "human";
  if (level === 4) return "animal";
  if (level === 5 || level === 6) return "invitro";
  return "unknown";
}

function buildTimeline(records: EvidenceRecord[]): EvidenceTimelinePoint[] {
  const byYear = new Map<number, { human: number; animal: number; invitro: number }>();

  for (const record of records) {
    if (record.year === null) continue;
    const bucket = bucketForRecord(record);
    if (bucket === "unknown") continue;
    const current = byYear.get(record.year) ?? { human: 0, animal: 0, invitro: 0 };
    current[bucket] += 1;
    byYear.set(record.year, current);
  }

  return Array.from(byYear.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([year, counts]) => ({
      year: String(year),
      human: counts.human,
      animal: counts.animal,
      invitro: counts.invitro,
    }));
}

function buildRatios(pyramid: EvidencePyramid): { human: number; animal: number; invitro: number } {
  const human = pyramid.level_1 + pyramid.level_2 + pyramid.level_3;
  const animal = pyramid.level_4;
  const invitro = pyramid.level_5 + pyramid.level_6;
  const total = human + animal + invitro;
  if (total <= 0) return { human: 0, animal: 0, invitro: 0 };
  return {
    human: Math.round((human / total) * 100),
    animal: Math.round((animal / total) * 100),
    invitro: Math.round((invitro / total) * 100),
  };
}

export function parseEvidenceReportJson(reportJson: Record<string, unknown> | null): EvidencePanelData | null {
  if (!reportJson) return null;

  const summary = parseSummary(asRecord(reportJson.evidence_summary));
  if (!summary) return null;

  const pyramid = parsePyramid(asRecord(reportJson.evidence_pyramid));
  const records = asArray(reportJson.records)
    .map(parseRecord)
    .filter((record): record is EvidenceRecord => Boolean(record));
  const timeline = buildTimeline(records);
  const ratios = buildRatios(pyramid);

  return {
    summary,
    pyramid,
    records,
    timeline,
    ratios,
    totalRecords: records.length,
  };
}

export function extractEvidenceFromToolResult(
  toolName: unknown,
  toolResult: Record<string, unknown> | null
): EvidencePanelData | null {
  if (normalizeToolName(toolName) !== EVIDENCE_REPORT_TOOL) return null;
  if (!toolResult) return null;

  const status = toStringValue(toolResult.status).toLowerCase();
  if (status !== "success" && status !== "completed") return null;

  const output = asRecord(toolResult.output);
  const data = asRecord(output?.data);
  const reportJson = asRecord(data?.report_json);
  return parseEvidenceReportJson(reportJson);
}

export function extractLatestEvidenceFromTraceBlocks(
  blocks: Array<Record<string, unknown>>
): EvidencePanelData | null {
  let latest: EvidencePanelData | null = null;
  for (const block of blocks) {
    if (String(block.type ?? "") !== "tool_result") continue;
    const resultPayload = parseObject(block.content) ?? parseObject(block.result);
    const evidence = extractEvidenceFromToolResult(block.name, resultPayload);
    if (evidence) latest = evidence;
  }
  return latest;
}
