from __future__ import annotations

import pytest

from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.sources.knowledge_graph import (
    _assert_generated_cypher_guardrails,
    _assert_read_only_cypher,
    _estimate_return_column_count,
    _generated_cypher_guardrail_violations,
)


def test_estimate_return_column_count_ignores_nested_commas() -> None:
    query = (
        "MATCH (d:Drug) "
        "RETURN d.id AS drug_id, coalesce(d.name, 'x,y') AS drug_name, "
        "[x IN [1,2,3] | x] AS nums"
    )
    assert _estimate_return_column_count(query) == 3


def test_generated_guardrails_allow_concise_query() -> None:
    query = (
        "MATCH (d:Drug)-[r:Drug_targets_protein]->(p:Protein) "
        "WHERE toLower(d.name) CONTAINS toLower('sirolimus') "
        "RETURN d.id AS drug_id, d.name AS drug_name, p.id AS protein_id, p.primary_protein_name AS protein_name"
    )
    assert _generated_cypher_guardrail_violations(query) == []


def test_generated_guardrails_flag_overwide_return_and_variable_length() -> None:
    many_columns = ", ".join(f"d.prop{i} AS p{i}" for i in range(30))
    query = f"MATCH (d:Drug)-[:Drug_targets_protein*1..3]->(p:Protein) RETURN {many_columns}"
    issues = _generated_cypher_guardrail_violations(query)
    assert any("too many RETURN columns" in issue for issue in issues)
    assert any("variable-length path pattern" in issue for issue in issues)


def test_assert_generated_cypher_guardrails_rejects_complex_query() -> None:
    with pytest.raises(ToolExecutionError):
        _assert_generated_cypher_guardrails("MATCH (n) WITH n MATCH (m) WITH m MATCH (k) WITH k MATCH (x) RETURN x")


def test_assert_read_only_cypher_rejects_write_clause() -> None:
    with pytest.raises(ToolExecutionError):
        _assert_read_only_cypher("MATCH (d:Drug {id:'drugbank:DB00877'}) MERGE (x:Tmp {id:'1'}) RETURN x")

