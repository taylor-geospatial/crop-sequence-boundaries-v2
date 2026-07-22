#!/bin/bash
# One-time: convert the USDA CSB1825 FileGDB into a Hilbert-sorted, bbox-indexed
# GeoParquet reused by every parity / object-level evaluation.

#SBATCH --job-name=csb-prep-usda
#SBATCH --partition=cpu
#SBATCH --account=YOUR_ACCOUNT
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=300G
#SBATCH --time=02:00:00
#SBATCH --output=logs/prep_usda_%j.out
#SBATCH --error=logs/prep_usda_%j.err

set -euo pipefail
mkdir -p logs data/eval
cd "$SLURM_SUBMIT_DIR"
export UV_LINK_MODE=copy

USDA_GDB=${USDA_GDB:-data/input/usda/NationalCSB_2018-2025_rev23/CSB1825.gdb}
USDA_OUT=${USDA_OUT:-data/eval/usda_csb1825_indexed.parquet}

uv run python -c "
from pathlib import Path
from csb.parity import prep_usda_gdb
prep_usda_gdb(Path('$USDA_GDB'), Path('$USDA_OUT'), threads=48)
print('wrote', '$USDA_OUT')
"
