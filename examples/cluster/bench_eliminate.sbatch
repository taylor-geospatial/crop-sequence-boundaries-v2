#!/bin/bash
# Controlled same-machine elimination benchmark: raster-side vs polygon-side
# (DuckDB + SedonaDB) on one Iowa I15 tile across sizes. Feeds the paper's
# controlled comparison and the internal engine choice. Results flush per size.

#SBATCH --job-name=csb-bench
#SBATCH --partition=cpu
#SBATCH --account=YOUR_ACCOUNT
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=256G
#SBATCH --time=06:00:00
#SBATCH --output=logs/bench_%j.out
#SBATCH --error=logs/bench_%j.err

set -euo pipefail
mkdir -p logs results
cd "$SLURM_SUBMIT_DIR"

export UV_LINK_MODE=copy

# Iowa I15 tile origin (col 15, row I) at 5000-px tiling of the national CDL.
COL_OFF=${COL_OFF:-75000}
ROW_OFF=${ROW_OFF:-40000}
SIZES=${SIZES:-1000,2500,5000}
REPEATS=${REPEATS:-5}

uv run --group bench csb bench-eliminate 2018 2025 \
    --col-off "$COL_OFF" --row-off "$ROW_OFF" \
    --sizes "$SIZES" --repeats "$REPEATS" \
    --implementations "${IMPLS:-raster,duckdb,sedona}" \
    -o results/bench_eliminate_i15.json
