import numpy as np

def load_log(filename):
    with open(filename, 'r') as f:
        return [np.array(eval(line.strip())) for line in f if line.strip()]

def compare_observations(file_a, file_b, atol=1e-6):
    log_a = load_log(file_a)
    log_b = load_log(file_b)
    min_len = min(len(log_a), len(log_b))
    results = []
    results.append(f"Comparing {file_a} ({len(log_a)} entries) vs {file_b} ({len(log_b)} entries)")
    for i in range(min_len):
        arr_a = log_a[i]
        arr_b = log_b[i]
        if arr_a.shape != arr_b.shape:
            results.append(f"Line {i}: shape mismatch {arr_a.shape} vs {arr_b.shape}")
            continue
        if not np.allclose(arr_a, arr_b, atol=atol):
            diff = arr_a - arr_b
            results.append(f"Line {i}: values differ (max abs diff={np.max(np.abs(diff)):.4g})")
            results.append(f"  IsaacSim: {arr_a}")
            results.append(f"  MuJoCo:   {arr_b}")
    results.append(f"Compared {min_len} lines. Done.")
    return results

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Compare two RL observation log files.")
    parser.add_argument('--isaacsim', type=str, default='policy_observations_isaacsim.log', help='IsaacSim observations log file')
    parser.add_argument('--mujoco', type=str, default='policy_observations_mujoco.log', help='MuJoCo observations log file')
    parser.add_argument('--atol', type=float, default=1e-6, help='Absolute tolerance for comparison')
    args = parser.parse_args()

    results = compare_observations(args.isaacsim, args.mujoco, atol=args.atol)
    output_file = "compare_observations_results.txt"
    with open(output_file, "w") as f:
        for line in results:
            f.write(line + "\n")
    print(f"Results saved to {output_file}")
