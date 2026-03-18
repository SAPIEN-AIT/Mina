# HPC Cluster Workflow — EPFL LPHE (lphesrv1)

## Quick Reference

### 0 — One-time configuration (do once)
```bash
# Replace YOUR_EPFL_USERNAME in both files
nano docker_mina/cluster/.env.cluster
nano docker_mina/cluster/submit_job_slurm.sh
```

### 1 — Build the Docker image locally (do once, or after Dockerfile changes)
```bash
cd /home/alex/dev/Mina
docker compose -f docker_mina/docker-compose.yaml build mina-isaaclab-base
```

### 2 — Convert and push the image to the cluster (do once, or after image changes)
```bash
bash docker_mina/cluster/cluster_interface.sh push mina-isaaclab-base
```

### 3 — Submit a training job
```bash
bash docker_mina/cluster/cluster_interface.sh job mina-isaaclab-base \
    --task Isaac-Velocity-Rough-H1-v0 \
    --num_envs 4096 \
    --headless
```

### 4 — Monitor the job on the cluster
```bash
ssh YOUR_EPFL_USERNAME@lphesrv1.epfl.ch
squeue -u $USER
tail -f /panfs/YOUR_EPFL_USERNAME/isaaclab_*/logs/<run_name>/...
```

---

## Detailed Explanation

### 0 — One-time configuration

Before anything else, two files must be edited with your real Gaspar username:

**`docker_mina/cluster/.env.cluster`** — defines all paths used by the scripts:

| Variable | Purpose |
|---|---|
| `CLUSTER_LOGIN` | SSH address of the login node (`you@lphesrv1.epfl.ch`) |
| `CLUSTER_ISAACLAB_DIR` | Where your code is rsync'd to on `/panfs` |
| `CLUSTER_ISAAC_SIM_CACHE_DIR` | Where Isaac Sim's warm cache lives on `/panfs` (must end in `docker-isaac-sim`) |
| `CLUSTER_SIF_PATH` | Where the `.tar`/`.sif` container image is stored on `/panfs` |
| `CLUSTER_PYTHON_EXECUTABLE` | The training script path relative to the Isaac Lab root |
| `REMOVE_CODE_COPY_AFTER_JOB` | Whether to clean up the timestamped code snapshot after the job finishes |

The `/panfs` filesystem is used for everything because `/home` has strict quotas and is network-mounted — writing large files there would be extremely slow and risk hitting the quota.

**`docker_mina/cluster/submit_job_slurm.sh`** — update `--mail-user` with your real email so SLURM can notify you when the job ends.

---

### 1 — Build the Docker image locally

```bash
docker compose -f docker_mina/docker-compose.yaml build mina-isaaclab-base
```

The `docker compose build` step compiles your `Dockerfile.base` into a local image named `mina-isaaclab-base:latest`. This image contains Isaac Sim, Isaac Lab, and all your Python dependencies baked in.

You only need to redo this step when you change the Dockerfile or add new pip dependencies.

---

### 2 — Convert and push the image to the cluster

```bash
bash docker_mina/cluster/cluster_interface.sh push mina-isaaclab-base
```

This single command does four things automatically:

1. **Converts the Docker image to a Singularity `.sif`** using `apptainer build --sandbox --fakeroot`. HPC clusters cannot run Docker directly (it requires root), so Apptainer/Singularity is used instead. The `--fakeroot` flag allows the conversion as a normal user.

2. **Tars the `.sif` directory** into a single `mina-isaaclab-base.tar` file. A Singularity sandbox image is actually a directory tree with thousands of files. Sending it as a single `.tar` is far faster over SSH than transferring each file individually.

3. **Creates the target directory on the cluster** (`$CLUSTER_SIF_PATH`) via SSH if it doesn't exist.

4. **Uploads the `.tar`** to the cluster with `scp`.

The resulting file lives at `/panfs/YOUR_EPFL_USERNAME/singularity/mina-isaaclab-base.tar` and does not need to be re-uploaded unless you rebuild the Docker image.

> **Note:** This step can take 10–30 minutes the first time depending on image size and network speed.

---

### 3 — Submit a training job

```bash
bash docker_mina/cluster/cluster_interface.sh job mina-isaaclab-base \
    --task Isaac-Velocity-Rough-H1-v0 \
    --num_envs 4096 \
    --headless
```

Everything after the image name is forwarded as-is to your Python training script. The full chain is:

```
cluster_interface.sh job base <args>
    │
    ├─ rsync: syncs your local Mina/IsaacLab code to
    │         /panfs/YOU/isaaclab_YYYYMMDD_HHMMSS/
    │         (timestamped so concurrent jobs don't collide)
    │
    └─ SSH → submit_job_slurm.sh <isaaclab_dir> <profile> <args>
                │
                └─ sbatch → job.sh (queued by SLURM)
                                │
                                ├─ sets TMPDIR=/scratch/$USER/$SLURM_JOB_ID
                                └─ run_singularity.sh <isaaclab_dir> <profile> <args>
                                        │
                                        ├─ copies cache + code + .sif → /scratch (local disk)
                                        ├─ singularity exec (with -B bind-mounts)
                                        │       └─ /isaac-sim/python.sh train.py <args>
                                        └─ rsync cache back to /panfs after training
```

Key design decisions in this chain:
- **Timestamped code directory**: each `job` call creates a new `isaaclab_YYYYMMDD_HHMMSS` snapshot on `/panfs`. This means multiple jobs submitted at different times each run from an independent snapshot of your code, avoiding race conditions.
- **All heavy I/O on `/scratch`**: the compute node copies everything (cache, code, container) to `/scratch/$USER/$SLURM_JOB_ID` before running. This is node-local NVMe/SSD storage — orders of magnitude faster than reading over the network from `/panfs` during training. The scratch directory is automatically wiped by SLURM after the job finishes.
- **Cache warm-up across jobs**: after the job finishes, `run_singularity.sh` rsyncs the Isaac Sim cache back from `/scratch` to `/panfs`. The next job picks up this warm cache (pre-compiled shaders, pip packages, etc.) and skips re-generating them, saving 5–20 minutes of startup time per job.
- **Logs go directly to `/panfs`**: the `logs/` subdirectory inside the container is bind-mounted straight to `/panfs/YOU/isaaclab_*/logs/` — bypassing scratch. This means your training curves are written to persistent storage in real time and are not lost if the job crashes or the node reboots before the rsync.

---

### 4 — Monitor the job

```bash
ssh YOUR_EPFL_USERNAME@lphesrv1.epfl.ch

# Check queue status
squeue -u $USER

# Check a specific job
scontrol show job <JOBID>

# Cancel a job
scancel <JOBID>

# Watch logs live (path depends on your training framework)
tail -f /panfs/YOUR_EPFL_USERNAME/isaaclab_*/logs/**/*.txt
```

---

## File Map

```
docker_mina/cluster/
├── .env.cluster          ← edit this: your Gaspar ID and /panfs paths
├── cluster_interface.sh  ← the one script you call from your laptop
├── submit_job_slurm.sh   ← edit this: SBATCH directives + mail address
├── run_singularity.sh    ← runs on the compute node; do not edit for normal use
└── hpc_workflow.md       ← this file
```
