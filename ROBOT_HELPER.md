# Berkeley Humanoid Lite: Isaac Lab Helper

This workspace is configured to train Berkeley Humanoid Lite in Isaac Lab with the finalized Docker workflow.

## One-Time Deployment

Run the deployment script from the host with `sudo`:

```bash
cd docker
chmod +x deploy_all.sh
sudo ./deploy_all.sh
```

This starts the `isaac-sim` container and registers the local Berkeley packages inside the container as `root`.

## Critical Rules

1. Always execute interactive commands as `root` with `docker exec -u root ...`.
2. Do not use plain `python3` for Isaac Lab runs inside the container.
3. Always use the Isaac Lab wrapper:

```bash
/workspace/isaaclab/isaaclab.sh -p
```

## Training Commands

1. Train full humanoid from the host:

```bash
docker exec -u root -it isaac-sim bash -c 'cd /workspace/isaaclab/source/standalone/mina_project && /workspace/isaaclab/isaaclab.sh -p scripts/rsl_rl/train.py --task Velocity-Berkeley-Humanoid-Lite-v0'
```

2. Train biped variant from the host:

```bash
docker exec -u root -it isaac-sim bash -c 'cd /workspace/isaaclab/source/standalone/mina_project && /workspace/isaaclab/isaaclab.sh -p scripts/rsl_rl/train.py --task Velocity-Berkeley-Humanoid-Lite-Biped-v0'
```

3. Train from inside the container:

```bash
docker exec -u root -it isaac-sim bash
cd /workspace/isaaclab/source/standalone/mina_project
/workspace/isaaclab/isaaclab.sh -p scripts/rsl_rl/train.py --task Velocity-Berkeley-Humanoid-Lite-v0
```

## Playback

```bash
docker exec -u root -it isaac-sim bash -c 'cd /workspace/isaaclab/source/standalone/mina_project && /workspace/isaaclab/isaaclab.sh -p scripts/rsl_rl/play.py --task Velocity-Berkeley-Humanoid-Lite-v0'
```

## Troubleshooting Tips

- If you see `could not find driver with gpu`, run `sudo systemctl restart docker` on the host and retry.
- Code changes are visible immediately through the bind mount.
- Logs are saved under `logs/rsl_rl/<experiment>/...`.

## Confirmed Task IDs

1. `Velocity-Berkeley-Humanoid-Lite-v0`
2. `Velocity-Berkeley-Humanoid-Lite-Biped-v0`
