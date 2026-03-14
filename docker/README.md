# Isaac Sim / Isaac Lab Docker Environment

This folder contains a fully automated, one-click deployment pipeline for running Isaac Sim and Isaac Lab in a GPU-accelerated Docker container.

## 0. Install Docker

If Docker is not installed yet, install it with the convenience script:

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

### Post-install setup

Add your user to the `docker` group so Docker commands work without `sudo`:

```bash
sudo groupadd docker
sudo usermod -aG docker $USER
newgrp docker
```

### Verify Docker

```bash
docker run hello-world
```

## 🚀 Quick Start

To install all necessary NVIDIA plumbing, generate your configuration, and start the container, run:

```bash
cd docker
chmod +x deploy_all.sh
sudo ./deploy_all.sh
```

`deploy_all.sh` must be run with `sudo`. It launches the `isaac-lab` container and registers the local Berkeley packages into the container's Python environment.

## Troubleshooting

If you hit an error similar to `could not find driver with gpu`, restart Docker and try again:

```bash
sudo systemctl restart docker
```

This often resets GPU runtime detection and resolves the issue.

## 🚪 How to Run Training

### Critical Rules

- Package registration is performed inside the container as `root`.
- Because of that, all interactive commands must also use `docker exec -u root ...`.
- Do not run plain `python3` for Isaac Lab tasks inside the container.
- Always use the Isaac Lab wrapper:

```bash
/workspace/isaaclab/isaaclab.sh -p
```

### Option A: Run from the Host (Recommended)

```bash
docker exec -u root -it isaac-lab bash -c 'cd /workspace/isaaclab/source/standalone/mina_project && /workspace/isaaclab/isaaclab.sh -p scripts/rsl_rl/train.py --task Velocity-Berkeley-Humanoid-Lite-v0'
```

### Option B: Run from Inside the Container

Step 1. Enter the container as `root`:

```bash
docker exec -u root -it isaac-lab bash
```

Step 2. Run from the project directory using the Isaac Lab wrapper:

```bash
cd /workspace/isaaclab/source/standalone/mina_project
/workspace/isaaclab/isaaclab.sh -p scripts/rsl_rl/train.py --task Velocity-Berkeley-Humanoid-Lite-v0
```

Playback uses the same pattern:

```bash
/workspace/isaaclab/isaaclab.sh -p scripts/rsl_rl/play.py --task Velocity-Berkeley-Humanoid-Lite-v0
```

## 📂 File Structure

* **`deploy_all.sh`**: The master setup script. It safely handles `sudo` permissions, installs the NVIDIA Container Toolkit if missing, creates your local cache folders in `~/docker/isaac-sim/` (cache dir, separate from container name), writes the `.env.base` file, and starts the container.
* **`docker-compose.yaml`**: The blueprint that configures the container, passes through the physical GPUs, and maps your local repository to `/workspace`.
* **`Dockerfile`**: Fetches the base NVIDIA Isaac Sim image.
* **`.env.base`**: *(Auto-generated)* Stores your specific User ID, Group ID, and cache directory paths to prevent permission errors.