#!/bin/bash
#SBATCH --job-name=csb-parity
#SBATCH --partition=cpu_amd
#SBATCH --account=bgtj-tgirails
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=128G
#SBATCH --time=02:00:00
#SBATCH --output=data/logs/parity_%j.out
#SBATCH --error=data/logs/parity_%j.err

# Multi-tile USDA parity study. Submit from the repo root:
#   sbatch scripts/multi_tile_parity.sbatch

mkdir -p data/logs
cd "$SLURM_SUBMIT_DIR"

uv run python scripts/multi_tile_parity.py \
    --config configs/local.yaml \
    --tile-size 5000 \
    --years 2018 2025 \
    --output-root data/output/multi_tile \
    --report data/profile/multi_tile_parity.json
