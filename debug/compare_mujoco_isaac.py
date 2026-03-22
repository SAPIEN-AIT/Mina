import json
import sys

# File paths
mujoco_path = "debug/mujoco_debug.jsonl"
isaac_path = "debug/isaacsim_standalone_debug.jsonl"
diff_log_path = "debug/isaac_mujoco_diff_log.txt"

# Read JSONL files into lists of dicts
def read_jsonl(path):
    with open(path, 'r') as f:
        return [json.loads(line) for line in f if line.strip()]

mujoco_data = read_jsonl(mujoco_path)
isaac_data = read_jsonl(isaac_path)

# Check lengths
if len(mujoco_data) != len(isaac_data):
    print(f"Warning: Different number of steps: mujoco={len(mujoco_data)}, isaac={len(isaac_data)}")

# Compute differences and log
with open(diff_log_path, 'w') as log:
    for i, (mj, isac) in enumerate(zip(mujoco_data, isaac_data)):
        diff = {}
        rel_diff = {}
        for k in mj:
            if k in isac:
                try:
                    v1 = float(mj[k])
                    v2 = float(isac[k])
                    diff[k] = v1 - v2
                    if abs(v1) > 1e-8:
                        rel = abs(v1 - v2) / abs(v1)
                        rel_diff[k] = rel
                    else:
                        rel_diff[k] = float('inf')
                except Exception:
                    continue
        log.write(f"Step {i}:\n")
        for k in diff:
            log.write(f"  {k}: mujoco={mj[k]}, isaac={isac[k]}, diff={diff[k]:.4g}, rel_diff={rel_diff[k]:.2%}\n")
        log.write("\n")

# Print steps with any rel diff > 20%
print("Steps with relative difference > 20%:")
for i, (mj, isac) in enumerate(zip(mujoco_data, isaac_data)):
    for k in mj:
        if k in isac:
            try:
                v1 = float(mj[k])
                v2 = float(isac[k])
                if abs(v1) > 1e-8:
                    rel = abs(v1 - v2) / abs(v1)
                else:
                    rel = float('inf')
                if rel > 0.2:
                    print(f"Step {i}, key {k}: mujoco={v1}, isaac={v2}, rel_diff={rel:.2%}")
            except Exception:
                continue
