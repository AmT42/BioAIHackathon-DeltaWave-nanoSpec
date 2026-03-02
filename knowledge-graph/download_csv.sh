#!/usr/bin/env bash
# Download CROssBAR knowledge-graph CSV files from Google Drive.
#
# The CROssBAR knowledge graph is built by the CROssBARv2 project:
#   https://github.com/HUBioDataLab/CROssBARv2-KG
#
# Usage:
#   ./download_csv.sh
#
# Requires: pip install gdown  (handles Google Drive large-file downloads)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${SCRIPT_DIR}/data"
GDRIVE_FILE_ID="1KoMAxlvy_4IOo8MPi4TrSbMlQtBf8Pch"
GDRIVE_URL="https://drive.google.com/uc?id=${GDRIVE_FILE_ID}"

echo "=== CROssBAR Knowledge Graph CSV Download ==="
echo "Source: https://github.com/HUBioDataLab/CROssBARv2-KG"
echo "Dest:   $DATA_DIR"
echo ""

mkdir -p "$DATA_DIR"

ARCHIVE="${DATA_DIR}/crossbar_csv.zip"

# Google Drive requires special handling for large files.
# gdown is the most reliable tool for this.
if command -v gdown &>/dev/null; then
  echo "Downloading with gdown..."
  gdown "$GDRIVE_URL" -O "$ARCHIVE"
else
  echo "Error: gdown is not installed."
  echo ""
  echo "Install it with:"
  echo "  pip install gdown"
  echo ""
  echo "Then re-run this script."
  exit 1
fi

echo "Extracting..."
# Extract to a temp dir first so we can flatten nested folders
TMPDIR="${DATA_DIR}/_extract_tmp"
mkdir -p "$TMPDIR"
unzip -o "$ARCHIVE" -d "$TMPDIR"

# Move CSVs to data dir (handles both flat and single-subfolder archives)
find "$TMPDIR" -name '*.csv' -exec mv {} "$DATA_DIR/" \;

rm -rf "$TMPDIR"
rm -f "$ARCHIVE"

echo ""
echo "Done. CSV files are in: $DATA_DIR"
echo "Total CSV files: $(find "$DATA_DIR" -name '*.csv' | wc -l | tr -d ' ')"
