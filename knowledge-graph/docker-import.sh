#!/usr/bin/env bash
# Import CROssBAR knowledge-graph CSVs into Neo4j using Docker.
# CROssBAR KG: https://github.com/HUBioDataLab/CROssBARv2-KG
# This is the recommended method — no local Neo4j installation required.
#
# Prerequisites:
#   - Docker installed and running
#   - CSV data downloaded into ./data/  (see download_csv.sh)
#
# Usage:
#   ./docker-import.sh                  # default database "crossbarall"
#   ./docker-import.sh mydb             # custom database name
#   NEO4J_VERSION=5.26.0 ./docker-import.sh   # specific Neo4j version
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${SCRIPT_DIR}/data"
DB="${1:-crossbarall}"
NEO4J_VERSION="${NEO4J_VERSION:-5.26.0}"
CONTAINER_DATA="/import"

if [ ! -d "$DATA_DIR" ] || [ -z "$(ls "$DATA_DIR"/*.csv 2>/dev/null)" ]; then
  echo "Error: No CSV files found in $DATA_DIR"
  echo "Run ./download_csv.sh first."
  exit 1
fi

if ! command -v docker &>/dev/null; then
  echo "Error: Docker is not installed. Please install Docker first."
  exit 1
fi

echo "=== CROssBAR Neo4j Import (Docker) ==="
echo "Data dir:      $DATA_DIR"
echo "Database:      $DB"
echo "Neo4j version: $NEO4J_VERSION"
echo ""

# Build the --nodes and --relationships arguments
IMPORT_ARGS=""

# Nodes (14 types)
for label in BiologicalProcess CellularComponent Compound Disease Drug EcNumber Gene MolecularFunction OrganismTaxon Pathway Phenotype Protein ProteinDomain SideEffect; do
  IMPORT_ARGS+=" --nodes=${CONTAINER_DATA}/${label}-header.csv,${CONTAINER_DATA}/${label}-part.*"
done

# Relationships — collect from header files
while IFS= read -r header; do
  base="$(basename "$header" | sed 's/-header\.csv$//')"
  IMPORT_ARGS+=" --relationships=${CONTAINER_DATA}/${base}-header.csv,${CONTAINER_DATA}/${base}-part.*"
done < <(find "$DATA_DIR" -maxdepth 1 -name '*-header.csv' \
  ! -name 'BiologicalProcess-header.csv' \
  ! -name 'CellularComponent-header.csv' \
  ! -name 'Compound-header.csv' \
  ! -name 'Disease-header.csv' \
  ! -name 'Drug-header.csv' \
  ! -name 'EcNumber-header.csv' \
  ! -name 'Gene-header.csv' \
  ! -name 'MolecularFunction-header.csv' \
  ! -name 'OrganismTaxon-header.csv' \
  ! -name 'Pathway-header.csv' \
  ! -name 'Phenotype-header.csv' \
  ! -name 'Protein-header.csv' \
  ! -name 'ProteinDomain-header.csv' \
  ! -name 'SideEffect-header.csv' \
  | sort)

echo "Running neo4j-admin import inside Docker container..."
echo ""

# Run import in a temporary container, mounting the CSV dir and a volume for data
docker run --rm \
  -v "$DATA_DIR:${CONTAINER_DATA}:ro" \
  -v crossbar_neo4j_data:/data \
  "neo4j:${NEO4J_VERSION}" \
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
    $IMPORT_ARGS \
    -- "$DB"

echo ""
echo "Import completed!"
echo ""
echo "Start Neo4j with the imported data:"
echo ""
echo "  docker run -d \\"
echo "    --name crossbar-neo4j \\"
echo "    -p 7474:7474 -p 7687:7687 \\"
echo "    -v crossbar_neo4j_data:/data \\"
echo "    -e NEO4J_AUTH=neo4j/your-password-here \\"
echo "    -e NEO4J_server_default__database=$DB \\"
echo "    neo4j:${NEO4J_VERSION}"
echo ""
echo "Then open http://localhost:7474 or connect at bolt://localhost:7687"
