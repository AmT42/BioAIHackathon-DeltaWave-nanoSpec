"""
CROssBAR Knowledge Graph query tool for nanoSpec.

Translates natural-language biomedical questions into Cypher queries,
validates relationship directions against the graph schema, executes
them on a Neo4j database, and returns structured entity data.

Ported from CROssBAR_LLM (minimal_gemini_kg_adapter + neo4j_query_corrector).
LangChain dependency replaced with direct google.genai calls; CROssBAR
logging utilities replaced with stdlib logging.
"""

from __future__ import annotations

import json
import logging
import re
from collections import namedtuple
from pathlib import Path
from numbers import Number
from typing import Any

from app.agent.tools.contracts import make_tool_output
from app.agent.tools.descriptions import render_tool_description
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.registry import ToolSpec

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------

_SCHEMA_PATH = Path(__file__).with_name("crossbar_schema.json")


def _load_schema() -> dict[str, Any]:
    with open(_SCHEMA_PATH, encoding="utf-8") as fp:
        schema = json.load(fp)
    required = {"nodes", "node_properties", "edge_properties", "edges"}
    missing = required - set(schema)
    if missing:
        raise ValueError(f"crossbar_schema.json missing keys: {sorted(missing)}")
    return schema


# ---------------------------------------------------------------------------
# Cypher generation prompt
# ---------------------------------------------------------------------------

CYPHER_TEMPLATE = """\
Task: Generate a Cypher query for the given graph schema.

Rules:
- Use only node labels, relationship types, and properties from the provided schema.
- Return only Cypher. No markdown, explanations, or extra text.
- Do not invent labels, relationships, or properties.
- ALWAYS use case-insensitive matching for names: use toLower() on both sides, \
e.g. WHERE toLower(n.name) CONTAINS toLower('user input')
- Users may use common/trade names. Map them to generic names when obvious \
(e.g. rapamycin -> sirolimus, aspirin -> Acetylsalicylic acid, \
tylenol -> Acetaminophen).
- If returning nodes/entities, include their `id` and `name` in RETURN.
- ALWAYS assign a variable to relationships (e.g. -[r:REL_TYPE]-) and include \
relevant relationship properties in RETURN. Check the "Relationship properties" \
section for available properties on each relationship type.
- Prefer stable RETURN aliases for flat projections (e.g., `drug_id`, `drug_name`, \
`protein_id`, `protein_name`) instead of ambiguous bare fields like `name`.
- Prefer connected graph rows: return node-relation-node tuples, not standalone node lists.
- When user asks for traversal, neighborhood, or graph mapping, prefer explicit second-degree \
connections (A->B->C) when supported by schema, and return both relationships.
- For 2-hop queries, include ids/names for source, bridge, and target nodes and include \
relationship type/properties for each hop in RETURN.
- Never fabricate disconnected components if a connected path pattern can answer the question.
- Keep the query read-only and exploration-friendly: allow connected traversal patterns with up to 3 MATCH clauses \
and up to 1 OPTIONAL MATCH when needed for graph enrichment.
- Prefer explicit 1-hop and 2-hop path patterns anchored on user entities to maximize connected subgraph coverage.
- Do NOT use UNION, CALL subqueries, UNWIND, APOC, or variable-length path patterns (`*`).
- Return enough columns to reconstruct graph structure (node ids/names + per-hop relationship type/properties).
- Keep RETURN bounded (prefer <= 24 columns) and rely on LIMIT for result size control.
- For broader exploration, run multiple anchored queries instead of one oversized query.

Nodes:
{node_types}
Node properties:
{node_properties}
Relationship properties:
{edge_properties}
Relationships:
{edges}

Question:
{question}
"""

# ---------------------------------------------------------------------------
# Cypher cleaning helpers
# ---------------------------------------------------------------------------


def _clean_cypher(raw: str) -> str:
    cleaned = (raw or "").strip().strip("\n")
    cleaned = cleaned.replace("```cypher", "").replace("```", "")
    cleaned = cleaned.replace("cypher", "").strip("`").strip()
    cleaned = cleaned.replace("''", "'").replace('""', '"')
    return cleaned


def _apply_limit(query: str, top_k: int) -> str:
    if re.search(r"\bLIMIT\s+\d+\b", query, flags=re.IGNORECASE):
        return re.sub(r"\bLIMIT\s+\d+\b", f"LIMIT {top_k}", query, flags=re.IGNORECASE)
    return f"{query.rstrip()} LIMIT {top_k}"


_DEFAULT_KG_QUERY_TIMEOUT_SECONDS = 12.0
_MAX_GENERATED_CYPHER_CHARS = 1800
_MAX_GENERATED_MATCH_CLAUSES = 3
_MAX_GENERATED_OPTIONAL_MATCH_CLAUSES = 1
_MAX_GENERATED_WITH_CLAUSES = 2
_MAX_GENERATED_RETURN_COLUMNS = 24


def _normalize_query_timeout_seconds(value: Any) -> float:
    try:
        timeout = float(value)
    except Exception:
        return _DEFAULT_KG_QUERY_TIMEOUT_SECONDS
    if timeout < 1.0:
        return _DEFAULT_KG_QUERY_TIMEOUT_SECONDS
    return min(timeout, 120.0)


def _query_timeout_seconds(settings: Any) -> float:
    return _normalize_query_timeout_seconds(getattr(settings, "kg_query_timeout_seconds", _DEFAULT_KG_QUERY_TIMEOUT_SECONDS))


def _count_pattern(query: str, pattern: str) -> int:
    return len(re.findall(pattern, query, flags=re.IGNORECASE | re.DOTALL))


_RETURN_PATTERN = re.compile(
    r"\bRETURN\b(?P<body>.+?)(?:\bORDER\s+BY\b|\bSKIP\b|\bLIMIT\b|$)",
    flags=re.IGNORECASE | re.DOTALL,
)


def _estimate_return_column_count(query: str) -> int:
    matches = list(_RETURN_PATTERN.finditer(query))
    if not matches:
        return 0
    body = re.sub(r"^\s*DISTINCT\s+", "", matches[-1].group("body"), flags=re.IGNORECASE).strip()
    if not body:
        return 0

    depth_round = 0
    depth_square = 0
    depth_curly = 0
    columns = 1
    for ch in body:
        if ch == "(":
            depth_round += 1
        elif ch == ")":
            depth_round = max(0, depth_round - 1)
        elif ch == "[":
            depth_square += 1
        elif ch == "]":
            depth_square = max(0, depth_square - 1)
        elif ch == "{":
            depth_curly += 1
        elif ch == "}":
            depth_curly = max(0, depth_curly - 1)
        elif ch == "," and depth_round == 0 and depth_square == 0 and depth_curly == 0:
            columns += 1
    return columns


def _generated_cypher_guardrail_violations(query: str) -> list[str]:
    issues: list[str] = []
    compact = str(query or "").strip()
    if not compact:
        return ["query is empty"]

    if len(compact) > _MAX_GENERATED_CYPHER_CHARS:
        issues.append(f"query length exceeds {_MAX_GENERATED_CYPHER_CHARS} characters")

    total_match = _count_pattern(compact, r"\bMATCH\b")
    optional_match = _count_pattern(compact, r"\bOPTIONAL\s+MATCH\b")
    non_optional_match = max(0, total_match - optional_match)
    if non_optional_match > _MAX_GENERATED_MATCH_CLAUSES:
        issues.append(f"too many MATCH clauses ({non_optional_match} > {_MAX_GENERATED_MATCH_CLAUSES})")
    if optional_match > _MAX_GENERATED_OPTIONAL_MATCH_CLAUSES:
        issues.append(
            f"too many OPTIONAL MATCH clauses ({optional_match} > {_MAX_GENERATED_OPTIONAL_MATCH_CLAUSES})"
        )

    with_count = _count_pattern(compact, r"\bWITH\b")
    if with_count > _MAX_GENERATED_WITH_CLAUSES:
        issues.append(f"too many WITH clauses ({with_count} > {_MAX_GENERATED_WITH_CLAUSES})")

    return_columns = _estimate_return_column_count(compact)
    if return_columns > _MAX_GENERATED_RETURN_COLUMNS:
        issues.append(f"too many RETURN columns ({return_columns} > {_MAX_GENERATED_RETURN_COLUMNS})")

    if re.search(r"-\[[^\]]*\*[^\]]*\]-", compact, flags=re.IGNORECASE):
        issues.append("variable-length path pattern detected (`*` inside relationship)")
    if re.search(r"\bUNION\b", compact, flags=re.IGNORECASE):
        issues.append("UNION is not allowed for generated KG queries")
    if re.search(r"\bUNWIND\b", compact, flags=re.IGNORECASE):
        issues.append("UNWIND is not allowed for generated KG queries")
    if re.search(r"\bCALL\s*\{", compact, flags=re.IGNORECASE):
        issues.append("CALL subquery is not allowed for generated KG queries")
    if re.search(r"\bapoc\.", compact, flags=re.IGNORECASE):
        issues.append("APOC procedure usage is not allowed for generated KG queries")
    return issues


_WRITE_CLAUSE_PATTERN = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b",
    flags=re.IGNORECASE,
)


def _assert_read_only_cypher(query: str) -> None:
    if _WRITE_CLAUSE_PATTERN.search(str(query or "")):
        raise ToolExecutionError(
            code="VALIDATION_ERROR",
            message="Only read-only Cypher queries are allowed.",
            details={"reason": "write_clause_detected"},
        )


def _assert_generated_cypher_guardrails(query: str) -> None:
    issues = _generated_cypher_guardrail_violations(query)
    if not issues:
        return
    raise ToolExecutionError(
        code="VALIDATION_ERROR",
        message="Generated Cypher was too complex. Retry with a narrower KG question.",
        details={"issues": issues, "cypher_preview": str(query or "")[:1200]},
    )


def _strip_embedding_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_embedding_fields(v) for k, v in value.items() if "embedding" not in k.lower()}
    if isinstance(value, list):
        return [_strip_embedding_fields(v) for v in value]
    return value


def _is_neo4j_node(value: Any) -> bool:
    return hasattr(value, "labels") and hasattr(value, "element_id")


def _is_neo4j_relationship(value: Any) -> bool:
    return (
        hasattr(value, "type")
        and hasattr(value, "start_node")
        and hasattr(value, "end_node")
        and hasattr(value, "element_id")
    )


def _is_neo4j_path(value: Any) -> bool:
    return hasattr(value, "nodes") and hasattr(value, "relationships")


def _coerce_neo4j_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _coerce_neo4j_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce_neo4j_value(v) for v in value]

    if _is_neo4j_node(value):
        node_data = {str(k): _coerce_neo4j_value(v) for k, v in value.items()}
        node_data["__kind__"] = "node"
        node_data["__element_id__"] = str(getattr(value, "element_id", ""))
        node_data["__labels__"] = sorted(str(label) for label in getattr(value, "labels", []))
        return node_data

    if _is_neo4j_relationship(value):
        rel_data = {str(k): _coerce_neo4j_value(v) for k, v in value.items()}
        rel_data["__kind__"] = "relationship"
        rel_data["__element_id__"] = str(getattr(value, "element_id", ""))
        rel_data["__type__"] = str(getattr(value, "type", "RELATED_TO"))
        rel_data["__start_element_id__"] = str(getattr(getattr(value, "start_node", None), "element_id", ""))
        rel_data["__end_element_id__"] = str(getattr(getattr(value, "end_node", None), "element_id", ""))
        return rel_data

    if _is_neo4j_path(value):
        return {
            "__kind__": "path",
            "nodes": [_coerce_neo4j_value(node) for node in getattr(value, "nodes", [])],
            "relationships": [_coerce_neo4j_value(rel) for rel in getattr(value, "relationships", [])],
        }

    if hasattr(value, "iso_format"):
        try:
            return value.iso_format()
        except Exception:
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return value


# ---------------------------------------------------------------------------
# Query corrector  (ported from neo4j_query_corrector.py)
# ---------------------------------------------------------------------------

Schema = namedtuple("Schema", ["left_node", "relation", "right_node"])


def _parse_edge_schemas(edge_list: list[str]) -> list[Schema]:
    """Parse edge schema strings like '(:A)-[:REL]->(:B)' into Schema tuples."""
    to_replace = ["(", ")", ":", "[", "]", ">", "<"]
    parts_strs: list[str] = []
    for edge in edge_list:
        pieces = edge.strip().split("-")
        cleaned: list[str] = []
        for piece in pieces:
            for ch in to_replace:
                piece = piece.replace(ch, "")
            cleaned.append(piece)
        parts_strs.append(", ".join(cleaned))

    combined = ", ".join(f"({p})" for p in parts_strs)
    values = combined.replace("(", "").replace(")", "").split(",")
    schemas: list[Schema] = []
    for i in range(len(values) // 3):
        schemas.append(Schema(values[i * 3].strip(), values[i * 3 + 1].strip(), values[i * 3 + 2].strip()))
    return schemas


class _QueryCorrector:
    property_pattern = re.compile(r"\{.+?\}")
    node_pattern = re.compile(r"\(.+?\)")
    path_pattern = re.compile(
        r"(\([^\,\(\)]*?(\{.+\})?[^\,\(\)]*?\))(<?-)(\[.*?\])?(->?)(\([^\,\(\)]*?(\{.+\})?[^\,\(\)]*?\))"
    )
    node_relation_node_pattern = re.compile(
        r"(\()+(?P<left_node>[^()]*?)\)(?P<relation>.*?)\((?P<right_node>[^()]*?)(\))+"
    )
    relation_type_pattern = re.compile(r":(?P<relation_type>.+?)?(\{.+\})?]")

    def __init__(self, schemas: list[Schema]) -> None:
        self.schemas = schemas

    def clean_node(self, node: str) -> str:
        node = re.sub(self.property_pattern, "", node)
        return node.replace("(", "").replace(")", "").strip()

    def detect_node_variables(self, query: str) -> dict[str, list[str]]:
        nodes = [self.clean_node(n) for n in re.findall(self.node_pattern, query)]
        result: dict[str, list[str]] = {}
        for node in nodes:
            parts = node.split(":")
            if parts == "":
                continue
            variable = parts[0]
            if variable not in result:
                result[variable] = []
            result[variable] += parts[1:]
        return result

    def extract_paths(self, query: str) -> list[str]:
        paths: list[str] = []
        idx = 0
        while matched := self.path_pattern.findall(query[idx:]):
            matched = matched[0]
            matched = [m for i, m in enumerate(matched) if i not in [1, len(matched) - 1]]
            path = "".join(matched)
            idx = query.find(path) + len(path) - len(matched[-1])
            paths.append(path)
        return paths

    def judge_direction(self, relation: str) -> str:
        if relation[0] == "<":
            return "INCOMING"
        if relation[-1] == ">":
            return "OUTGOING"
        return "BIDIRECTIONAL"

    def detect_labels(self, str_node: str, node_variable_dict: dict[str, list[str]]) -> list[str]:
        splitted = str_node.split(":")
        variable = splitted[0]
        if variable in node_variable_dict:
            return node_variable_dict[variable]
        if variable == "" and len(splitted) > 1:
            return splitted[1:]
        return []

    def verify_schema(self, from_labels: list[str], rel_types: list[str], to_labels: list[str]) -> bool:
        valid = list(self.schemas)
        if from_labels:
            from_labels = [l.strip("`") for l in from_labels]
            valid = [s for s in valid if s.left_node in from_labels]
        if to_labels:
            to_labels = [l.strip("`") for l in to_labels]
            valid = [s for s in valid if s.right_node in to_labels]
        if rel_types:
            rel_types = [t.strip("`") for t in rel_types]
            valid = [s for s in valid if s.relation in rel_types]
        return len(valid) > 0

    def detect_relation_types(self, str_relation: str) -> tuple[str, list[str]]:
        direction = self.judge_direction(str_relation)
        match = self.relation_type_pattern.search(str_relation)
        if match is None or match.group("relation_type") is None:
            return direction, []
        types = [t.strip().strip("!") for t in match.group("relation_type").split("|")]
        return direction, types

    def correct_query(self, query: str) -> str:
        node_vars = self.detect_node_variables(query)
        paths = self.extract_paths(query)

        for path in paths:
            original_path = path
            start_idx = 0
            while start_idx < len(path):
                m = re.match(self.node_relation_node_pattern, path[start_idx:])
                if m is None:
                    break
                start_idx += m.start()
                md = m.groupdict()

                left_labels = self.detect_labels(md["left_node"], node_vars)
                right_labels = self.detect_labels(md["right_node"], node_vars)
                end_idx = start_idx + 4 + len(md["left_node"]) + len(md["relation"]) + len(md["right_node"])
                original_partial = original_path[start_idx : end_idx + 1]
                direction, rel_types = self.detect_relation_types(md["relation"])

                if rel_types and "*" in "".join(rel_types):
                    start_idx += len(md["left_node"]) + len(md["relation"]) + 2
                    continue

                if direction == "OUTGOING":
                    if not self.verify_schema(left_labels, rel_types, right_labels):
                        if self.verify_schema(right_labels, rel_types, left_labels):
                            corrected_rel = "<" + md["relation"][:-1]
                            corrected_partial = original_partial.replace(md["relation"], corrected_rel)
                            query = query.replace(original_partial, corrected_partial)
                            log.debug("Corrected direction outgoing->incoming: %s", corrected_partial)
                        else:
                            log.warning("No valid schema for path segment: %s", original_partial)
                            return ""
                elif direction == "INCOMING":
                    if not self.verify_schema(right_labels, rel_types, left_labels):
                        if self.verify_schema(left_labels, rel_types, right_labels):
                            corrected_rel = md["relation"][1:] + ">"
                            corrected_partial = original_partial.replace(md["relation"], corrected_rel)
                            query = query.replace(original_partial, corrected_partial)
                            log.debug("Corrected direction incoming->outgoing: %s", corrected_partial)
                        else:
                            log.warning("No valid schema for path segment: %s", original_partial)
                            return ""
                else:  # BIDIRECTIONAL
                    ok = self.verify_schema(left_labels, rel_types, right_labels) or self.verify_schema(
                        right_labels, rel_types, left_labels
                    )
                    if not ok:
                        log.warning("No valid schema for bidirectional segment: %s", original_partial)
                        return ""

                start_idx += len(md["left_node"]) + len(md["relation"]) + 2

        return query


def _correct_cypher(query: str, edge_list: list[str]) -> str:
    """Top-level entry: parse schemas, run corrector, return corrected query."""
    # Strip markdown fences that may wrap the query
    raw = query.strip("\n")
    pattern = r"```(.*?)```"
    matches = re.findall(pattern, raw, re.DOTALL)
    if matches:
        raw = matches[0]

    schemas = _parse_edge_schemas(edge_list)
    corrector = _QueryCorrector(schemas)
    try:
        result = corrector.correct_query(raw)
    except Exception:
        log.exception("Query correction failed, returning original")
        return query
    return result if result else query


# ---------------------------------------------------------------------------
# Gemini Cypher generation (replaces langchain chain)
# ---------------------------------------------------------------------------


def _generate_cypher_via_gemini(question: str, schema: dict[str, Any], api_key: str) -> str:
    import google.genai as genai  # lazy

    prompt = CYPHER_TEMPLATE.format(
        node_types=json.dumps(schema["nodes"], indent=2),
        node_properties=json.dumps(schema["node_properties"], indent=2),
        edge_properties=json.dumps(schema["edge_properties"], indent=2),
        edges=json.dumps(schema["edges"], indent=2),
        question=question,
    )

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config={"temperature": 0.0},
    )
    return _clean_cypher(resp.text or "")


# ---------------------------------------------------------------------------
# Neo4j execution
# ---------------------------------------------------------------------------


def _execute_cypher(
    query: str,
    *,
    uri: str,
    user: str,
    password: str,
    database: str,
    top_k: int = 25,
    query_timeout_seconds: float = _DEFAULT_KG_QUERY_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    import neo4j  # lazy

    query_with_limit = _apply_limit(query.strip(), top_k=top_k)
    timed_query = neo4j.Query(
        query_with_limit,
        timeout=_normalize_query_timeout_seconds(query_timeout_seconds),
    )
    with neo4j.GraphDatabase.driver(uri, auth=(user, password)) as driver:
        records, _, _ = driver.execute_query(
            timed_query,
            database_=database,
            routing_="r",
        )

    if not records:
        return []

    # record.data() flattens graph entities to plain mappings; preserve raw values first.
    def _record_to_raw_mapping(record: Any) -> dict[str, Any]:
        try:
            keys = list(record.keys())
            return {str(key): record[key] for key in keys}
        except Exception:
            return record.data()

    return [_strip_embedding_fields(_coerce_neo4j_value(_record_to_raw_mapping(record))) for record in records]


# ---------------------------------------------------------------------------
# Query-local node statistics
# ---------------------------------------------------------------------------

_CONFIDENCE_NORMALIZERS: dict[str, float] = {
    "confidence_score": 1.0,
    "intact_score": 1.0,
    "opentargets_score": 1.0,
    "disgenet_gene_disease_score": 1.0,
    "disgenet_variant_disease_score": 1.0,
    "disgenet_jaccard_genes_score": 1.0,
    "disgenet_jaccard_variants_score": 1.0,
    "diseases_confidence_score": 1.0,
    "oma_orthology_score": 1.0,
    "string_combined_score": 1000.0,
    "string_physical_combined_score": 1000.0,
    "stitch_combined_score": 1000.0,
    "pchembl": 10.0,
    "max_phase": 4.0,
}

_PRIORITY_CONFIDENCE_KEYS: tuple[str, ...] = (
    "confidence_score",
    "opentargets_score",
    "intact_score",
    "disgenet_gene_disease_score",
    "disgenet_variant_disease_score",
    "diseases_confidence_score",
    "oma_orthology_score",
    "string_combined_score",
    "string_physical_combined_score",
    "stitch_combined_score",
    "pchembl",
    "max_phase",
)


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Number):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except Exception:
            return None
    return None


def _clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


def _normalize_confidence_value(key: str, value: Any) -> float | None:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    k = key.strip().lower()
    if k in _CONFIDENCE_NORMALIZERS:
        divisor = _CONFIDENCE_NORMALIZERS[k]
        if divisor <= 0:
            return None
        return _clamp(numeric / divisor, 0.0, 1.0)
    if 0.0 <= numeric <= 1.0:
        return numeric
    if numeric <= 10.0:
        return _clamp(numeric / 10.0, 0.0, 1.0)
    if numeric <= 100.0:
        return _clamp(numeric / 100.0, 0.0, 1.0)
    return _clamp(numeric / 1000.0, 0.0, 1.0)


def _select_confidence_score(properties: dict[str, Any]) -> tuple[float | None, str | None]:
    normalized_props = {str(k).strip().lower(): v for k, v in properties.items()}

    for key in _PRIORITY_CONFIDENCE_KEYS:
        if key not in normalized_props:
            continue
        score = _normalize_confidence_value(key, normalized_props[key])
        if score is not None:
            return score, key

    for key, value in normalized_props.items():
        if "score" not in key and "confidence" not in key:
            continue
        score = _normalize_confidence_value(key, value)
        if score is not None:
            return score, key
    return None, None


def _edge_weight(properties: dict[str, Any]) -> tuple[float, str | None]:
    confidence, confidence_key = _select_confidence_score(properties)
    if confidence is None:
        return 1.0, None
    return 1.0 + confidence, confidence_key


def _clean_wrapped_properties(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if not str(k).startswith("__")}


def _node_type_from_labels(labels: list[str]) -> str:
    clean_labels = [str(label).strip() for label in labels if str(label).strip()]
    if clean_labels:
        return sorted(clean_labels)[0]
    return "Unknown"


def _node_name(properties: dict[str, Any], fallback: str) -> str:
    for key in ("name", "gene_symbol", "primary_protein_name", "organism_name"):
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _node_public_id(properties: dict[str, Any], fallback: str) -> str:
    for key in ("id", "gene_symbol", "name"):
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _collect_wrapped_graph_values(
    value: Any,
    *,
    node_wrappers: list[dict[str, Any]],
    rel_wrappers: list[dict[str, Any]],
) -> None:
    if _is_neo4j_node(value) or _is_neo4j_relationship(value) or _is_neo4j_path(value):
        _collect_wrapped_graph_values(
            _coerce_neo4j_value(value),
            node_wrappers=node_wrappers,
            rel_wrappers=rel_wrappers,
        )
        return

    if isinstance(value, dict):
        kind = str(value.get("__kind__", "")).strip().lower()
        if kind == "node":
            node_wrappers.append(value)
        elif kind == "relationship":
            rel_wrappers.append(value)

        for nested in value.values():
            _collect_wrapped_graph_values(nested, node_wrappers=node_wrappers, rel_wrappers=rel_wrappers)
    elif isinstance(value, list):
        for item in value:
            _collect_wrapped_graph_values(item, node_wrappers=node_wrappers, rel_wrappers=rel_wrappers)


def _label_from_alias(alias: str) -> str:
    parts = [part for part in alias.split("_") if part]
    if not parts:
        return "Unknown"
    return "".join(part[:1].upper() + part[1:] for part in parts)


def _singularize_alias(alias: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", str(alias or "").strip().lower()).strip("_")
    if not text:
        return "unknown"
    if text.endswith("ies") and len(text) > 3:
        return text[:-3] + "y"
    if text.endswith("ses") and len(text) > 3:
        return text[:-2]
    if text.endswith("s") and not text.endswith("ss") and len(text) > 1:
        return text[:-1]
    return text


def _slugify_token(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return text or "unknown"


_SCALAR_ENTITY_ALIASES = {
    "drug",
    "compound",
    "target",
    "protein",
    "gene",
    "disease",
    "phenotype",
    "pathway",
    "process",
    "trial",
    "intervention",
    "treatment",
    "organism",
}

_LIST_ENTITY_ALIASES = {
    "pathway",
    "process",
    "phenotype",
    "disease",
    "gene",
    "protein",
    "target",
    "mechanism",
}


_ID_PREFIX_ALIAS_HINTS: tuple[tuple[str, str], ...] = (
    ("drugbank:", "drug"),
    ("chembl.compound:", "drug"),
    ("chembl:", "drug"),
    ("pubchem.compound:", "compound"),
    ("chebi:", "compound"),
    ("uniprot:", "protein"),
    ("ensembl:", "gene"),
    ("entrez:", "gene"),
    ("ncbigene:", "gene"),
    ("mondo:", "disease"),
    ("doid:", "disease"),
    ("mesh:", "disease"),
    ("efo:", "disease"),
)


_NAME_FIELD_PRIORITY: tuple[str, ...] = (
    "name",
    "primary_protein_name",
    "gene_symbol",
    "organism_name",
    "label",
    "title",
)


def _normalize_column_token(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip().lower()).strip("_")


def _split_qualified_column_key(key: str) -> tuple[str, str] | None:
    text = str(key or "").strip()
    if "." not in text:
        return None
    left, right = text.split(".", 1)
    alias = _singularize_alias(left)
    field = _normalize_column_token(right)
    if not alias or not field:
        return None
    return alias, field


def _alias_hint_from_node_id(node_id: str) -> str | None:
    lowered = str(node_id or "").strip().lower()
    if not lowered:
        return None
    for prefix, alias in _ID_PREFIX_ALIAS_HINTS:
        if lowered.startswith(prefix):
            return alias
    return None


def _canonical_scalar_alias(raw_alias: str, node_id: str) -> str:
    alias = _singularize_alias(raw_alias)
    if not alias:
        return alias
    if alias in _SCALAR_ENTITY_ALIASES:
        return alias
    hint = _alias_hint_from_node_id(node_id)
    if hint:
        return hint
    return alias


def _infer_alias_pair(text: str, aliases: set[str]) -> tuple[str, str] | None:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return None

    ordered_aliases = sorted(
        [alias.strip().lower() for alias in aliases if str(alias).strip()],
        key=len,
        reverse=True,
    )
    for left_alias in ordered_aliases:
        prefix = f"{left_alias}_"
        if not normalized.startswith(prefix):
            continue
        remainder = normalized[len(prefix) :]
        for right_alias in ordered_aliases:
            suffix = f"_{right_alias}"
            if remainder.endswith(suffix) and len(remainder) > len(suffix):
                return left_alias, right_alias
    return None


def _collect_inferred_graph_values_from_flat_row(
    row: dict[str, Any],
    *,
    node_wrappers: list[dict[str, Any]],
    rel_wrappers: list[dict[str, Any]],
) -> None:
    alias_to_node_id: dict[str, str] = {}
    alias_variants: dict[str, set[str]] = {}
    alias_variant_to_alias: dict[str, str] = {}
    alias_field_values: dict[str, dict[str, Any]] = {}
    relation_field_values: dict[str, dict[str, Any]] = {}
    alias_to_node_element_id: dict[str, str] = {}
    alias_to_node_wrapper: dict[str, dict[str, Any]] = {}
    alias_name_is_explicit: dict[str, bool] = {}
    alias_order: list[str] = []

    def register_node_alias(raw_alias: str, node_id: str) -> str:
        normalized_raw_alias = _singularize_alias(raw_alias)
        if not normalized_raw_alias:
            return ""
        canonical_alias = _canonical_scalar_alias(normalized_raw_alias, node_id)
        if not canonical_alias:
            return ""
        alias_variant_to_alias[normalized_raw_alias] = canonical_alias
        alias_variants.setdefault(canonical_alias, set()).add(normalized_raw_alias)
        if canonical_alias not in alias_to_node_id:
            alias_to_node_id[canonical_alias] = node_id
            alias_order.append(canonical_alias)
        return canonical_alias

    # Pass 1: infer node aliases from *_id and alias.id columns.
    for raw_key, raw_value in row.items():
        if not isinstance(raw_value, str):
            continue
        node_id = raw_value.strip()
        if not node_id:
            continue

        key = str(raw_key).strip()
        raw_alias = ""
        if key.endswith("_id"):
            raw_alias = key[:-3].strip()
        else:
            qualified = _split_qualified_column_key(key)
            if qualified:
                dotted_alias, field = qualified
                if field == "id":
                    raw_alias = dotted_alias
        if not raw_alias:
            continue

        canonical_alias = register_node_alias(raw_alias, node_id)
        if not canonical_alias:
            continue
        alias_field_values.setdefault(canonical_alias, {})["id"] = node_id

    # Pass 2: collect scoped fields for known node aliases and relation aliases.
    for raw_key, raw_value in row.items():
        key = str(raw_key).strip()
        if key.endswith("_relationship"):
            continue

        if key.endswith("_name") and isinstance(raw_value, str) and raw_value.strip():
            raw_alias = _singularize_alias(key[:-5].strip())
            canonical_alias = alias_variant_to_alias.get(raw_alias)
            if canonical_alias:
                alias_field_values.setdefault(canonical_alias, {})["name"] = raw_value.strip()
            continue

        qualified = _split_qualified_column_key(key)
        if not qualified:
            continue
        raw_alias, field = qualified
        canonical_alias = alias_variant_to_alias.get(raw_alias)
        if canonical_alias:
            alias_field_values.setdefault(canonical_alias, {})[field] = raw_value
            continue

        if isinstance(raw_value, dict):
            continue
        relation_field_values.setdefault(raw_alias, {})[field] = raw_value

    # Build node wrappers inferred from node aliases.
    for alias in alias_order:
        node_id = str(alias_to_node_id.get(alias, "")).strip()
        if not node_id:
            continue

        fields = alias_field_values.get(alias, {})
        node_name = ""
        for field in _NAME_FIELD_PRIORITY:
            value = fields.get(field)
            if isinstance(value, str) and value.strip():
                node_name = value.strip()
                break
        explicit_name = bool(node_name)
        if not node_name:
            node_name = node_id

        element_id = f"inferred:{alias}:{node_id}"
        alias_to_node_element_id[alias] = element_id
        alias_name_is_explicit[alias] = explicit_name
        node_wrapper = {
            "__kind__": "node",
            "__element_id__": element_id,
            "__labels__": [_label_from_alias(alias)],
            "id": node_id,
            "name": node_name,
        }
        alias_to_node_wrapper[alias] = node_wrapper
        node_wrappers.append(node_wrapper)

    # If one node still has only ID as its label, use a single unscoped "name" column.
    unscoped_name = row.get("name")
    if isinstance(unscoped_name, str) and unscoped_name.strip():
        candidate_aliases = [alias for alias in alias_order if not alias_name_is_explicit.get(alias, False)]
        if len(candidate_aliases) == 1:
            alias_to_node_wrapper[candidate_aliases[0]]["name"] = unscoped_name.strip()

    # Projection-style rows often return named columns (e.g., Drug/Target/Pathways)
    # without *_id fields. Infer node wrappers from scalar/list columns as fallback.
    if not alias_to_node_element_id:
        for raw_key, raw_value in row.items():
            key = str(raw_key).strip()
            if key.endswith("_relationship"):
                continue
            if not isinstance(raw_value, str):
                continue
            value = raw_value.strip()
            if not value:
                continue

            alias = _singularize_alias(key)
            if alias not in _SCALAR_ENTITY_ALIASES:
                continue
            element_id = f"inferred:{alias}:{value}"
            if alias not in alias_to_node_element_id:
                alias_to_node_element_id[alias] = element_id
                alias_order.append(alias)
            node_wrappers.append(
                {
                    "__kind__": "node",
                    "__element_id__": element_id,
                    "__labels__": [_label_from_alias(alias)],
                    "id": value,
                    "name": value,
                }
            )

    aliases = set(alias_to_node_element_id.keys())
    rel_count_before = len(rel_wrappers)

    # Existing explicit relationship payloads (e.g., drug_targets_protein_relationship).
    for raw_key, raw_value in row.items():
        key = str(raw_key).strip()
        if not key.endswith("_relationship") or not isinstance(raw_value, dict):
            continue

        relation_properties = {str(k): v for k, v in raw_value.items()}
        relation_type = (
            str(relation_properties.get("relationship_type") or key[:-13] or "RELATED_TO").strip() or "RELATED_TO"
        )
        alias_pair = _infer_alias_pair(key[:-13], aliases) or _infer_alias_pair(relation_type, aliases)
        if alias_pair is None:
            continue
        source_alias, target_alias = alias_pair
        source_element_id = alias_to_node_element_id.get(source_alias)
        target_element_id = alias_to_node_element_id.get(target_alias)
        if not source_element_id or not target_element_id:
            continue

        relation_element_id = str(
            relation_properties.get("relationship_id")
            or relation_properties.get("id")
            or f"inferred:{key}:{source_element_id}->{target_element_id}"
        ).strip()
        rel_wrapper = {
            "__kind__": "relationship",
            "__element_id__": relation_element_id,
            "__type__": relation_type,
            "__start_element_id__": source_element_id,
            "__end_element_id__": target_element_id,
        }
        rel_wrapper.update(relation_properties)
        rel_wrappers.append(rel_wrapper)

    # Dotted relationship properties (e.g., r.confidence_score, r.source).
    if len(alias_order) >= 2:
        source_alias = alias_order[0]
        target_alias = alias_order[1]
        source_element_id = alias_to_node_element_id.get(source_alias, "")
        target_element_id = alias_to_node_element_id.get(target_alias, "")
        if source_element_id and target_element_id:
            fallback_rel_type = f"{_label_from_alias(source_alias)}_related_to_{_label_from_alias(target_alias)}"
            for rel_alias, fields in relation_field_values.items():
                if not fields:
                    continue
                relation_type = str(fields.get("relationship_type") or fields.get("type") or fallback_rel_type).strip()
                relation_type = relation_type or fallback_rel_type
                relation_element_id = str(
                    fields.get("relationship_id")
                    or fields.get("id")
                    or f"inferred:{rel_alias}:{source_element_id}->{target_element_id}"
                ).strip()
                rel_wrapper = {
                    "__kind__": "relationship",
                    "__element_id__": relation_element_id,
                    "__type__": relation_type,
                    "__start_element_id__": source_element_id,
                    "__end_element_id__": target_element_id,
                }
                rel_wrapper.update(fields)
                rel_wrappers.append(rel_wrapper)

    # If explicit relationship wrappers are absent, infer at least one link
    # between the first two scalar entities so subgraph/stats are non-empty.
    if len(rel_wrappers) == rel_count_before and len(alias_order) >= 2:
        source_alias = alias_order[0]
        target_alias = alias_order[1]
        source_element_id = alias_to_node_element_id[source_alias]
        target_element_id = alias_to_node_element_id[target_alias]
        rel_wrappers.append(
            {
                "__kind__": "relationship",
                "__element_id__": f"inferred:{source_alias}-to-{target_alias}:{source_element_id}->{target_element_id}",
                "__type__": f"{_label_from_alias(source_alias)}_related_to_{_label_from_alias(target_alias)}",
                "__start_element_id__": source_element_id,
                "__end_element_id__": target_element_id,
            }
        )

    # Attach list-valued columns (e.g., Pathways, Processes) as neighboring nodes.
    if aliases:
        anchor_alias = ""
        for preferred in ("target", "protein", "gene", "disease", "drug", "compound"):
            if preferred in alias_to_node_element_id:
                anchor_alias = preferred
                break
        if not anchor_alias and alias_order:
            anchor_alias = alias_order[0]

        if anchor_alias:
            anchor_element_id = alias_to_node_element_id.get(anchor_alias, "")
            for raw_key, raw_value in row.items():
                if not isinstance(raw_value, list):
                    continue
                normalized_values = [str(item).strip() for item in raw_value if isinstance(item, str) and str(item).strip()]
                if not normalized_values:
                    continue

                field_alias = _singularize_alias(str(raw_key).strip())
                if field_alias not in _LIST_ENTITY_ALIASES:
                    continue
                field_label = _label_from_alias(field_alias)
                rel_type = f"{_label_from_alias(anchor_alias)}_has_{field_label}"
                seen_values: set[str] = set()
                for item_value in normalized_values:
                    lowered = item_value.lower()
                    if lowered in seen_values:
                        continue
                    seen_values.add(lowered)

                    item_element_id = f"inferred:{field_alias}:{item_value}"
                    node_wrappers.append(
                        {
                            "__kind__": "node",
                            "__element_id__": item_element_id,
                            "__labels__": [field_label],
                            "id": item_value,
                            "name": item_value,
                        }
                    )
                    rel_wrappers.append(
                        {
                            "__kind__": "relationship",
                            "__element_id__": (
                                f"inferred:{anchor_alias}-to-{field_alias}:{_slugify_token(item_value)}:"
                                f"{anchor_element_id}->{item_element_id}"
                            ),
                            "__type__": rel_type,
                            "__start_element_id__": anchor_element_id,
                            "__end_element_id__": item_element_id,
                        }
                    )


def _upsert_node(
    node_wrapper: dict[str, Any],
    *,
    nodes_by_key: dict[str, dict[str, Any]],
    element_to_key: dict[str, str],
) -> str:
    element_id = str(node_wrapper.get("__element_id__", "")).strip()
    labels_raw = node_wrapper.get("__labels__", [])
    labels = [str(label) for label in labels_raw] if isinstance(labels_raw, list) else []
    properties = _clean_wrapped_properties(node_wrapper)

    node_type = _node_type_from_labels(labels)
    fallback_id = element_id or f"{node_type}:unknown"
    public_id = _node_public_id(properties, fallback=fallback_id)

    key = element_id if element_id else f"{node_type}:{public_id}"
    if key in nodes_by_key:
        existing = nodes_by_key[key]
        merged_labels = sorted(set(existing.get("labels", [])) | set(labels))
        existing["labels"] = merged_labels
        if existing.get("node_type") == "Unknown" and node_type != "Unknown":
            existing["node_type"] = node_type
        if existing.get("id", "").endswith(":unknown") and public_id:
            existing["id"] = public_id
        if not existing.get("name"):
            existing["name"] = _node_name(properties, fallback=public_id)
    else:
        nodes_by_key[key] = {
            "key": key,
            "id": public_id,
            "name": _node_name(properties, fallback=public_id),
            "node_type": node_type,
            "labels": labels,
        }

    if element_id:
        element_to_key[element_id] = key
    return key


def _ensure_placeholder_node(
    element_id: str,
    *,
    nodes_by_key: dict[str, dict[str, Any]],
    element_to_key: dict[str, str],
) -> str:
    clean_id = element_id.strip()
    if clean_id in element_to_key:
        return element_to_key[clean_id]
    key = clean_id or "unknown-node"
    if key not in nodes_by_key:
        nodes_by_key[key] = {
            "key": key,
            "id": key,
            "name": key,
            "node_type": "Unknown",
            "labels": [],
        }
    if clean_id:
        element_to_key[clean_id] = key
    return key


def _extract_subgraph_from_records(records: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    node_wrappers: list[dict[str, Any]] = []
    rel_wrappers: list[dict[str, Any]] = []
    for row in records:
        node_count_before = len(node_wrappers)
        rel_count_before = len(rel_wrappers)
        _collect_wrapped_graph_values(row, node_wrappers=node_wrappers, rel_wrappers=rel_wrappers)
        if len(node_wrappers) == node_count_before and len(rel_wrappers) == rel_count_before:
            _collect_inferred_graph_values_from_flat_row(row, node_wrappers=node_wrappers, rel_wrappers=rel_wrappers)

    nodes_by_key: dict[str, dict[str, Any]] = {}
    element_to_key: dict[str, str] = {}
    for node_wrapper in node_wrappers:
        _upsert_node(node_wrapper, nodes_by_key=nodes_by_key, element_to_key=element_to_key)

    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str, str]] = set()

    for rel_wrapper in rel_wrappers:
        rel_type = str(rel_wrapper.get("__type__", "RELATED_TO")).strip() or "RELATED_TO"
        rel_id = str(rel_wrapper.get("__element_id__", "")).strip()
        start_element_id = str(rel_wrapper.get("__start_element_id__", "")).strip()
        end_element_id = str(rel_wrapper.get("__end_element_id__", "")).strip()
        if not start_element_id or not end_element_id:
            continue

        source_key = _ensure_placeholder_node(start_element_id, nodes_by_key=nodes_by_key, element_to_key=element_to_key)
        target_key = _ensure_placeholder_node(end_element_id, nodes_by_key=nodes_by_key, element_to_key=element_to_key)
        edge_key = (source_key, target_key, rel_type, rel_id)
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)

        rel_properties = _clean_wrapped_properties(rel_wrapper)
        weight, confidence_key = _edge_weight(rel_properties)
        edges.append(
            {
                "source": source_key,
                "target": target_key,
                "type": rel_type,
                "weight": float(weight),
                "confidence_key": confidence_key,
            }
        )

    return nodes_by_key, edges


def _build_canonical_subgraph(records: list[dict[str, Any]]) -> dict[str, Any]:
    nodes_by_key, edges = _extract_subgraph_from_records(records)
    nodes = [
        {
            "key": node["key"],
            "id": node["id"],
            "name": node["name"],
            "node_type": node["node_type"],
            "labels": node["labels"],
        }
        for node in sorted(nodes_by_key.values(), key=lambda row: str(row.get("key", "")))
    ]
    canonical_edges = sorted(
        [
            {
                "source": edge["source"],
                "target": edge["target"],
                "type": edge["type"],
                "weight": round(float(edge["weight"]), 6),
                "confidence_key": edge["confidence_key"],
            }
            for edge in edges
        ],
        key=lambda row: (
            str(row.get("source", "")),
            str(row.get("type", "")),
            str(row.get("target", "")),
        ),
    )
    return {
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(canonical_edges),
        },
        "nodes": nodes,
        "edges": canonical_edges,
    }


def _compute_weighted_pagerank(
    node_keys: list[str],
    *,
    adjacency: dict[str, dict[str, float]],
    out_weight_sum: dict[str, float],
    damping: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-9,
) -> dict[str, float]:
    node_count = len(node_keys)
    if node_count == 0:
        return {}

    base = 1.0 / node_count
    ranks = {node_key: base for node_key in node_keys}
    teleport = (1.0 - damping) / node_count

    for _ in range(max_iter):
        dangling = sum(ranks[node_key] for node_key in node_keys if out_weight_sum.get(node_key, 0.0) <= 0.0)
        next_ranks = {node_key: teleport + damping * dangling / node_count for node_key in node_keys}

        for source_key, targets in adjacency.items():
            source_out = out_weight_sum.get(source_key, 0.0)
            if source_out <= 0.0:
                continue
            source_rank = ranks.get(source_key, 0.0)
            if source_rank <= 0.0:
                continue
            scaled = damping * source_rank / source_out
            for target_key, edge_weight in targets.items():
                next_ranks[target_key] = next_ranks.get(target_key, 0.0) + scaled * edge_weight

        delta = sum(abs(next_ranks[node_key] - ranks[node_key]) for node_key in node_keys)
        ranks = next_ranks
        if delta < tol:
            break

    return ranks


def _min_max_normalize(values: dict[str, float], keys: list[str]) -> dict[str, float]:
    if not keys:
        return {}
    numbers = [values.get(key, 0.0) for key in keys]
    min_value = min(numbers)
    max_value = max(numbers)
    span = max_value - min_value
    if span <= 1e-12:
        constant = 1.0 if max_value > 0.0 else 0.0
        return {key: constant for key in keys}
    return {key: (values.get(key, 0.0) - min_value) / span for key in keys}


def _build_query_local_node_stats(records: list[dict[str, Any]], *, top_n_per_type: int = 5) -> dict[str, Any]:
    nodes_by_key, edges = _extract_subgraph_from_records(records)
    if not nodes_by_key:
        return {
            "summary": {
                "node_count": 0,
                "edge_count": 0,
                "node_type_count": 0,
            },
            "scoring": {
                "scope": "query_local",
                "per_node_type_ranking": True,
                "weights": {
                    "pagerank": 0.55,
                    "weighted_degree": 0.30,
                    "edge_type_diversity": 0.15,
                },
                "edge_weighting": "confidence_aware_fallback_1.0",
            },
            "by_type": [],
        }

    node_keys = sorted(nodes_by_key.keys())
    out_weight_sum = {key: 0.0 for key in node_keys}
    in_weight_sum = {key: 0.0 for key in node_keys}
    out_degree = {key: 0 for key in node_keys}
    in_degree = {key: 0 for key in node_keys}
    edge_type_sets = {key: set() for key in node_keys}
    adjacency: dict[str, dict[str, float]] = {key: {} for key in node_keys}

    for edge in edges:
        source_key = str(edge["source"])
        target_key = str(edge["target"])
        weight = float(edge.get("weight", 1.0))
        relation_type = str(edge.get("type", "RELATED_TO"))
        if source_key not in nodes_by_key or target_key not in nodes_by_key:
            continue

        out_weight_sum[source_key] += weight
        in_weight_sum[target_key] += weight
        out_degree[source_key] += 1
        in_degree[target_key] += 1
        edge_type_sets[source_key].add(relation_type)
        edge_type_sets[target_key].add(relation_type)
        adjacency[source_key][target_key] = adjacency[source_key].get(target_key, 0.0) + weight

    pagerank = _compute_weighted_pagerank(node_keys, adjacency=adjacency, out_weight_sum=out_weight_sum)
    weighted_degree = {key: out_weight_sum[key] + in_weight_sum[key] for key in node_keys}
    edge_type_diversity = {key: float(len(edge_type_sets[key])) for key in node_keys}

    by_type_keys: dict[str, list[str]] = {}
    for key, node in nodes_by_key.items():
        node_type = str(node.get("node_type", "Unknown") or "Unknown")
        by_type_keys.setdefault(node_type, []).append(key)

    by_type: list[dict[str, Any]] = []
    for node_type in sorted(by_type_keys):
        keys = by_type_keys[node_type]
        norm_pagerank = _min_max_normalize(pagerank, keys)
        norm_weighted_degree = _min_max_normalize(weighted_degree, keys)
        norm_diversity = _min_max_normalize(edge_type_diversity, keys)

        scored: list[dict[str, Any]] = []
        for key in keys:
            score = (
                0.55 * norm_pagerank.get(key, 0.0)
                + 0.30 * norm_weighted_degree.get(key, 0.0)
                + 0.15 * norm_diversity.get(key, 0.0)
            )
            node = nodes_by_key[key]
            scored.append(
                {
                    "key": key,
                    "id": node.get("id"),
                    "name": node.get("name"),
                    "labels": node.get("labels", []),
                    "importance_score": round(score, 6),
                    "metrics": {
                        "pagerank": round(pagerank.get(key, 0.0), 6),
                        "weighted_degree": round(weighted_degree.get(key, 0.0), 6),
                        "edge_type_diversity": int(edge_type_diversity.get(key, 0.0)),
                        "in_degree": int(in_degree.get(key, 0)),
                        "out_degree": int(out_degree.get(key, 0)),
                        "weighted_in_degree": round(in_weight_sum.get(key, 0.0), 6),
                        "weighted_out_degree": round(out_weight_sum.get(key, 0.0), 6),
                    },
                    "normalized": {
                        "pagerank": round(norm_pagerank.get(key, 0.0), 6),
                        "weighted_degree": round(norm_weighted_degree.get(key, 0.0), 6),
                        "edge_type_diversity": round(norm_diversity.get(key, 0.0), 6),
                    },
                }
            )

        scored.sort(
            key=lambda row: (
                -float(row["importance_score"]),
                -float(row["metrics"]["pagerank"]),
                -float(row["metrics"]["weighted_degree"]),
                str(row.get("name") or row.get("id") or row.get("key")),
            )
        )

        top_nodes = []
        for rank, row in enumerate(scored[:top_n_per_type], start=1):
            top_nodes.append(
                {
                    "rank": rank,
                    "id": row["id"],
                    "name": row["name"],
                    "labels": row["labels"],
                    "importance_score": row["importance_score"],
                    "metrics": row["metrics"],
                    "normalized": row["normalized"],
                }
            )

        by_type.append(
            {
                "node_type": node_type,
                "total_nodes": len(keys),
                "top_nodes": top_nodes,
            }
        )

    return {
        "summary": {
            "node_count": len(nodes_by_key),
            "edge_count": len(edges),
            "node_type_count": len(by_type),
        },
        "scoring": {
            "scope": "query_local",
            "per_node_type_ranking": True,
            "weights": {
                "pagerank": 0.55,
                "weighted_degree": 0.30,
                "edge_type_diversity": 0.15,
            },
            "edge_weighting": "confidence_aware_fallback_1.0",
        },
        "by_type": by_type,
    }


# ---------------------------------------------------------------------------
# Tool builder
# ---------------------------------------------------------------------------


def _require_neo4j_settings(settings: Any) -> tuple[str, str, str, str]:
    """Return (uri, user, password, database) or raise."""
    if not settings.neo4j_uri or not settings.neo4j_user or not settings.neo4j_password:
        raise ToolExecutionError(
            code="UNCONFIGURED",
            message="Neo4j credentials are not configured (NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD)",
            details={"env": ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"]},
        )
    return settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password, settings.neo4j_database


def _require_gemini_key(settings: Any) -> str:
    if not settings.gemini_api_key:
        raise ToolExecutionError(
            code="UNCONFIGURED",
            message="GEMINI_API_KEY is required for KG Cypher generation",
            details={"env": "GEMINI_API_KEY"},
        )
    return settings.gemini_api_key


def build_kg_tools(settings: Any) -> list[ToolSpec]:
    """Build the kg_query and kg_cypher_execute tool specs."""
    schema = _load_schema()
    edges = schema.get("edges", [])

    # --- kg_query handler ---------------------------------------------------
    def kg_query_handler(payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload.get("question", "")).strip()
        if not question:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'question' is required")
        top_k = int(payload.get("top_k", 25))
        if top_k < 1 or top_k > 100:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'top_k' must be between 1 and 100")
        include_node_stats = bool(payload.get("include_node_stats", True))
        top_n_per_type = int(payload.get("top_n_per_type", 5))
        if top_n_per_type < 1 or top_n_per_type > 25:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'top_n_per_type' must be between 1 and 25")

        api_key = _require_gemini_key(settings)
        uri, user, password, database = _require_neo4j_settings(settings)
        query_timeout_seconds = _query_timeout_seconds(settings)

        try:
            cypher = _generate_cypher_via_gemini(question, schema, api_key)
        except Exception as exc:
            raise ToolExecutionError(
                code="UPSTREAM_ERROR",
                message=f"Gemini Cypher generation failed: {exc}",
                retryable=True,
            ) from exc

        if edges:
            cypher = _correct_cypher(cypher, edges)
        _assert_read_only_cypher(cypher)
        _assert_generated_cypher_guardrails(cypher)

        try:
            rows = _execute_cypher(
                cypher,
                uri=uri,
                user=user,
                password=password,
                database=database,
                top_k=top_k,
                query_timeout_seconds=query_timeout_seconds,
            )
        except Exception as exc:
            raise ToolExecutionError(
                code="UPSTREAM_ERROR",
                message=f"Neo4j query execution failed: {exc}",
                retryable=True,
                details={"cypher": cypher, "query_timeout_seconds": query_timeout_seconds},
            ) from exc

        data: dict[str, Any] = {"cypher": cypher, "records": rows}
        warnings: list[str] = []
        subgraph = _build_canonical_subgraph(rows)
        data["subgraph"] = subgraph
        if include_node_stats:
            data["node_stats"] = _build_query_local_node_stats(rows, top_n_per_type=top_n_per_type)
        if rows and int(((subgraph.get("summary") or {}).get("node_count") or 0)) == 0:
            warnings.append("KG rows returned but no graph entities could be inferred for subgraph/node_stats.")

        summary = f"KG query returned {len(rows)} records" if rows else "KG query returned no results"
        return make_tool_output(
            source="crossbar_kg",
            summary=summary,
            result_kind="record_list",
            data=data,
            warnings=warnings,
        )

    # --- kg_cypher_execute handler ------------------------------------------
    def kg_cypher_execute_handler(payload: dict[str, Any]) -> dict[str, Any]:
        cypher = str(payload.get("cypher", "")).strip()
        if not cypher:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'cypher' is required")
        top_k = int(payload.get("top_k", 25))
        if top_k < 1 or top_k > 100:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'top_k' must be between 1 and 100")
        correct = payload.get("correct_directions", True)
        include_node_stats = bool(payload.get("include_node_stats", True))
        top_n_per_type = int(payload.get("top_n_per_type", 5))
        if top_n_per_type < 1 or top_n_per_type > 25:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'top_n_per_type' must be between 1 and 25")

        uri, user, password, database = _require_neo4j_settings(settings)
        query_timeout_seconds = _query_timeout_seconds(settings)

        if correct and edges:
            cypher = _correct_cypher(cypher, edges)
        _assert_read_only_cypher(cypher)

        try:
            rows = _execute_cypher(
                cypher,
                uri=uri,
                user=user,
                password=password,
                database=database,
                top_k=top_k,
                query_timeout_seconds=query_timeout_seconds,
            )
        except Exception as exc:
            raise ToolExecutionError(
                code="UPSTREAM_ERROR",
                message=f"Neo4j query execution failed: {exc}",
                retryable=True,
                details={"cypher": cypher, "query_timeout_seconds": query_timeout_seconds},
            ) from exc

        data: dict[str, Any] = {"cypher": cypher, "records": rows}
        warnings: list[str] = []
        subgraph = _build_canonical_subgraph(rows)
        data["subgraph"] = subgraph
        if include_node_stats:
            data["node_stats"] = _build_query_local_node_stats(rows, top_n_per_type=top_n_per_type)
        if rows and int(((subgraph.get("summary") or {}).get("node_count") or 0)) == 0:
            warnings.append("Cypher rows returned but no graph entities could be inferred for subgraph/node_stats.")

        summary = f"Cypher returned {len(rows)} records" if rows else "Cypher returned no results"
        return make_tool_output(
            source="crossbar_kg",
            summary=summary,
            result_kind="record_list",
            data=data,
            warnings=warnings,
        )

    # --- ToolSpec definitions -----------------------------------------------
    kg_query_spec = ToolSpec(
        name="kg_query",
        description=render_tool_description(
            purpose="Query the CROssBAR biomedical knowledge graph with a natural-language question. "
            "Translates the question into a Cypher query, validates relationship directions, "
            "and executes it against a Neo4j database. Optimized for connected node-edge-node rows "
            "that are usable for graph visualization and traversal, including graph-enrichment passes "
            "for second-degree neighborhoods.",
            when=[
                "user asks about gene-disease associations, drug targets, protein interactions, pathways, or other biomedical relationships",
                "need structured entity data from the CROssBAR knowledge graph",
                "need connected subgraph outputs (including second-degree/2-hop paths) rather than isolated entities",
                "need richer neighborhood exploration to expand graph connectivity before downstream analysis",
            ],
            avoid=[
                "questions better answered by literature search (use literature tools instead)",
                "when the user already provides a Cypher query (use kg_cypher_execute)",
            ],
            critical_args=[
                "question (str, required): the biomedical question in natural language; specify direct links, 2-hop traversal, or enrichment intent",
            ],
            returns=(
                "record_list with cypher query used and matching connected rows from the knowledge graph "
                "(node/edge tuples suitable for subgraph building)"
            ),
            fails_if=[
                "Neo4j credentials not configured",
                "Gemini API key not set",
                "generated Cypher is invalid or returns no results",
            ],
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "Natural-language biomedical question to query the knowledge graph. "
                        "If you want traversal-ready output, explicitly ask for connected or 2-hop relationships."
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of result rows (1-100, default 25). Use higher values (e.g., 25-60) for graph enrichment/traversal coverage.",
                    "default": 25,
                },
                "include_node_stats": {
                    "type": "boolean",
                    "description": "Whether to compute query-local node importance stats (default true)",
                    "default": True,
                },
                "top_n_per_type": {
                    "type": "integer",
                    "description": "How many ranked nodes to return per node type (1-25, default 5)",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 25,
                },
            },
            "required": ["question"],
        },
        handler=kg_query_handler,
        source="crossbar_kg",
    )

    kg_cypher_spec = ToolSpec(
        name="kg_cypher_execute",
        description=render_tool_description(
            purpose="Execute a raw Cypher query directly against the CROssBAR Neo4j knowledge graph. "
            "Optionally validates and corrects relationship directions before execution. "
            "Best for explicit connected path queries (including two-hop traversals and enrichment-focused neighborhood expansion).",
            when=[
                "user provides an explicit Cypher query to run",
                "need to run a follow-up or refined query after inspecting kg_query results",
                "need to control exact connected path shape for graph mapping/traversal",
                "need to deliberately expand local graph neighborhoods while preserving connectedness",
            ],
            avoid=[
                "when user asks a natural-language question (use kg_query instead)",
            ],
            critical_args=["cypher (str, required): the Cypher query to execute"],
            returns="record_list with the executed cypher and matching connected records from the knowledge graph",
            fails_if=[
                "Neo4j credentials not configured",
                "Cypher syntax is invalid",
            ],
        ),
        input_schema={
            "type": "object",
            "properties": {
                "cypher": {
                    "type": "string",
                    "description": (
                        "Cypher query to execute against the CROssBAR knowledge graph. "
                        "Prefer connected node-edge path returns (including 2-hop chains) for graph traversal use-cases."
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of result rows (1-100, default 25). Use higher values for enrichment-oriented traversal queries.",
                    "default": 25,
                },
                "correct_directions": {
                    "type": "boolean",
                    "description": "Whether to validate/correct relationship directions against the schema (default true)",
                    "default": True,
                },
                "include_node_stats": {
                    "type": "boolean",
                    "description": "Whether to compute query-local node importance stats (default true)",
                    "default": True,
                },
                "top_n_per_type": {
                    "type": "integer",
                    "description": "How many ranked nodes to return per node type (1-25, default 5)",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 25,
                },
            },
            "required": ["cypher"],
        },
        handler=kg_cypher_execute_handler,
        source="crossbar_kg",
    )

    return [kg_query_spec, kg_cypher_spec]
