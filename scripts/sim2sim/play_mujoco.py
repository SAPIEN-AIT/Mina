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


cfg = Cfg.from_arguments()

if not cfg:
    raise ValueError("Failed to load config.")


def main():
    robot = MujocoSimulator(cfg)
    obs = robot.reset()

    controller = RlController(cfg)
    controller.load_policy()

    default_actions = np.array(cfg.default_joint_positions, dtype=np.float32)[robot.cfg.action_indices]

    while True:
        obs_np = obs.numpy() if hasattr(obs, 'numpy') else np.array(obs)
        actions = controller.update(obs_np)

        if actions is None:
            actions = default_actions

        obs = robot.step(torch.tensor(actions))


if __name__ == "__main__":
    main()
