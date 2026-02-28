from __future__ import annotations

from app.agent.tools.sources.knowledge_graph import _build_canonical_subgraph, _build_query_local_node_stats


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


def test_canonical_subgraph_shape_defaults_when_no_graph_values() -> None:
    subgraph = _build_canonical_subgraph(records=[{"x": 1, "y": "z"}])
    assert subgraph["summary"] == {"node_count": 0, "edge_count": 0}
    assert subgraph["nodes"] == []
    assert subgraph["edges"] == []


def test_canonical_subgraph_contains_nodes_and_edges() -> None:
    records = [
        {
            "d": {
                "__kind__": "node",
                "__element_id__": "n-drug",
                "__labels__": ["Drug"],
                "id": "DB001",
                "name": "Sirolimus",
            },
            "p": {
                "__kind__": "node",
                "__element_id__": "n-protein",
                "__labels__": ["Protein"],
                "id": "P12345",
                "name": "MTOR",
            },
            "r": {
                "__kind__": "relationship",
                "__element_id__": "r1",
                "__type__": "Drug_targets_protein",
                "__start_element_id__": "n-drug",
                "__end_element_id__": "n-protein",
                "confidence_score": 0.8,
            },
        }
    ]
    subgraph = _build_canonical_subgraph(records)
    assert subgraph["summary"] == {"node_count": 2, "edge_count": 1}
    assert {row["node_type"] for row in subgraph["nodes"]} == {"Drug", "Protein"}
    edge = subgraph["edges"][0]
    assert edge["source"] == "n-drug"
    assert edge["target"] == "n-protein"
    assert edge["type"] == "Drug_targets_protein"
    assert edge["weight"] > 1.0


def test_flat_records_infer_subgraph_and_node_stats() -> None:
    records = [
        {
            "drug_id": "drugbank:DB01050",
            "drug_name": "Ibuprofen",
            "protein_id": "uniprot:P08183",
            "protein_name": "ATP-dependent translocase ABCB1",
            "drug_targets_protein_relationship": {
                "relationship_type": "Drug_targets_protein",
                "confidence_score": 0.6,
                "source": ["DGIdb"],
            },
        }
    ]

    subgraph = _build_canonical_subgraph(records)
    assert subgraph["summary"] == {"node_count": 2, "edge_count": 1}
    assert {row["node_type"] for row in subgraph["nodes"]} == {"Drug", "Protein"}

    stats = _build_query_local_node_stats(records, top_n_per_type=5)
    assert stats["summary"]["node_count"] == 2
    assert stats["summary"]["edge_count"] == 1
    assert stats["summary"]["node_type_count"] == 2
    assert {row["node_type"] for row in stats["by_type"]} == {"Drug", "Protein"}


def test_dotted_flat_records_infer_subgraph_and_node_stats() -> None:
    records = [
        {
            "d.id": "drugbank:DB00877",
            "d.name": "Sirolimus",
            "p.id": "uniprot:Q9JLN9",
            "name": "Serine/threonine-protein kinase mTOR",
            "r.confidence_score": 0.9,
            "r.activity_type": "IC50",
        }
    ]

    subgraph = _build_canonical_subgraph(records)
    assert subgraph["summary"] == {"node_count": 2, "edge_count": 1}
    assert {row["node_type"] for row in subgraph["nodes"]} == {"Drug", "Protein"}
    names = {row["name"] for row in subgraph["nodes"]}
    assert "Sirolimus" in names
    assert "Serine/threonine-protein kinase mTOR" in names
    assert subgraph["edges"][0]["weight"] > 1.0

    stats = _build_query_local_node_stats(records, top_n_per_type=5)
    assert stats["summary"]["node_count"] == 2
    assert stats["summary"]["edge_count"] == 1
    assert stats["summary"]["node_type_count"] == 2
    assert {row["node_type"] for row in stats["by_type"]} == {"Drug", "Protein"}


def test_projection_rows_infer_subgraph_and_node_stats() -> None:
    records = [
        {
            "Drug": "Sirolimus",
            "Target": "Serine/threonine-protein kinase mTOR",
            "Processes": [
                "positive regulation of cellular senescence",
                "regulation of autophagy",
            ],
            "Pathways": [
                "mTORC1-mediated signalling",
                "Autophagy",
            ],
        }
    ]

    subgraph = _build_canonical_subgraph(records)
    assert subgraph["summary"]["node_count"] >= 4
    assert subgraph["summary"]["edge_count"] >= 3
    assert any(row["node_type"] == "Drug" for row in subgraph["nodes"])
    assert any(row["node_type"] == "Target" for row in subgraph["nodes"])
    assert any(row["node_type"] == "Pathway" for row in subgraph["nodes"])
    assert any(row["node_type"] == "Process" for row in subgraph["nodes"])

    stats = _build_query_local_node_stats(records, top_n_per_type=5)
    assert stats["summary"]["node_count"] >= 4
    assert stats["summary"]["edge_count"] >= 3
    assert stats["summary"]["node_type_count"] >= 4
    assert any(row["node_type"] == "Target" for row in stats["by_type"])


def test_function_projection_rows_infer_relation_type_and_labels() -> None:
    records = [
        {
            "n.name": "Nicotinamide Mononucleotide",
            "labels(n)": ["SmallMolecule", "Drug"],
            "type(r)": "Drug_targets_protein",
            "m.name": "Nicotinamide phosphoribosyltransferase",
            "labels(m)": ["Protein"],
        }
    ]

    subgraph = _build_canonical_subgraph(records)
    assert subgraph["summary"] == {"node_count": 2, "edge_count": 1}
    assert {row["node_type"] for row in subgraph["nodes"]} == {"Drug", "Protein"}
    edge = subgraph["edges"][0]
    assert edge["type"] == "Drug_targets_protein"

    stats = _build_query_local_node_stats(records, top_n_per_type=5)
    assert stats["summary"]["node_count"] == 2
    assert stats["summary"]["edge_count"] == 1
    assert stats["summary"]["node_type_count"] == 2


def test_projection_rows_preserve_edge_type_from_rel_column_and_target_labels() -> None:
    records = [
        {
            "compound": "Nicotinamide riboside",
            "rel": "Disease_is_treated_by_drug",
            "target": "heart failure",
            "label": ["Disease"],
        }
    ]

    subgraph = _build_canonical_subgraph(records)
    assert subgraph["summary"] == {"node_count": 2, "edge_count": 1}
    assert {row["node_type"] for row in subgraph["nodes"]} == {"Compound", "Disease"}
    edge = subgraph["edges"][0]
    assert edge["type"] == "Disease_is_treated_by_drug"

    stats = _build_query_local_node_stats(records, top_n_per_type=5)
    assert stats["summary"]["node_count"] == 2
    assert stats["summary"]["edge_count"] == 1


def test_suffix_projection_rows_infer_subgraph_for_n_m_r_aliases() -> None:
    records = [
        {
            "n_labels": ["BiologicalProcess", "GOTerm"],
            "n_name": "nicotinamide riboside catabolic process",
            "r_type": "Protein_involved_in_biological_process",
            "m_labels": ["Protein"],
            "m_name": "Nicotinamide phosphoribosyltransferase",
        }
    ]

    subgraph = _build_canonical_subgraph(records)
    assert subgraph["summary"] == {"node_count": 2, "edge_count": 1}
    assert {row["node_type"] for row in subgraph["nodes"]} == {"BiologicalProcess", "Protein"}
    edge = subgraph["edges"][0]
    assert edge["type"] == "Protein_involved_in_biological_process"

    stats = _build_query_local_node_stats(records, top_n_per_type=5)
    assert stats["summary"]["node_count"] == 2
    assert stats["summary"]["edge_count"] == 1
