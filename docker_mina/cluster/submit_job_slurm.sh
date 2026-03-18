#!/usr/bin/env bash

# in the case you need to load specific modules on the cluster, add them here
# e.g., `module load eth_proxy`

# create job script with compute demands
### MODIFY HERE FOR YOUR JOB ###
cat <<EOT > job.sh
#!/bin/bash

#SBATCH -n 1
#SBATCH --cpus-per-task=4
#SBATCH --partition=l40s
#SBATCH --gres=gpu:1
#SBATCH --time=12:00:00
#SBATCH --mail-type=END
#SBATCH --mail-user=alexandre.huou@epfl.ch
#SBATCH --job-name="training-$(date +"%Y-%m-%dT%H:%M")"

# Use /scratch for all temporary I/O — avoids /home quota limits and
# network overhead. TMPDIR is picked up by run_singularity.sh.
export TMPDIR=/scratch/\$USER/\$SLURM_JOB_ID
export SINGULARITY_TMPDIR=\$TMPDIR/singularity-tmp
export SINGULARITY_CACHEDIR=\$TMPDIR/singularity-cache
export APPTAINER_TMPDIR=\$SINGULARITY_TMPDIR
export APPTAINER_CACHEDIR=\$SINGULARITY_CACHEDIR
mkdir -p "\$SINGULARITY_TMPDIR" "\$SINGULARITY_CACHEDIR"

# Pass the container profile first to run_singularity.sh, then all arguments intended for the executed script
bash "$1/docker_mina/cluster/run_singularity.sh" "$1" "$2" "${@:3}"
EOT

sbatch < job.sh
rm job.sh
