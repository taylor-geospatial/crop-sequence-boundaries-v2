#!/bin/bash
# Full evaluation of one CONUS output against the USDA CSB1825 reference:
# index ours -> 16-region parity + per-class 2024 -> whole-CONUS parity ->
# object-level matched-polygon IoU. Reuses a pre-built USDA index.
#
# Required env:
#   OURS_NATIONAL  national GeoParquet from postprocess
#   TAG            short label for the output files (e.g. noroads, roads)
#   USDA_INDEXED   prepped USDA parquet (from prep_usda.sbatch)

#SBATCH --job-name=csb-eval
#SBATCH --partition=cpu
#SBATCH --account=YOUR_ACCOUNT
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=64
#SBATCH --mem=400G
#SBATCH --time=03:00:00
#SBATCH --output=logs/eval_%j.out
#SBATCH --error=logs/eval_%j.err

set -euo pipefail
mkdir -p logs data/eval
cd "$SLURM_SUBMIT_DIR"
export UV_LINK_MODE=copy

: "${OURS_NATIONAL:?set OURS_NATIONAL}"
: "${TAG:?set TAG}"
USDA_INDEXED=${USDA_INDEXED:-data/eval/usda_csb1825_indexed.parquet}
OURS_INDEXED=data/eval/ours_${TAG}_indexed.parquet

echo "[eval:$TAG] indexing ours -> $OURS_INDEXED"
uv run python -c "
from pathlib import Path
from csb.parity import prep_ours_parquet
prep_ours_parquet(Path('$OURS_NATIONAL'), Path('$OURS_INDEXED'), threads=64)
print('indexed ours')
"

echo "[eval:$TAG] 16-region parity + per-class 2024"
uv run csb parity --ours "$OURS_INDEXED" --usda "$USDA_INDEXED" \
    --threads 64 --per-class 2024 --report data/eval/parity_${TAG}.json

echo "[eval:$TAG] whole-CONUS parity"
uv run csb parity --ours "$OURS_INDEXED" --usda "$USDA_INDEXED" \
    --threads 64 --whole-conus --report data/eval/parity_conus_${TAG}.json

echo "[eval:$TAG] object-level matched-polygon IoU (16 regions)"
uv run csb object-eval --ours "$OURS_INDEXED" --usda "$USDA_INDEXED" \
    --threads 64 -o data/eval/object_${TAG}.json

echo "[eval:$TAG] done"
