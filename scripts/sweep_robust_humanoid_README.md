# Robust Humanoid Policy Sweep

## Problem being solved

The current policy walks forward indefinitely with zero `cmd_vel` because:
1. Only 2% of training envs had zero-command episodes (`rel_standing_envs=0.02`)
2. Commands were held for 10s fixed — no walk→stop transitions
3. No reward signal for standing still or being near default pose

## Baseline changes (shared across all variants)

All changes are in the humanoid config only. Biped and observation space are untouched.

| File | What changed |
|------|-------------|
| `mdp/rewards.py` | Added `stand_still_penalty` and `stand_default_pose` |
| `config/humanoid/env_cfg.py` | `rel_standing_envs` 0.02→0.15, `resampling_time_range` (10,10)→(3,8), added `stand_still` and `stand_pose` reward terms, uncommented `push_robot` |

**New rewards:**
- `stand_still` (weight -1.5): penalizes any base XY velocity when `|cmd_vel_xy| < 0.1`
- `stand_pose` (weight +1.0): rewards being near default joint positions, but **only activates when both cmd and actual velocity are near zero** — avoids fighting deceleration mid-step

## Sweep variants

| # | Name | Key difference | Hypothesis |
|---|------|---------------|------------|
| V1 | `humanoid_robust_baseline` | Nothing beyond shared | Control — does baseline fix standing? |
| V2 | `humanoid_robust_tight_tracking` | Velocity tracking std 0.5→0.3 | Tighter tracking may reduce overshoot on stop |
| V3 | `humanoid_robust_high_standing` | `rel_standing_envs` 0.15→0.25 | More zero-command exposure |
| V4 | `humanoid_robust_longer_train` | 10k iters, 32 steps/env | More training time to converge the new rewards |
| V5 | `humanoid_robust_strong_reg` | Double `action_rate`, `torques`, `stand_still` weight | Smoother, more conservative policy |
| V6 | `humanoid_robust_aggressive_push` | Push ±1.5 m/s, interval 5-10s, force ±5 N | More robustness to external disturbances |

All variants: 4096 envs, 25 Hz policy, task `Velocity-Berkeley-Humanoid-Lite-v0`.

## Workflow

### Phase 1 — Smoke test locally (do this first)

```bash
./docker_mina/run.sh dev
# Inside the container:
isaaclab -p scripts/rsl_rl/train.py \
  --task Velocity-Berkeley-Humanoid-Lite-v0 \
  --num_envs 64 \
  --max_iterations 50 \
  --headless
```

Check that it runs 50 iterations without crashing. The reward log should show
`stand_still` and `stand_pose` terms appearing.

### Phase 2 — Submit cluster sweep

```bash
bash scripts/sweep_robust_humanoid.sh
```

Monitor jobs:
```bash
ssh huou@kuma.hpc.epfl.ch squeue -u huou
```

### Phase 3 — Compare results

```bash
tensorboard --logdir logs/rsl_rl/humanoid/
```

Key metrics to compare across variants:
- `Episode/Reward/stand_still` — should increase (less negative) as training progresses
- `Episode/Reward/stand_pose` — should increase toward 1.0 when standing
- `Episode/Reward/track_lin_vel_xy_exp` — must not regress significantly
- `Train/mean_reward` — overall policy quality

### Phase 4 — Export and validate winner

```bash
# Export ONNX from best checkpoint
./docker_mina/run.sh eval Velocity-Berkeley-Humanoid-Lite-v0

# Validate in MuJoCo
uv run scripts/sim2sim/play_mujoco.py --config configs/policy_latest.yaml

# Validate in Isaac Sim standalone
uv run scripts/sim2sim/play_isaacsim.py --config configs/policy_latest.yaml
```

## What to look for in TensorBoard

A good outcome: V1 or V3 shows the robot standing still with zero command
(ang_vel_body near zero, base_pos stable) while maintaining good velocity
tracking when commanded. V5 may be the most stable but potentially slower
to start moving. V6 should handle pushes best.

If all variants still walk forward, increase `stand_still` weight further
(-3.0 → -5.0) or push `rel_standing_envs` to 0.30.
