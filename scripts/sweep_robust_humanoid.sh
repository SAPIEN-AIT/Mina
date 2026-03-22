#!/usr/bin/env bash
# =============================================================================
# sweep_robust_humanoid.sh — Submit 6 cluster jobs for robust humanoid training
#
# Usage: bash scripts/sweep_robust_humanoid.sh
# Run from the project root (/home/alex/dev/Mina).
#
# Each variant patches the source files, submits a job, then restores the
# originals for the next variant. Requires cluster access via cluster_interface.sh.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CLUSTER_CMD="bash $ROOT/docker_mina/cluster/cluster_interface.sh job mina-isaaclab-base"

# Iteration counts — override with MAX_ITER=N bash scripts/sweep_robust_humanoid.sh
MAX_ITER="${MAX_ITER:-6000}"
MAX_ITER_V4="${MAX_ITER_V4:-10000}"  # V4 uses more iters by design
ENV_CFG="$ROOT/source/berkeley_humanoid_lite/berkeley_humanoid_lite/tasks/locomotion/velocity/config/humanoid/env_cfg.py"
REWARDS_CFG="$ROOT/source/berkeley_humanoid_lite/berkeley_humanoid_lite/tasks/locomotion/velocity/mdp/rewards.py"

# Backup originals
cp "$ENV_CFG"    "$ENV_CFG.bak"
cp "$REWARDS_CFG" "$REWARDS_CFG.bak"

restore() {
    cp "$ENV_CFG.bak"     "$ENV_CFG"
    cp "$REWARDS_CFG.bak" "$REWARDS_CFG"
}
trap restore EXIT

submit() {
    local name="$1"; shift
    local seed="$1"; shift
    local max_iter="${1:-6000}"; shift || true
    local num_steps="${1:-24}"; shift || true

    echo "==> Submitting: $name (seed=$seed, max_iter=$max_iter, num_steps=$num_steps)"
    $CLUSTER_CMD \
        --task Velocity-Berkeley-Humanoid-Lite-v0 \
        --num_envs 4096 \
        --max_iterations "$max_iter" \
        --headless \
        --seed "$seed" \
        --experiment_name "$name" \
        --num_steps_per_env "$num_steps"
    echo "    Submitted."
    sleep 2
}

# =============================================================================
# V1 — Baseline (shared changes only, control variant)
# =============================================================================
restore
submit "humanoid_robust_baseline" 42 "$MAX_ITER"

# =============================================================================
# V2 — Tight tracking: std 0.5 → 0.3 for both velocity tracking rewards
# =============================================================================
restore
sed -i 's/"command_name": "base_velocity", "std": 0\.5},\n        weight=2\.0/"command_name": "base_velocity", "std": 0.3},\n        weight=2.0/' "$ENV_CFG" || true
# More reliable: patch both std values directly
python3 - "$ENV_CFG" <<'PYEOF'
import sys, re
path = sys.argv[1]
text = open(path).read()
# Replace std=0.5 in both tracking reward entries
count = 0
def replacer(m):
    global count
    count += 1
    if count <= 2:
        return m.group(0).replace('"std": 0.5', '"std": 0.3')
    return m.group(0)
text = re.sub(r'"command_name": "base_velocity", "std": 0\.5', '"command_name": "base_velocity", "std": 0.3', text)
open(path, 'w').write(text)
print(f"  Patched tracking stds to 0.3")
PYEOF
submit "humanoid_robust_tight_tracking" 43 "$MAX_ITER"

# =============================================================================
# V3 — High standing: rel_standing_envs 0.15 → 0.25
# =============================================================================
restore
sed -i 's/rel_standing_envs=0\.15/rel_standing_envs=0.25/' "$ENV_CFG"
submit "humanoid_robust_high_standing" 44 "$MAX_ITER"

# =============================================================================
# V4 — Longer training: 10000 iters, 32 steps/env
# =============================================================================
restore
submit "humanoid_robust_longer_train" 45 "$MAX_ITER_V4" 32

# =============================================================================
# V5 — Strong regularization: double action_rate, torques, stand_still weight
# =============================================================================
restore
sed -i 's/weight=-0\.001,  # action_rate_l2/weight=-0.002,  # action_rate_l2/' "$ENV_CFG" || true
python3 - "$ENV_CFG" <<'PYEOF'
import sys
path = sys.argv[1]
text = open(path).read()
text = text.replace('func=mdp.action_rate_l2,\n        weight=-0.001,', 'func=mdp.action_rate_l2,\n        weight=-0.002,')
text = text.replace('func=mdp.joint_torques_l2,\n        params={"asset_cfg": SceneEntityCfg("robot", joint_names=HUMANOID_LITE_JOINTS)},\n        weight=-2.0e-5,',
                    'func=mdp.joint_torques_l2,\n        params={"asset_cfg": SceneEntityCfg("robot", joint_names=HUMANOID_LITE_JOINTS)},\n        weight=-4.0e-5,')
text = text.replace('func=mdp.stand_still_penalty,\n        params={"command_name": "base_velocity"},\n        weight=-1.5,',
                    'func=mdp.stand_still_penalty,\n        params={"command_name": "base_velocity"},\n        weight=-3.0,')
open(path, 'w').write(text)
print("  Patched strong regularization weights")
PYEOF
submit "humanoid_robust_strong_reg" 46 "$MAX_ITER"

# =============================================================================
# V6 — Aggressive push: ±1.5 m/s, tighter interval, stronger base forces
# =============================================================================
restore
python3 - "$ENV_CFG" <<'PYEOF'
import sys
path = sys.argv[1]
text = open(path).read()
text = text.replace('"velocity_range": {"x": (-0.8, 0.8), "y": (-0.8, 0.8)}',
                    '"velocity_range": {"x": (-1.5, 1.5), "y": (-1.5, 1.5)}')
text = text.replace('interval_range_s=(8.0, 12.0)',
                    'interval_range_s=(5.0, 10.0)')
text = text.replace('"force_range": (-2.0, 2.0)',
                    '"force_range": (-5.0, 5.0)')
open(path, 'w').write(text)
print("  Patched aggressive push params")
PYEOF
submit "humanoid_robust_aggressive_push" 47 "$MAX_ITER"

echo ""
echo "All 6 jobs submitted. Monitor with:"
echo "  ssh huou@kuma.hpc.epfl.ch squeue -u huou"
echo "  tensorboard --logdir logs/rsl_rl/humanoid/"
