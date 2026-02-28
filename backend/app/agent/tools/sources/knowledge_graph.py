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


def _strip_embedding_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_embedding_fields(v) for k, v in value.items() if "embedding" not in k.lower()}
    if isinstance(value, list):
        return [_strip_embedding_fields(v) for v in value]
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
) -> list[dict[str, Any]]:
    import neo4j  # lazy

    query_with_limit = _apply_limit(query.strip(), top_k=top_k)
    with neo4j.GraphDatabase.driver(uri, auth=(user, password)) as driver:
        records, _, _ = driver.execute_query(query_with_limit, database_=database, routing_="r")

    if not records:
        return []
    return [_strip_embedding_fields(record.data()) for record in records]


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

        api_key = _require_gemini_key(settings)
        uri, user, password, database = _require_neo4j_settings(settings)

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

        try:
            rows = _execute_cypher(cypher, uri=uri, user=user, password=password, database=database, top_k=top_k)
        except Exception as exc:
            raise ToolExecutionError(
                code="UPSTREAM_ERROR",
                message=f"Neo4j query execution failed: {exc}",
                retryable=True,
                details={"cypher": cypher},
            ) from exc

        summary = f"KG query returned {len(rows)} records" if rows else "KG query returned no results"
        return make_tool_output(
            source="crossbar_kg",
            summary=summary,
            result_kind="record_list",
            data={"cypher": cypher, "records": rows},
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

        uri, user, password, database = _require_neo4j_settings(settings)

        if correct and edges:
            cypher = _correct_cypher(cypher, edges)

        try:
            rows = _execute_cypher(cypher, uri=uri, user=user, password=password, database=database, top_k=top_k)
        except Exception as exc:
            raise ToolExecutionError(
                code="UPSTREAM_ERROR",
                message=f"Neo4j query execution failed: {exc}",
                retryable=True,
                details={"cypher": cypher},
            ) from exc

        summary = f"Cypher returned {len(rows)} records" if rows else "Cypher returned no results"
        return make_tool_output(
            source="crossbar_kg",
            summary=summary,
            result_kind="record_list",
            data={"cypher": cypher, "records": rows},
        )

    # --- ToolSpec definitions -----------------------------------------------
    kg_query_spec = ToolSpec(
        name="kg_query",
        description=render_tool_description(
            purpose="Query the CROssBAR biomedical knowledge graph with a natural-language question. "
            "Translates the question into a Cypher query, validates relationship directions, "
            "and executes it against a Neo4j database.",
            when=[
                "user asks about gene-disease associations, drug targets, protein interactions, pathways, or other biomedical relationships",
                "need structured entity data from the CROssBAR knowledge graph",
            ],
            avoid=[
                "questions better answered by literature search (use literature tools instead)",
                "when the user already provides a Cypher query (use kg_cypher_execute)",
            ],
            critical_args=["question (str, required): the biomedical question in natural language"],
            returns="record_list with cypher query used and matching records from the knowledge graph",
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
                    "description": "Natural-language biomedical question to query the knowledge graph",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of result rows (1-100, default 25)",
                    "default": 25,
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
            "Optionally validates and corrects relationship directions before execution.",
            when=[
                "user provides an explicit Cypher query to run",
                "need to run a follow-up or refined query after inspecting kg_query results",
            ],
            avoid=[
                "when user asks a natural-language question (use kg_query instead)",
            ],
            critical_args=["cypher (str, required): the Cypher query to execute"],
            returns="record_list with the executed cypher and matching records",
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
                    "description": "Cypher query to execute against the CROssBAR knowledge graph",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of result rows (1-100, default 25)",
                    "default": 25,
                },
                "correct_directions": {
                    "type": "boolean",
                    "description": "Whether to validate/correct relationship directions against the schema (default true)",
                    "default": True,
                },
            },
            "required": ["cypher"],
        },
        handler=kg_cypher_execute_handler,
        source="crossbar_kg",
    )

    return [kg_query_spec, kg_cypher_spec]
