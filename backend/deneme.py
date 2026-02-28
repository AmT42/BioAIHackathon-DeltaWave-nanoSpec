import json
from app.config import get_settings
from app.agent.tools.science_registry import create_science_registry

registry = create_science_registry(get_settings())

cypher = """
MATCH (d:Drug)-[r:Drug_targets_protein]->(p:Protein)
WHERE toLower(d.name) CONTAINS toLower('sirolimus')
RETURN d, r, p
"""

res = registry.execute("kg_cypher_execute", {
    "cypher": cypher,
    "top_k": 25,
    "include_node_stats": True,
    "top_n_per_type": 10
})

print("status:", res["status"])
print(json.dumps(res["output"]["data"]["node_stats"], indent=2))

print("tool summary:", res["output"]["summary"])
records = res["output"]["data"]["records"]
print("record_count:", len(records))
if records:
    print("first record keys:", list(records[0].keys()))