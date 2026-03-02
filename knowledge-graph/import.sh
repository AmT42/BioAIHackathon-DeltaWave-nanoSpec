#!/usr/bin/env bash
# Import CROssBAR knowledge-graph CSVs into Neo4j using neo4j-admin bulk import.
# CROssBAR KG: https://github.com/HUBioDataLab/CROssBARv2-KG
#
# Prerequisites:
#   - Neo4j 5.x installed (community or enterprise)
#   - CSV data downloaded into ./data/  (see download_csv.sh)
#
# Usage:
#   ./import.sh              # uses default database name "crossbarall"
#   ./import.sh mydb         # custom database name
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${SCRIPT_DIR}/data"
DB="${1:-crossbarall}"

if [ ! -d "$DATA_DIR" ] || [ -z "$(ls "$DATA_DIR"/*.csv 2>/dev/null)" ]; then
  echo "Error: No CSV files found in $DATA_DIR"
  echo "Run ./download_csv.sh first."
  exit 1
fi

echo "=== CROssBAR Neo4j Import ==="
echo "Data dir: $DATA_DIR"
echo "Database: $DB"
echo ""

# ── Step 1: Stop Neo4j ──────────────────────────────────────────────
echo "Stopping Neo4j..."
neo4j stop 2>/dev/null || true
sleep 5
if neo4j status 2>/dev/null | grep -q "running"; then
  echo "Neo4j didn't stop gracefully, force killing..."
  NEO4J_PID=$(neo4j status 2>/dev/null | grep -oE '[0-9]+' | head -1)
  if [ -n "$NEO4J_PID" ]; then
    kill -9 "$NEO4J_PID" 2>/dev/null || true
    sleep 2
  fi
fi
echo "Neo4j stopped."
echo ""

# ── Step 2: Bulk import ─────────────────────────────────────────────
echo "Starting bulk import..."

neo4j-admin database import full \
  --verbose \
  --delimiter="\t" \
  --array-delimiter="|" \
  --quote="'" \
  --overwrite-destination=true \
  --skip-bad-relationships=true \
  --skip-duplicate-nodes=true \
  --bad-tolerance=10000000 \
  --max-off-heap-memory=2G \
  \
  --nodes="${DATA_DIR}/BiologicalProcess-header.csv,${DATA_DIR}/BiologicalProcess-part.*" \
  --nodes="${DATA_DIR}/CellularComponent-header.csv,${DATA_DIR}/CellularComponent-part.*" \
  --nodes="${DATA_DIR}/Compound-header.csv,${DATA_DIR}/Compound-part.*" \
  --nodes="${DATA_DIR}/Disease-header.csv,${DATA_DIR}/Disease-part.*" \
  --nodes="${DATA_DIR}/Drug-header.csv,${DATA_DIR}/Drug-part.*" \
  --nodes="${DATA_DIR}/EcNumber-header.csv,${DATA_DIR}/EcNumber-part.*" \
  --nodes="${DATA_DIR}/Gene-header.csv,${DATA_DIR}/Gene-part.*" \
  --nodes="${DATA_DIR}/MolecularFunction-header.csv,${DATA_DIR}/MolecularFunction-part.*" \
  --nodes="${DATA_DIR}/OrganismTaxon-header.csv,${DATA_DIR}/OrganismTaxon-part.*" \
  --nodes="${DATA_DIR}/Pathway-header.csv,${DATA_DIR}/Pathway-part.*" \
  --nodes="${DATA_DIR}/Phenotype-header.csv,${DATA_DIR}/Phenotype-part.*" \
  --nodes="${DATA_DIR}/Protein-header.csv,${DATA_DIR}/Protein-part.*" \
  --nodes="${DATA_DIR}/ProteinDomain-header.csv,${DATA_DIR}/ProteinDomain-part.*" \
  --nodes="${DATA_DIR}/SideEffect-header.csv,${DATA_DIR}/SideEffect-part.*" \
  \
  --relationships="${DATA_DIR}/Biological_process_is_a_biological_process.BiologicalProcessToBiologicalProcessAssociation-header.csv,${DATA_DIR}/Biological_process_is_a_biological_process.BiologicalProcessToBiologicalProcessAssociation-part.*" \
  --relationships="${DATA_DIR}/Biological_process_negatively_regulates_biological_process.BiologicalProcessToBiologicalProcessAssociation-header.csv,${DATA_DIR}/Biological_process_negatively_regulates_biological_process.BiologicalProcessToBiologicalProcessAssociation-part.*" \
  --relationships="${DATA_DIR}/Biological_process_negatively_regulates_molecular_function.BiologicalProcessToMolecularFunctionAssociation-header.csv,${DATA_DIR}/Biological_process_negatively_regulates_molecular_function.BiologicalProcessToMolecularFunctionAssociation-part.*" \
  --relationships="${DATA_DIR}/Biological_process_part_of_biological_process.BiologicalProcessToBiologicalProcessAssociation-header.csv,${DATA_DIR}/Biological_process_part_of_biological_process.BiologicalProcessToBiologicalProcessAssociation-part.*" \
  --relationships="${DATA_DIR}/Biological_process_positively_regulates_biological_process.BiologicalProcessToBiologicalProcessAssociation-header.csv,${DATA_DIR}/Biological_process_positively_regulates_biological_process.BiologicalProcessToBiologicalProcessAssociation-part.*" \
  --relationships="${DATA_DIR}/Biological_process_positively_regulates_molecular_function.BiologicalProcessToMolecularFunctionAssociation-header.csv,${DATA_DIR}/Biological_process_positively_regulates_molecular_function.BiologicalProcessToMolecularFunctionAssociation-part.*" \
  --relationships="${DATA_DIR}/Cellular_component_is_a_cellular_component.CellularComponentToCellularComponentAssociation-header.csv,${DATA_DIR}/Cellular_component_is_a_cellular_component.CellularComponentToCellularComponentAssociation-part.*" \
  --relationships="${DATA_DIR}/Cellular_component_part_of_cellular_component.CellularComponentToCellularComponentAssociation-header.csv,${DATA_DIR}/Cellular_component_part_of_cellular_component.CellularComponentToCellularComponentAssociation-part.*" \
  --relationships="${DATA_DIR}/Compound_targets_protein-header.csv,${DATA_DIR}/Compound_targets_protein-part.*" \
  --relationships="${DATA_DIR}/Disease_is_a_disease-header.csv,${DATA_DIR}/Disease_is_a_disease-part.*" \
  --relationships="${DATA_DIR}/Disease_is_associated_with_disease-header.csv,${DATA_DIR}/Disease_is_associated_with_disease-part.*" \
  --relationships="${DATA_DIR}/Disease_is_comorbid_with_disease-header.csv,${DATA_DIR}/Disease_is_comorbid_with_disease-part.*" \
  --relationships="${DATA_DIR}/Disease_is_treated_by_drug-header.csv,${DATA_DIR}/Disease_is_treated_by_drug-part.*" \
  --relationships="${DATA_DIR}/Disease_modulates_pathway-header.csv,${DATA_DIR}/Disease_modulates_pathway-part.*" \
  --relationships="${DATA_DIR}/Drug_downregulates_gene.DrugToGeneAssociation-header.csv,${DATA_DIR}/Drug_downregulates_gene.DrugToGeneAssociation-part.*" \
  --relationships="${DATA_DIR}/Drug_has_side_effect-header.csv,${DATA_DIR}/Drug_has_side_effect-part.*" \
  --relationships="${DATA_DIR}/Drug_has_target_in_pathway-header.csv,${DATA_DIR}/Drug_has_target_in_pathway-part.*" \
  --relationships="${DATA_DIR}/Drug_interacts_with_drug-header.csv,${DATA_DIR}/Drug_interacts_with_drug-part.*" \
  --relationships="${DATA_DIR}/Drug_targets_protein-header.csv,${DATA_DIR}/Drug_targets_protein-part.*" \
  --relationships="${DATA_DIR}/Drug_upregulates_gene.DrugToGeneAssociation-header.csv,${DATA_DIR}/Drug_upregulates_gene.DrugToGeneAssociation-part.*" \
  --relationships="${DATA_DIR}/Ec_number_is_a_ec_number-header.csv,${DATA_DIR}/Ec_number_is_a_ec_number-part.*" \
  --relationships="${DATA_DIR}/Gene_encodes_protein-header.csv,${DATA_DIR}/Gene_encodes_protein-part.*" \
  --relationships="${DATA_DIR}/Gene_is_orthologous_with_gene-header.csv,${DATA_DIR}/Gene_is_orthologous_with_gene-part.*" \
  --relationships="${DATA_DIR}/Gene_is_related_to_disease-header.csv,${DATA_DIR}/Gene_is_related_to_disease-part.*" \
  --relationships="${DATA_DIR}/Gene_regulates_gene-header.csv,${DATA_DIR}/Gene_regulates_gene-part.*" \
  --relationships="${DATA_DIR}/Molecular_function_is_a_molecular_function.MolecularFunctionToMolecularFunctionAssociation-header.csv,${DATA_DIR}/Molecular_function_is_a_molecular_function.MolecularFunctionToMolecularFunctionAssociation-part.*" \
  --relationships="${DATA_DIR}/Molecular_function_negatively_regulates_molecular_function.MolecularFunctionToMolecularFunctionAssociation-header.csv,${DATA_DIR}/Molecular_function_negatively_regulates_molecular_function.MolecularFunctionToMolecularFunctionAssociation-part.*" \
  --relationships="${DATA_DIR}/Molecular_function_part_of_molecular_function.MolecularFunctionToMolecularFunctionAssociation-header.csv,${DATA_DIR}/Molecular_function_part_of_molecular_function.MolecularFunctionToMolecularFunctionAssociation-part.*" \
  --relationships="${DATA_DIR}/Molecular_function_positively_regulates_molecular_function.MolecularFunctionToMolecularFunctionAssociation-header.csv,${DATA_DIR}/Molecular_function_positively_regulates_molecular_function.MolecularFunctionToMolecularFunctionAssociation-part.*" \
  --relationships="${DATA_DIR}/Organism_causes_disease-header.csv,${DATA_DIR}/Organism_causes_disease-part.*" \
  --relationships="${DATA_DIR}/Pathway_is_equivalent_to_pathway.PathwayToPathwayAssociation-header.csv,${DATA_DIR}/Pathway_is_equivalent_to_pathway.PathwayToPathwayAssociation-part.*" \
  --relationships="${DATA_DIR}/Pathway_is_ortholog_to_pathway-header.csv,${DATA_DIR}/Pathway_is_ortholog_to_pathway-part.*" \
  --relationships="${DATA_DIR}/Pathway_is_part_of_pathway.PathwayToPathwayAssociation-header.csv,${DATA_DIR}/Pathway_is_part_of_pathway.PathwayToPathwayAssociation-part.*" \
  --relationships="${DATA_DIR}/Pathway_participates_pathway-header.csv,${DATA_DIR}/Pathway_participates_pathway-part.*" \
  --relationships="${DATA_DIR}/Phenotype_is_a_phenotype-header.csv,${DATA_DIR}/Phenotype_is_a_phenotype-part.*" \
  --relationships="${DATA_DIR}/Phenotype_is_associated_with_disease-header.csv,${DATA_DIR}/Phenotype_is_associated_with_disease-part.*" \
  --relationships="${DATA_DIR}/Protein_belongs_to_organism-header.csv,${DATA_DIR}/Protein_belongs_to_organism-part.*" \
  --relationships="${DATA_DIR}/Protein_catalyzes_ec_number-header.csv,${DATA_DIR}/Protein_catalyzes_ec_number-part.*" \
  --relationships="${DATA_DIR}/Protein_contributes_to_molecular_function.ProteinToMolecularFunctionAssociation-header.csv,${DATA_DIR}/Protein_contributes_to_molecular_function.ProteinToMolecularFunctionAssociation-part.*" \
  --relationships="${DATA_DIR}/Protein_domain_enables_molecular_function-header.csv,${DATA_DIR}/Protein_domain_enables_molecular_function-part.*" \
  --relationships="${DATA_DIR}/Protein_domain_involved_in_biological_process-header.csv,${DATA_DIR}/Protein_domain_involved_in_biological_process-part.*" \
  --relationships="${DATA_DIR}/Protein_domain_located_in_cellular_component-header.csv,${DATA_DIR}/Protein_domain_located_in_cellular_component-part.*" \
  --relationships="${DATA_DIR}/Protein_enables_molecular_function.ProteinToMolecularFunctionAssociation-header.csv,${DATA_DIR}/Protein_enables_molecular_function.ProteinToMolecularFunctionAssociation-part.*" \
  --relationships="${DATA_DIR}/Protein_has_domain-header.csv,${DATA_DIR}/Protein_has_domain-part.*" \
  --relationships="${DATA_DIR}/Protein_interacts_with_protein-header.csv,${DATA_DIR}/Protein_interacts_with_protein-part.*" \
  --relationships="${DATA_DIR}/Protein_involved_in_biological_process-header.csv,${DATA_DIR}/Protein_involved_in_biological_process-part.*" \
  --relationships="${DATA_DIR}/Protein_is_active_in_cellular_component.ProteinToCellularComponentAssociation-header.csv,${DATA_DIR}/Protein_is_active_in_cellular_component.ProteinToCellularComponentAssociation-part.*" \
  --relationships="${DATA_DIR}/Protein_is_associated_with_phenotype-header.csv,${DATA_DIR}/Protein_is_associated_with_phenotype-part.*" \
  --relationships="${DATA_DIR}/Protein_located_in_cellular_component.ProteinToCellularComponentAssociation-header.csv,${DATA_DIR}/Protein_located_in_cellular_component.ProteinToCellularComponentAssociation-part.*" \
  --relationships="${DATA_DIR}/Protein_part_of_cellular_component.ProteinToCellularComponentAssociation-header.csv,${DATA_DIR}/Protein_part_of_cellular_component.ProteinToCellularComponentAssociation-part.*" \
  --relationships="${DATA_DIR}/Protein_take_part_in_pathway-header.csv,${DATA_DIR}/Protein_take_part_in_pathway-part.*" \
  --relationships="${DATA_DIR}/Side_effect_is_a_side_effect-header.csv,${DATA_DIR}/Side_effect_is_a_side_effect-part.*" \
  \
  -- "$DB"

echo ""
echo "Import completed!"
echo ""

# ── Step 3: Start Neo4j ─────────────────────────────────────────────
echo "Starting Neo4j..."
neo4j start

echo ""
echo "Neo4j is starting up. Once ready, set the active database:"
echo "  cypher-shell -d system \"CREATE DATABASE $DB IF NOT EXISTS;\""
echo ""
echo "Or set it as the default in neo4j.conf:"
echo "  server.default_database=$DB"
echo ""
