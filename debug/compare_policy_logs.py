import numpy as np

def load_log(filename):
    with open(filename, 'r') as f:
        return [np.array(eval(line.strip())) for line in f if line.strip()]

def compare_logs(file_a, file_b, label_a, label_b, atol=1e-6):
    log_a = load_log(file_a)
    log_b = load_log(file_b)
    min_len = min(len(log_a), len(log_b))
    print(f"Comparing {label_a} ({len(log_a)} entries) vs {label_b} ({len(log_b)} entries)")
    for i in range(min_len):
        arr_a = log_a[i]
        arr_b = log_b[i]
        if arr_a.shape != arr_b.shape:
            print(f"Line {i}: shape mismatch {arr_a.shape} vs {arr_b.shape}")
            continue
        if not np.allclose(arr_a, arr_b, atol=atol):
            diff = arr_a - arr_b
            print(f"Line {i}: values differ (max abs diff={np.max(np.abs(diff)):.4g})")
            print(f"  {label_a}: {arr_a}")
            print(f"  {label_b}: {arr_b}")
    print(f"Compared {min_len} lines. Done.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Compare two RL log files.")
    parser.add_argument('--isaacsim-actions', type=str, default='policy_actions.log', help='IsaacSim actions log file')
    parser.add_argument('--mujoco-actions', type=str, default='policy_actions_mujoco.log', help='MuJoCo actions log file')
    parser.add_argument('--isaacsim-obs', type=str, default='policy_observations.log', help='IsaacSim observations log file')
    parser.add_argument('--mujoco-obs', type=str, default='policy_observations_mujoco.log', help='MuJoCo observations log file')
    parser.add_argument('--atol', type=float, default=1e-6, help='Absolute tolerance for comparison')
    args = parser.parse_args()

    print("=== Comparing Policy Actions ===")
    compare_logs(args.isaacsim_actions, args.mujoco_actions, 'IsaacSim', 'MuJoCo', atol=args.atol)
    print("\n=== Comparing Policy Observations ===")
    compare_logs(args.isaacsim_obs, args.mujoco_obs, 'IsaacSim', 'MuJoCo', atol=args.atol)
