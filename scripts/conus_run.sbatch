#!/bin/bash
#SBATCH --job-name=csb-conus
#SBATCH --partition=cpu
#SBATCH --account=bgtj-tgirails
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=256G
#SBATCH --time=04:00:00
#SBATCH --output=data/logs/conus_%j.out
#SBATCH --error=data/logs/conus_%j.err

set -euo pipefail
mkdir -p data/logs
cd "$SLURM_SUBMIT_DIR"

CONFIG=configs/conus.yaml

echo "==== CONUS run starting at $(date) ===="
echo "Node: $SLURMD_NODENAME, cpus=$SLURM_CPUS_PER_TASK, mem=${SLURM_MEM_PER_NODE}MB"

t0=$(date +%s)
uv run csb --config "$CONFIG" polygonize 2018 2025
t1=$(date +%s)
echo "==== polygonize done in $((t1-t0))s ===="

uv run csb --config "$CONFIG" postprocess 2018 2025 \
    --polygonize-dir data/output/conus/polygonize/2018_2025
t2=$(date +%s)
echo "==== postprocess done in $((t2-t1))s ===="
echo "==== CONUS total wall: $((t2-t0))s ===="
echo "==== finished at $(date) ===="
