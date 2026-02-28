from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ClaimContext:
    query: str
    intervention: str
    population: str
    outcome: str
    comparator: str
    claim_mode: str = "explicit"
    ask_clarify: bool = False
    directness_warnings: list[str] = field(default_factory=list)
    ambiguity_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StudyRecord:
    study_key: str
    source: str
    title: str | None
    year: int | None
    ids: dict[str, str] = field(default_factory=dict)
    evidence_level: int | None = None
    study_type: str = "unknown"
    population_class: str = "unknown"
    endpoint_class: str = "mechanistic_only"
    quality_flags: list[str] = field(default_factory=list)
    directness_flags: list[str] = field(default_factory=list)
    effect_direction: str = "unknown"
    citations: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidenceLedger:
    records: list[StudyRecord] = field(default_factory=list)
    dedupe_stats: dict[str, int] = field(default_factory=dict)
    counts_by_level: dict[str, int] = field(default_factory=dict)
    counts_by_endpoint: dict[str, int] = field(default_factory=dict)
    counts_by_source: dict[str, int] = field(default_factory=dict)
    coverage_gaps: list[str] = field(default_factory=list)
    optional_source_status: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["records"] = [record.to_dict() for record in self.records]
        return out


@dataclass(slots=True)
class ScoreTrace:
    ces: float
    mp: float
    final_confidence: float
    penalties: list[dict[str, Any]] = field(default_factory=list)
    bonuses: list[dict[str, Any]] = field(default_factory=list)
    caps_applied: list[dict[str, Any]] = field(default_factory=list)
    components: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
