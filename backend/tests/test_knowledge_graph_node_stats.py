from __future__ import annotations

from app.agent.tools.sources.knowledge_graph import _build_query_local_node_stats


def _as_map_by_type(stats: dict) -> dict[str, dict]:
    return {row["node_type"]: row for row in stats["by_type"]}


def test_query_local_node_stats_rank_per_node_type() -> None:
    records = [
        {
            "protein_a": {
                "__kind__": "node",
                "__element_id__": "n1",
                "__labels__": ["Protein"],
                "id": "P001",
                "name": "AKT1",
            },
            "protein_b": {
                "__kind__": "node",
                "__element_id__": "n2",
                "__labels__": ["Protein"],
                "id": "P002",
                "name": "MTOR",
            },
            "disease_a": {
                "__kind__": "node",
                "__element_id__": "n3",
                "__labels__": ["Disease"],
                "id": "D001",
                "name": "Metabolic Disease",
            },
            "disease_b": {
                "__kind__": "node",
                "__element_id__": "n4",
                "__labels__": ["Disease"],
                "id": "D002",
                "name": "Cancer",
            },
            "r1": {
                "__kind__": "relationship",
                "__element_id__": "r1",
                "__type__": "Protein_interacts_with_protein",
                "__start_element_id__": "n1",
                "__end_element_id__": "n2",
                "confidence_score": 1.0,
            },
            "r2": {
                "__kind__": "relationship",
                "__element_id__": "r2",
                "__type__": "Protein_interacts_with_protein",
                "__start_element_id__": "n2",
                "__end_element_id__": "n1",
                "confidence_score": 0.1,
            },
            "r3": {
                "__kind__": "relationship",
                "__element_id__": "r3",
                "__type__": "Gene_is_related_to_disease",
                "__start_element_id__": "n1",
                "__end_element_id__": "n3",
                "opentargets_score": 0.9,
            },
            "r4": {
                "__kind__": "relationship",
                "__element_id__": "r4",
                "__type__": "Gene_is_related_to_disease",
                "__start_element_id__": "n2",
                "__end_element_id__": "n4",
                "opentargets_score": 0.2,
            },
        }
    ]

    stats = _build_query_local_node_stats(records, top_n_per_type=2)
    by_type = _as_map_by_type(stats)

    assert stats["summary"]["node_count"] == 4
    assert stats["summary"]["edge_count"] == 4
    assert set(by_type) == {"Disease", "Protein"}

    top_protein = by_type["Protein"]["top_nodes"][0]
    top_disease = by_type["Disease"]["top_nodes"][0]

    assert top_protein["name"] == "MTOR"
    assert top_disease["name"] == "Cancer"

    protein_rows = {row["name"]: row for row in by_type["Protein"]["top_nodes"]}
    assert protein_rows["AKT1"]["metrics"]["weighted_degree"] > protein_rows["MTOR"]["metrics"]["weighted_degree"]
    assert top_protein["importance_score"] >= by_type["Protein"]["top_nodes"][1]["importance_score"]
    assert top_disease["importance_score"] >= by_type["Disease"]["top_nodes"][1]["importance_score"]


def test_query_local_node_stats_returns_empty_shape_without_wrapped_graph_values() -> None:
    stats = _build_query_local_node_stats(records=[{"a": 1, "b": "x"}], top_n_per_type=5)
    assert stats["summary"]["node_count"] == 0
    assert stats["summary"]["edge_count"] == 0
    assert stats["by_type"] == []
