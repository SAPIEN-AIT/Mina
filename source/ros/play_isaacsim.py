# Copyright (c) 2025, The Berkeley Humanoid Lite Project Developers.

"""
ROS2 Policy Controller — Minimal version matching MuJoCo exactly.

Publishes joint position targets at 25 Hz. PhysX joint drives handle PD
control internally at physics rate.

Usage:
    uv run ./scripts/sim2sim/play_isaacsim.py --config ./configs/policy_humanoid.yaml
"""

import argparse

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy
from geometry_msgs.msg import Twist
from sensor_msgs.msg import Imu, JointState
from omegaconf import OmegaConf

from berkeley_humanoid_lite_lowlevel.policy.rl_controller import RlController


def parse_args():
    parser = argparse.ArgumentParser(
        description="ROS2 Policy Controller for Berkeley Humanoid Lite"
    )
    parser.add_argument("--config", type=str, default="./configs/policy_humanoid.yaml",
                        help="Path to policy configuration YAML")
    parser.add_argument("--joint-states-topic", type=str, default="joint_states")
    parser.add_argument("--imu-topic", type=str, default="imu")
    parser.add_argument("--cmd-vel-topic", type=str, default="cmd_vel")
    parser.add_argument("--joint-command-topic", type=str, default="joint_command")
    parser.add_argument("--no-policy", action="store_true",
                        help="Disable RL policy — only publish default pose")
    #parser.add_argument("--use-sim-time", action="store_true",
    #                   help="Use /clock for timing (required for Isaac Sim)")
    return parser.parse_args()


class HumanoidPolicyNode(Node):

    def __init__(self, cfg, args):
        super().__init__("berkeley_humanoid_policy")
        self.cfg = cfg
        self._no_policy = args.no_policy

        # test
        #self.declare_parameter("use_sim_time", True)


        # Joint names in YAML order
        self.joint_names = list(cfg.joints)

        # Robot's joint order — learned from first /joint_states message
        self._robot_joint_names = None
        self._robot_to_yaml = None
        self._yaml_to_robot = None

        # Cached sensor state (all in YAML joint order)
        self._joint_pos = np.array(cfg.default_joint_positions, dtype=np.float32)
        self._joint_vel = np.zeros(cfg.num_joints, dtype=np.float32)
        self._base_quat = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        self._base_ang_vel = np.zeros(3, dtype=np.float32)
        self._cmd_vel = np.zeros(3, dtype=np.float32)

        # Sensor readiness flags
        self._joint_ready = False
        self._imu_ready = False

        # Policy state — match MuJoCo exactly
        self._policy_active = False
        self._first_policy_tick = True

        # Defaults
        self._default_joint_positions = np.array(cfg.default_joint_positions, dtype=np.float32)

        # Load RL policy
        self._controller = RlController(cfg)
        self._controller.load_policy()

        # Tick counter for periodic logging
        self._tick = 0

        # QoS — sensor subscriptions: BEST_EFFORT so we drop stale messages
        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=2,
        )
        # QoS — velocity commands: RELIABLE, small queue
        cmd_vel_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=2,
        )
        # QoS — command publisher: RELIABLE to match Isaac Sim's subscriber
        cmd_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=2,
        )

        # Subscriptions
        self.create_subscription(Twist, args.cmd_vel_topic,
                                 self._cmd_vel_callback, cmd_vel_qos)
        self.create_subscription(JointState, args.joint_states_topic,
                                 self._joint_states_callback, sensor_qos)
        self.create_subscription(Imu, args.imu_topic,
                                 self._imu_callback, sensor_qos)

        # Publisher
        self._pub = self.create_publisher(JointState, args.joint_command_topic, cmd_qos)

        # Single timer at 25 Hz — matches MuJoCo's policy loop
        self.create_timer(cfg.policy_dt, self._policy_callback)

        # Log files
        #self._actions_log = open("debug/policy_actions_isaacsim.log", "w")
        #self._obs_log = open("debug/policy_observations_isaacsim.log", "w")

        self.get_logger().info(
            f"Policy node ready | policy_rate: {1.0/cfg.policy_dt:.0f} Hz | "
            f"no_policy: {self._no_policy}"
        )

    # --- Sensor callbacks (minimal, no extras) ---

    def _cmd_vel_callback(self, msg: Twist):
        self._cmd_vel[0] = msg.linear.x
        self._cmd_vel[1] = msg.linear.y
        self._cmd_vel[2] = msg.angular.z

    def _joint_states_callback(self, msg: JointState):
        if self._robot_joint_names is None:
            self._robot_joint_names = list(msg.name)
            robot_name_to_idx = {n: i for i, n in enumerate(self._robot_joint_names)}
            self._yaml_to_robot = np.array(
                [robot_name_to_idx[n] for n in self.joint_names], dtype=np.int32)
            self._robot_to_yaml = np.argsort(self._yaml_to_robot).astype(np.int32)
            self.get_logger().info(f"Learned joint order ({len(self._robot_joint_names)} joints)")

        positions = np.array(msg.position, dtype=np.float32)
        self._joint_pos[:] = positions[self._yaml_to_robot]

        if len(msg.velocity) >= len(positions):
            velocities = np.array(msg.velocity, dtype=np.float32)
            self._joint_vel[:] = velocities[self._yaml_to_robot]

        self._joint_ready = True

    def _imu_callback(self, msg: Imu):
        q = msg.orientation
        self._base_quat[:] = [q.w, q.x, q.y, q.z]
        av = msg.angular_velocity
        self._base_ang_vel[:] = [av.x, av.y, av.z]
        self._imu_ready = True

    # --- Observation building (matches MuJoCo exactly) ---

    def _build_observation(self) -> np.ndarray:
        """Build 55-element obs matching MujocoSimulator._get_observations().

        RlController.update() transforms this into the 75-element policy input:
          - Replaces quat with projected gravity
          - Subtracts default joint positions
          - Appends prev_actions
        """
        return np.concatenate([
            self._base_quat,                                        # [0:4]
            self._base_ang_vel,                                     # [4:7]
            self._joint_pos[list(self.cfg.action_indices)],        # [7:29]
            self._joint_vel[list(self.cfg.action_indices)],        # [29:51]
            [3.0],                                                  # [51] mode = RL
            [self._cmd_vel[0], self._cmd_vel[1] * 0.5, self._cmd_vel[2]],  # [52:55]
        ]).astype(np.float32)

    # --- Publish (simple) ---

    def _publish_positions(self, positions_yaml: np.ndarray):
        if self._robot_joint_names is None:
            return
        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name = self._robot_joint_names
        msg.position = positions_yaml[self._robot_to_yaml].tolist()
        msg.velocity = []
        msg.effort = []
        self._pub.publish(msg)

    # --- Policy loop (25 Hz, matches MuJoCo's main loop exactly) ---

    def _policy_callback(self):
        if self._robot_joint_names is None:
            return

        if self._no_policy:
            self._publish_positions(self._default_joint_positions)
            return

        if not (self._joint_ready and self._imu_ready):
            self._publish_positions(self._default_joint_positions)
            return

        # Activate policy on first sensor-ready tick
        if not self._policy_active:
            self._policy_active = True
            self._controller.prev_actions[:] = 0.0
            self.get_logger().info("Sensors ready — starting RL policy.")

        # First tick: MuJoCo sends zeros (reset quirk). Replicate exactly.
        if self._first_policy_tick:
            self._first_policy_tick = False
            num_actions = len(self.cfg.action_indices)
            obs = np.concatenate([
                np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32),  # identity quat
                np.zeros(3, dtype=np.float32),                       # zero ang_vel
                np.zeros(num_actions, dtype=np.float32),             # zero joint_pos
                np.zeros(num_actions, dtype=np.float32),             # zero joint_vel
                np.array([3.0], dtype=np.float32),                   # mode = RL
                np.zeros(3, dtype=np.float32),                       # zero cmd_vel
            ])
            self.get_logger().info("First tick: zero observation (MuJoCo reset quirk)")
        else:
            obs = self._build_observation()

        # Log observation
        #self._obs_log.write(f"{obs.tolist()}\n")
        #self._obs_log.flush()

        # Run ONNX policy — identical to play_mujoco.py
        actions = self._controller.update(obs)

        # Log actions
        #if actions is not None:
         #   self._actions_log.write(f"{actions.tolist()}\n")
            #self._actions_log.flush()

        if actions is None:
            self._publish_positions(self._default_joint_positions)
            return

        # Build full target array — identical to MuJoCo
        target_positions = self._default_joint_positions.copy()
        for i, idx in enumerate(self.cfg.action_indices):
            target_positions[idx] = actions[i]

        # Periodic debug log
        #self._tick += 1
        #if self._tick % 25 == 1:
        #    deltas = target_positions - self._default_joint_positions
        #    max_delta = np.max(np.abs(deltas))
        #    self.get_logger().info(
        #        f"[tick={self._tick}] max_delta={max_delta:.4f} | "
        #        f"quat={self._base_quat} | cmd_vel={self._cmd_vel}"
        #    )

        self._publish_positions(target_positions)


def main():
    args = parse_args()
    print(f"Loading config from {args.config}")
    with open(args.config, "r") as f:
        cfg = OmegaConf.load(f)

    rclpy.init()
    node = HumanoidPolicyNode(cfg, args)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        #node._actions_log.close()
        #node._obs_log.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()