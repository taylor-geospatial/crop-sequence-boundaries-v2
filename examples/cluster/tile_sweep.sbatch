#!/bin/bash
# Parity parameter sweep: one array task per region, each sweeping
# min_cropland_years x simplify_tolerance x roads-mask on/off against USDA
# CSB1825. Finds the settings that maximize per-tile parity.

#SBATCH --job-name=csb-sweep
#SBATCH --partition=cpu
#SBATCH --account=YOUR_ACCOUNT
#SBATCH --array=0-15
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=24
#SBATCH --mem=200G
#SBATCH --time=04:00:00
#SBATCH --output=logs/sweep_%A_%a.out
#SBATCH --error=logs/sweep_%A_%a.err

set -euo pipefail
mkdir -p logs data/eval/sweep
cd "$SLURM_SUBMIT_DIR"
export UV_LINK_MODE=copy

REGIONS=(iowa_corn_belt illinois_corn nebraska_irrigated kansas_wheat \
         texas_panhandle texas_cotton_belt mississippi_delta georgia_peanut \
         central_valley_ca imperial_valley_ca palouse_wheat snake_river_id \
         northern_plains_nd wisconsin_mixed ohio_corn_soy delmarva)
REGION=${REGIONS[$SLURM_ARRAY_TASK_ID]}

USDA_INDEXED=${USDA_INDEXED:-data/eval/usda_csb1825_indexed.parquet}
ROADS=$(readlink -f data/input/roads/overture_roads.parquet)

uv run csb tile-sweep --region "$REGION" \
    --usda-indexed "$USDA_INDEXED" \
    --cropland-years "${CROPLAND_YEARS:-1,2}" \
    --simplify "${SIMPLIFY:-30,60}" \
    --min-area "${MIN_AREA:-10000}" \
    --roads-mask "$ROADS" \
    --threads 24 \
    -o data/eval/sweep/sweep_${REGION}.json
