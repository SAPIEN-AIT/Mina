#!/usr/bin/env python3
# Copyright (c) 2025, The Berkeley Humanoid Lite Project Developers.

"""
Hybrid Controller GUI - Gamepad to Arm Joint Targets Bridge

A Tkinter GUI that maps physical gamepad/joystick inputs to robotic arm joint
targets via ROS2. Allows dynamic remapping of joystick axes to joints with
configurable scale multipliers.

Usage:
    # Terminal 1: Start the joy node (connects to your gamepad)
    ros2 run joy joy_node

    # Terminal 2: Run this GUI
    cd ~/dev/Mina
    uv run python scripts/teleop/hybrid_gui.py

    # The GUI will open. Configure axis mappings and scale factors.
    # Hold the Debug Modifier button to see computed targets in terminal.

ROS2 Interfaces:
    Subscriber: /joy (sensor_msgs/msg/Joy)
    Publisher:  /hybrid_arm_commands (sensor_msgs/msg/JointState) @ 25 Hz
"""

import tkinter as tk
from tkinter import ttk
from typing import Optional

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy, JointState


# The 10 arm joints in order
ARM_JOINT_NAMES = [
    "left_shoulder_pitch",
    "left_shoulder_roll",
    "left_shoulder_yaw",
    "left_elbow",
    "left_wrist",
    "right_shoulder_pitch",
    "right_shoulder_roll",
    "right_shoulder_yaw",
    "right_elbow",
    "right_wrist",
]

# Default axis mappings (can be changed in GUI)
# Maps joint index -> joystick axis index
DEFAULT_AXIS_MAP = {
    0: 0,   # left_shoulder_pitch  -> axis 0 (left stick Y)
    1: 1,   # left_shoulder_roll   -> axis 1 (left stick X)
    2: 2,   # left_shoulder_yaw    -> axis 2 (right stick Y)
    3: 3,   # left_elbow           -> axis 3 (right stick X)
    4: 4,   # left_wrist           -> axis 4 (L2 trigger)
    5: 0,   # right_shoulder_pitch -> axis 0
    6: 1,   # right_shoulder_roll  -> axis 1
    7: 2,   # right_shoulder_yaw   -> axis 2
    8: 3,   # right_elbow          -> axis 3
    9: 5,   # right_wrist          -> axis 5 (R2 trigger)
}

# Default scale multipliers (radians per unit joystick deflection)
DEFAULT_SCALES = {
    0: 1.57,   # left_shoulder_pitch
    1: 1.57,   # left_shoulder_roll
    2: 1.57,   # left_shoulder_yaw
    3: 1.57,   # left_elbow
    4: 1.57,   # left_wrist
    5: 1.57,   # right_shoulder_pitch
    6: 1.57,   # right_shoulder_roll
    7: 1.57,   # right_shoulder_yaw
    8: 1.57,   # right_elbow
    9: 1.57,   # right_wrist
}

DEFAULT_DEBUG_BUTTON = 4  # Usually L1/LB


class HybridGuiNode(Node):
    """ROS2 node with Tkinter GUI for gamepad-to-arm mapping."""

    def __init__(self):
        super().__init__("hybrid_gui_node")

        # Latest joy message (None until first message received)
        self._latest_joy: Optional[Joy] = None

        # Computed target positions (radians)
        self._targets = [0.0] * len(ARM_JOINT_NAMES)

        # ═══════════════════════════════════════════════════════════════════════
        # ROS2 Setup
        # ═══════════════════════════════════════════════════════════════════════

        # Subscriber to /joy
        self.create_subscription(Joy, "/joy", self._joy_callback, 10)

        # Publisher to /hybrid_arm_commands
        self._pub = self.create_publisher(JointState, "/hybrid_arm_commands", 10)

        # 25 Hz timer for processing and publishing
        self._timer = self.create_timer(1.0 / 25.0, self._timer_callback)

        # ═══════════════════════════════════════════════════════════════════════
        # Tkinter GUI Setup
        # ═══════════════════════════════════════════════════════════════════════

        self._root = tk.Tk()
        self._root.title("Hybrid Controller - Gamepad to Arm Mapping")
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Storage for GUI widgets (per joint)
        self._axis_vars = []      # IntVar for axis index
        self._scale_vars = []     # DoubleVar for scale multiplier
        self._target_labels = []  # Labels showing computed targets

        # Debug modifier button index
        self._debug_button_var = tk.IntVar(value=DEFAULT_DEBUG_BUTTON)

        # Joy status indicator
        self._joy_status_var = tk.StringVar(value="Waiting for /joy...")

        self._build_gui()

        self.get_logger().info("Hybrid GUI Node started. Waiting for /joy messages...")

    def _build_gui(self):
        """Construct the Tkinter GUI."""
        root = self._root

        # ─────────────────────────────────────────────────────────────────────
        # Global Settings Frame
        # ─────────────────────────────────────────────────────────────────────
        global_frame = ttk.LabelFrame(root, text="Global Settings", padding=10)
        global_frame.pack(fill=tk.X, padx=10, pady=5)

        # Joy status
        ttk.Label(global_frame, text="Joy Status:").grid(row=0, column=0, sticky=tk.W)
        status_label = ttk.Label(global_frame, textvariable=self._joy_status_var,
                                  foreground="gray")
        status_label.grid(row=0, column=1, columnspan=3, sticky=tk.W, padx=5)
        self._status_label = status_label

        # Debug modifier button
        ttk.Label(global_frame, text="Debug Modifier Button:").grid(row=1, column=0, sticky=tk.W)
        debug_spin = ttk.Spinbox(global_frame, from_=0, to=15, width=5,
                                  textvariable=self._debug_button_var)
        debug_spin.grid(row=1, column=1, sticky=tk.W, padx=5)
        ttk.Label(global_frame, text="(Hold to print targets to terminal)",
                  foreground="gray").grid(row=1, column=2, sticky=tk.W)

        # ─────────────────────────────────────────────────────────────────────
        # Joint Mapping Frame
        # ─────────────────────────────────────────────────────────────────────
        joints_frame = ttk.LabelFrame(root, text="Joint Mappings", padding=10)
        joints_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Header row
        headers = ["Joint Name", "Axis Index", "Scale (rad)", "Current Target"]
        for col, header in enumerate(headers):
            ttk.Label(joints_frame, text=header, font=("TkDefaultFont", 9, "bold")
                      ).grid(row=0, column=col, padx=5, pady=2, sticky=tk.W)

        # Per-joint rows
        for i, joint_name in enumerate(ARM_JOINT_NAMES):
            row = i + 1

            # Joint name label
            ttk.Label(joints_frame, text=joint_name).grid(
                row=row, column=0, padx=5, pady=2, sticky=tk.W
            )

            # Axis index spinbox
            axis_var = tk.IntVar(value=DEFAULT_AXIS_MAP.get(i, 0))
            self._axis_vars.append(axis_var)
            axis_spin = ttk.Spinbox(joints_frame, from_=0, to=7, width=5,
                                     textvariable=axis_var)
            axis_spin.grid(row=row, column=1, padx=5, pady=2)

            # Scale multiplier entry
            scale_var = tk.DoubleVar(value=DEFAULT_SCALES.get(i, 1.0))
            self._scale_vars.append(scale_var)
            scale_entry = ttk.Entry(joints_frame, width=8, textvariable=scale_var)
            scale_entry.grid(row=row, column=2, padx=5, pady=2)

            # Current target label (read-only)
            target_label = ttk.Label(joints_frame, text="0.000 rad", width=12,
                                      relief=tk.SUNKEN, anchor=tk.E)
            target_label.grid(row=row, column=3, padx=5, pady=2)
            self._target_labels.append(target_label)

        # ─────────────────────────────────────────────────────────────────────
        # Instructions Frame
        # ─────────────────────────────────────────────────────────────────────
        info_frame = ttk.LabelFrame(root, text="Instructions", padding=10)
        info_frame.pack(fill=tk.X, padx=10, pady=5)

        instructions = (
            "1. Run 'ros2 run joy joy_node' in another terminal to start the gamepad driver.\n"
            "2. Set the Axis Index for each joint (check your gamepad with 'ros2 topic echo /joy').\n"
            "3. Set the Scale to convert joystick range [-1, 1] to radians (use negative to invert).\n"
            "4. Hold the Debug Modifier button to print computed targets to the terminal.\n"
            "5. Published to /hybrid_arm_commands at 25 Hz."
        )
        ttk.Label(info_frame, text=instructions, justify=tk.LEFT).pack(anchor=tk.W)

        # Set minimum window size
        root.update_idletasks()
        root.minsize(500, 450)

    def _joy_callback(self, msg: Joy):
        """Store the latest Joy message."""
        self._latest_joy = msg

    def _timer_callback(self):
        """25 Hz callback: process joy, update GUI, publish targets."""
        # Update Tkinter (non-blocking alternative to mainloop)
        try:
            self._root.update_idletasks()
            self._root.update()
        except tk.TclError:
            # Window was closed
            return

        joy = self._latest_joy

        # Update status indicator
        if joy is None:
            self._joy_status_var.set("Waiting for /joy...")
            self._status_label.configure(foreground="red")
        else:
            n_axes = len(joy.axes)
            n_buttons = len(joy.buttons)
            self._joy_status_var.set(f"Connected ({n_axes} axes, {n_buttons} buttons)")
            self._status_label.configure(foreground="green")

        # Compute targets for each joint
        for i, joint_name in enumerate(ARM_JOINT_NAMES):
            if joy is None:
                # No joy message yet - use default (0.0)
                self._targets[i] = 0.0
            else:
                # Get axis index from GUI
                try:
                    axis_idx = self._axis_vars[i].get()
                except tk.TclError:
                    axis_idx = 0

                # Get scale from GUI
                try:
                    scale = self._scale_vars[i].get()
                except (tk.TclError, ValueError):
                    scale = 1.0

                # Read axis value (default to 0 if axis doesn't exist)
                if 0 <= axis_idx < len(joy.axes):
                    axis_value = joy.axes[axis_idx]
                else:
                    axis_value = 0.0

                # Compute target
                self._targets[i] = axis_value * scale

            # Update GUI label
            self._target_labels[i].configure(text=f"{self._targets[i]:+.3f} rad")

        # Publish JointState message
        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name = list(ARM_JOINT_NAMES)
        msg.position = list(self._targets)
        msg.velocity = []
        msg.effort = []
        self._pub.publish(msg)

        # Debug logging (only if modifier button is pressed)
        if joy is not None:
            try:
                debug_button_idx = self._debug_button_var.get()
            except tk.TclError:
                debug_button_idx = DEFAULT_DEBUG_BUTTON

            if 0 <= debug_button_idx < len(joy.buttons):
                if joy.buttons[debug_button_idx] == 1:
                    # Format targets for logging
                    targets_str = ", ".join(
                        f"{name}: {val:+.3f}"
                        for name, val in zip(ARM_JOINT_NAMES, self._targets)
                    )
                    self.get_logger().info(f"Targets: {targets_str}")

    def _on_close(self):
        """Handle window close button."""
        self.get_logger().info("GUI closed. Shutting down...")
        self._root.quit()
        self._root.destroy()
        raise SystemExit(0)

    def shutdown(self):
        """Clean shutdown of GUI."""
        try:
            self._root.quit()
            self._root.destroy()
        except tk.TclError:
            pass


def main():
    rclpy.init()
    node = HybridGuiNode()

    try:
        # Use spin() - the timer callback handles Tkinter updates
        rclpy.spin(node)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        node.get_logger().info("Shutting down Hybrid GUI Node...")
        node.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
