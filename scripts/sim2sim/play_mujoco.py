# Copyright (c) 2025, The Berkeley Humanoid Lite Project Developers.

"""Standalone MuJoCo sim2sim playback.

Loads a trained ONNX policy and runs it in the MuJoCo simulator at 25 Hz.
No ROS dependency — the simulation loop is self-contained.

Usage:
    uv run ./scripts/sim2sim/play_mujoco.py --config ./configs/policy_humanoid.yaml
"""

import numpy as np
import torch

from berkeley_humanoid_lite_lowlevel.policy.rl_controller import RlController
from berkeley_humanoid_lite.environments import MujocoSimulator, Cfg


# Load configuration
cfg = Cfg.from_arguments()

if not cfg:
    raise ValueError("Failed to load config.")


# Main execution block
def main():
    """Main execution function for the MuJoCo simulation environment."""
    # Initialize environment
    robot = MujocoSimulator(cfg)
    obs = robot.reset()

    # Initialize and start policy controller
    controller = RlController(cfg)
    controller.load_policy()

    # Open log files
    actions_log = open("debug/policy_actions_mujoco.log", "w")  # File A
    obs_log = open("debug/policy_observations_mujoco.log", "w")  # File B

    # Default actions for fallback
    default_actions = np.array(cfg.default_joint_positions, dtype=np.float32)[robot.cfg.action_indices]

    # Main control loop
    try:
        while True:
            # Log observation before sending to policy
            obs_list = obs.numpy().tolist() if hasattr(obs, 'numpy') else obs.tolist()
            obs_log.write(f"{obs_list}\n")
            obs_log.flush()

            # Send observations and receive actions
            actions = controller.update(obs.numpy())

            # Log actions output by policy
            actions_list = actions.tolist() if hasattr(actions, 'tolist') else list(actions)
            actions_log.write(f"{actions_list}\n")
            actions_log.flush()

            # Use default actions if no actions received
            if actions is None:
                actions = default_actions

            # Execute step
            actions = torch.tensor(actions)
            obs = robot.step(actions)
    finally:
        actions_log.close()
        obs_log.close()


if __name__ == "__main__":
    main()
