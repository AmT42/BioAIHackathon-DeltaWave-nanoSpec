export type EvidenceSummary = {
  score: number;
  label: string;
  confidence: string;
  notes: string[];
};

export type EvidencePyramid = {
  level_1: number;
  level_2: number;
  level_3: number;
  level_4: number;
  level_5: number;
  level_6: number;
};

export type EvidenceRecord = {
  studyKey: string;
  year: number | null;
  evidenceLevel: number | null;
  populationClass: string;
  source: string;
  endpointClass: string;
};

export type EvidenceTimelinePoint = {
  year: string;
  human: number;
  animal: number;
  invitro: number;
};

export type EvidenceRatios = {
  human: number;
  animal: number;
  invitro: number;
};

export type EvidencePanelData = {
  summary: EvidenceSummary;
  pyramid: EvidencePyramid;
  records: EvidenceRecord[];
  timeline: EvidenceTimelinePoint[];
  ratios: EvidenceRatios;
  totalRecords: number;
};
