#!/usr/bin/env python3
"""Gamepad to ROS2 /cmd_vel bridge node.

Reads Xbox controller input via Se2Gamepad and publishes geometry_msgs/Twist
messages to /cmd_vel at ~50 Hz. Values are passed through as-is in [-1, 1] range.

Usage:
    cd ~/dev/Mina
    source /opt/ros/humble/setup.bash
    uv run python source/ros/gamepad_teleop.py
"""

import argparse
import sys

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist

# Fix inputs library bug: module-level DeviceManager() crashes on LED parsing
# when a sysfs LED can't be associated with a gamepad device.
# (https://github.com/zeth/inputs/issues/134)
# We must patch before `import inputs` since the crash happens at module scope.
import importlib.util as _ilu
_spec = _ilu.find_spec("inputs")
_src = _spec.origin
with open(_src) as _f:
    _code = _f.read()
_code = _code.replace(
    "self._character_device_path = self.gamepad.get_char_device_path()",
    "self._character_device_path = self.gamepad.get_char_device_path() if self.gamepad else None",
)
import types
_mod = types.ModuleType("inputs")
_mod.__file__ = _src
_mod.__spec__ = _spec
import sys
sys.modules["inputs"] = _mod
exec(compile(_code, _src, "exec"), _mod.__dict__)

from berkeley_humanoid_lite_lowlevel.policy.gamepad import Se2Gamepad


class GamepadTeleopNode(Node):
    """ROS2 node that bridges gamepad input to /cmd_vel."""

    def __init__(self, topic: str = "/cmd_vel", rate: float = 50.0, verbose: bool = False):
        super().__init__("gamepad_teleop")

        self._publisher = self.create_publisher(Twist, topic, 10)
        self._timer = self.create_timer(1.0 / rate, self._timer_callback)
        self._verbose = verbose

        self._gamepad: Se2Gamepad | None = None
        self._gamepad_connected = False

        # Try to initialize gamepad
        try:
            self._gamepad = Se2Gamepad()
            self._gamepad.run()
            self._gamepad_connected = True
            self.get_logger().info(f"Gamepad connected. Publishing to {topic} at {rate} Hz")
        except Exception as e:
            self.get_logger().error(f"Failed to connect gamepad: {e}")
            self.get_logger().warn("Publishing zero velocities until gamepad is connected")
    
    def _timer_callback(self):
        """Publish gamepad commands as Twist message."""
        msg = Twist()
        
        if self._gamepad_connected and self._gamepad is not None:
            try:
                commands = self._gamepad.commands
                msg.linear.x = float(commands["velocity_x"])    # forward/back
                msg.linear.y = float(commands["velocity_y"])    # lateral
                msg.angular.z = float(commands["velocity_yaw"]) # yaw
            except Exception as e:
                self.get_logger().warn(f"Error reading gamepad: {e}", throttle_duration_sec=5.0)

        self._publisher.publish(msg)
        if self._verbose:
            self.get_logger().info(
                f"vx={msg.linear.x:+.3f}  vy={msg.linear.y:+.3f}  wz={msg.angular.z:+.3f}",
                throttle_duration_sec=0.1,
            )
    
    def shutdown(self):
        """Clean shutdown of gamepad thread."""
        if self._gamepad is not None:
            try:
                self._gamepad.stop()
                self.get_logger().info("Gamepad stopped")
            except Exception as e:
                self.get_logger().warn(f"Error stopping gamepad: {e}")


def parse_args():
    parser = argparse.ArgumentParser(description="Gamepad to ROS2 /cmd_vel bridge")
    parser.add_argument("--topic", type=str, default="/cmd_vel",
                        help="ROS2 topic to publish Twist messages (default: /cmd_vel)")
    parser.add_argument("--rate", type=float, default=50.0,
                        help="Publishing rate in Hz (default: 50)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print live commands at ~10 Hz")
    return parser.parse_args()


def main():
    args = parse_args()
    
    rclpy.init()
    node = GamepadTeleopNode(topic=args.topic, rate=args.rate, verbose=args.verbose)
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
