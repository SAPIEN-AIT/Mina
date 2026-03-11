"""
Diagnostic instrumentation for play_isaacsim.py

WHAT THIS MEASURES:
  1. Sensor update rate — how often /joint_states actually delivers NEW data
     (not just how often the callback fires, but when positions actually change)
  2. PD publish rate — confirms our 250Hz timer is actually firing at 250Hz
  3. Torque-to-sensor latency — time between publishing a torque and seeing
     the joint state change in response
  4. Sensor staleness — how many PD ticks reuse the exact same sensor data

HOW TO USE:
  Patch these into HumanoidPolicyNode.__init__ and the callbacks.
  After ~10 seconds, hit Ctrl+C and the node will dump a timing report
  to diagnostics_timing.log

  Or: replace play_isaacsim.py's HumanoidPolicyNode with this subclass.

WHAT TO LOOK FOR:
  - If "sensor actual update rate" ≈ 60Hz → OmniGraph ticks at render rate
  - If "stale PD ticks between updates" ≈ 3-4 → 250Hz PD / 60Hz sensor = ~4
  - If "stale PD ticks" ≈ 0 → sensor updates every PD tick (good, unlikely)
  - The key question: does ArticulationController also tick at this same rate?
    If sensor publish = 60Hz and subscribe is on the same OnPlaybackTick,
    then YES — torques are applied at 60Hz regardless of publish rate.
"""

import time
import numpy as np
from collections import deque


class TimingDiagnostics:
    """Drop-in diagnostics mixin. Add to HumanoidPolicyNode.__init__."""
    
    def init_diagnostics(self):
        """Call this at the end of HumanoidPolicyNode.__init__"""
        # --- Sensor update tracking ---
        self._diag_last_joint_pos = None          # previous /joint_states positions
        self._diag_sensor_change_times = deque(maxlen=5000)  # wall-clock of actual changes
        self._diag_sensor_cb_times = deque(maxlen=5000)      # wall-clock of every callback
        
        # --- PD loop tracking ---
        self._diag_pd_times = deque(maxlen=5000)       # wall-clock of every PD tick
        self._diag_pd_stale_count = 0                   # consecutive PD ticks with stale data
        self._diag_pd_stale_runs = deque(maxlen=5000)   # length of each stale run
        
        # --- Torque tracking ---
        self._diag_last_torque_time = None
        self._diag_torque_to_change_latencies = deque(maxlen=5000)
        
        # --- Policy tracking ---
        self._diag_policy_times = deque(maxlen=5000)
        
        # --- Position delta tracking (detect if physics is actually stepping) ---
        self._diag_pos_deltas = deque(maxlen=5000)   # magnitude of position change per sensor update
        
        self._diag_start_time = time.monotonic()

    def diag_on_joint_states(self, positions_array: np.ndarray):
        """Call this INSIDE _joint_states_callback, after updating self._joint_pos.
        
        Args:
            positions_array: the raw np.array of positions from the message
                             (in robot order, before reordering)
        """
        now = time.monotonic()
        self._diag_sensor_cb_times.append(now)
        
        if self._diag_last_joint_pos is not None:
            delta = np.abs(positions_array - self._diag_last_joint_pos)
            max_delta = float(np.max(delta))
            self._diag_pos_deltas.append(max_delta)
            
            # Did the data actually change? Use a tiny threshold to ignore float noise
            if max_delta > 1e-7:
                self._diag_sensor_change_times.append(now)
                
                # Record how many PD ticks were stale
                if self._diag_pd_stale_count > 0:
                    self._diag_pd_stale_runs.append(self._diag_pd_stale_count)
                self._diag_pd_stale_count = 0
                
                # Torque→sensor latency
                if self._diag_last_torque_time is not None:
                    latency = now - self._diag_last_torque_time
                    self._diag_torque_to_change_latencies.append(latency)
        
        self._diag_last_joint_pos = positions_array.copy()

    def diag_on_pd_tick(self):
        """Call this at the START of _control_callback."""
        now = time.monotonic()
        self._diag_pd_times.append(now)
        self._diag_pd_stale_count += 1  # incremented every tick, reset on sensor change

    def diag_on_torque_publish(self):
        """Call this right AFTER publishing the torque message in _control_callback."""
        self._diag_last_torque_time = time.monotonic()

    def diag_on_policy_tick(self):
        """Call this at the START of _policy_callback."""
        self._diag_policy_times.append(now)

    def dump_diagnostics(self, filepath="diagnostics_timing.log"):
        """Call this in the finally block of main(), or on Ctrl+C."""
        elapsed = time.monotonic() - self._diag_start_time
        
        lines = []
        lines.append("=" * 72)
        lines.append("  TIMING DIAGNOSTICS REPORT")
        lines.append(f"  Duration: {elapsed:.1f}s")
        lines.append("=" * 72)
        
        # --- Sensor callback rate ---
        cb_times = list(self._diag_sensor_cb_times)
        if len(cb_times) > 1:
            cb_intervals = np.diff(cb_times)
            cb_rate = 1.0 / np.mean(cb_intervals)
            lines.append(f"\n/joint_states CALLBACK rate:")
            lines.append(f"  callbacks received:  {len(cb_times)}")
            lines.append(f"  mean interval:       {np.mean(cb_intervals)*1000:.1f} ms")
            lines.append(f"  std interval:        {np.std(cb_intervals)*1000:.1f} ms")
            lines.append(f"  effective rate:      {cb_rate:.1f} Hz")
        
        # --- Sensor ACTUAL update rate (data changed) ---
        change_times = list(self._diag_sensor_change_times)
        if len(change_times) > 1:
            change_intervals = np.diff(change_times)
            change_rate = 1.0 / np.mean(change_intervals)
            lines.append(f"\n/joint_states ACTUAL UPDATE rate (data changed):")
            lines.append(f"  actual updates:      {len(change_times)}")
            lines.append(f"  mean interval:       {np.mean(change_intervals)*1000:.1f} ms")
            lines.append(f"  std interval:        {np.std(change_intervals)*1000:.1f} ms")
            lines.append(f"  effective rate:      {change_rate:.1f} Hz")
            lines.append(f"  ^^^ THIS IS THE KEY NUMBER ^^^")
            lines.append(f"  If ~60 Hz → OmniGraph ticks at render rate")
            lines.append(f"  If ~2000 Hz → sensor updates every physics step")
        
        # --- PD loop rate ---
        pd_times = list(self._diag_pd_times)
        if len(pd_times) > 1:
            pd_intervals = np.diff(pd_times)
            pd_rate = 1.0 / np.mean(pd_intervals)
            lines.append(f"\nPD CONTROL loop rate:")
            lines.append(f"  ticks:               {len(pd_times)}")
            lines.append(f"  mean interval:       {np.mean(pd_intervals)*1000:.1f} ms")
            lines.append(f"  effective rate:      {pd_rate:.1f} Hz")
        
        # --- Staleness ---
        stale_runs = list(self._diag_pd_stale_runs)
        if stale_runs:
            lines.append(f"\nPD STALENESS (PD ticks between sensor updates):")
            lines.append(f"  mean stale run:      {np.mean(stale_runs):.1f} ticks")
            lines.append(f"  max stale run:       {np.max(stale_runs)} ticks")
            lines.append(f"  min stale run:       {np.min(stale_runs)} ticks")
            lines.append(f"  ^^^ If ~4 → 250Hz PD / 60Hz sensor")
            lines.append(f"  ^^^ If ~1 → 250Hz PD / 250Hz sensor (good)")
            
            # Histogram
            bins = [0, 1, 2, 3, 4, 5, 8, 12, 20, 50]
            hist, _ = np.histogram(stale_runs, bins=bins)
            lines.append(f"  distribution:")
            for i in range(len(hist)):
                bar = "#" * min(hist[i], 60)
                lines.append(f"    {bins[i]:>3}-{bins[i+1]:>3} ticks: {hist[i]:>5}  {bar}")
        
        # --- Position deltas ---
        deltas = list(self._diag_pos_deltas)
        if deltas:
            lines.append(f"\nPOSITION CHANGE magnitude per sensor update:")
            lines.append(f"  mean max-delta:      {np.mean(deltas):.6f} rad")
            lines.append(f"  max max-delta:       {np.max(deltas):.6f} rad")
            lines.append(f"  zeros (no change):   {sum(1 for d in deltas if d < 1e-7)}")
        
        # --- Torque→sensor latency ---
        latencies = list(self._diag_torque_to_change_latencies)
        if latencies:
            lines.append(f"\nTORQUE → SENSOR CHANGE latency:")
            lines.append(f"  mean:                {np.mean(latencies)*1000:.1f} ms")
            lines.append(f"  min:                 {np.min(latencies)*1000:.1f} ms")
            lines.append(f"  max:                 {np.max(latencies)*1000:.1f} ms")
            lines.append(f"  ^^^ If ~16ms → one render frame at 60Hz")
            lines.append(f"  ^^^ If ~4ms  → matches PD rate")
        
        lines.append("\n" + "=" * 72)
        lines.append("INTERPRETATION:")
        lines.append("")
        
        if len(change_times) > 1:
            change_rate_val = 1.0 / np.mean(np.diff(change_times))
            if change_rate_val < 100:
                lines.append(f"  Sensor update rate is {change_rate_val:.0f} Hz (< 100 Hz).")
                lines.append(f"  This confirms OmniGraph publishes at RENDER rate, not physics rate.")
                lines.append(f"  Since ROS2SubscribeJointState is on the same OnPlaybackTick,")
                lines.append(f"  ArticulationController ALSO applies torques at ~{change_rate_val:.0f} Hz.")
                lines.append(f"")
                lines.append(f"  YOUR 250Hz PD LOOP IS BEING DECIMATED TO ~{change_rate_val:.0f}Hz.")
                lines.append(f"  The robot sees ~{2000/change_rate_val:.0f}x fewer torque updates than training.")
                lines.append(f"")
                lines.append(f"  OPTIONS:")
                lines.append(f"    1. Move PD control INSIDE Isaac Sim (Script Editor / extension)")
                lines.append(f"    2. Use Isaac Sim's built-in PD drives (set stiffness/damping)")
                lines.append(f"       and send POSITION targets instead of torques")
                lines.append(f"    3. Skip sim2sim, deploy to real robot where CAN bus PD runs at 1kHz+")
            else:
                lines.append(f"  Sensor update rate is {change_rate_val:.0f} Hz — fast enough.")
                lines.append(f"  The bottleneck is likely elsewhere.")
        
        lines.append("=" * 72)
        
        report = "\n".join(lines)
        
        with open(filepath, "w") as f:
            f.write(report)
        
        # Also print to console
        print(report)
        
        return report


# ─────────────────────────────────────────────────────────────────────
# INTEGRATION INSTRUCTIONS
# ─────────────────────────────────────────────────────────────────────
#
# Option A: Minimal patch (add ~10 lines to existing play_isaacsim.py)
#
#   1. Import at top:
#        from diagnostics_patch import TimingDiagnostics
#
#   2. Make HumanoidPolicyNode inherit from both:
#        class HumanoidPolicyNode(Node, TimingDiagnostics):
#
#   3. At END of __init__:
#        self.init_diagnostics()
#
#   4. In _joint_states_callback, AFTER the reorder lines (258-259):
#        self.diag_on_joint_states(np.array(msg.position, dtype=np.float32))
#
#   5. In _control_callback, at the VERY START:
#        self.diag_on_pd_tick()
#
#   6. In _control_callback, AFTER self._pub.publish(msg):
#        self.diag_on_torque_publish()
#
#   7. In main() finally block, before destroy_node():
#        node.dump_diagnostics()
#
# Option B: Quick standalone test (no code changes to play_isaacsim.py)
#   Just run this file directly — it subscribes to /joint_states and
#   measures the update rate from outside.
# ─────────────────────────────────────────────────────────────────────

def standalone_sensor_rate_test():
    """Standalone test: just subscribe to /joint_states and measure timing.
    
    Run with: 
        uv run python diagnostics_patch.py
    
    This doesn't need play_isaacsim.py running — it measures Isaac Sim's
    raw publish rate directly.
    """
    import rclpy
    from rclpy.node import Node as RosNode
    from sensor_msgs.msg import JointState
    
    class SensorRateMonitor(RosNode):
        def __init__(self):
            super().__init__("sensor_rate_monitor")
            self._times = deque(maxlen=5000)
            self._change_times = deque(maxlen=5000)
            self._last_pos = None
            self._start = time.monotonic()
            
            self.create_subscription(
                JointState, "joint_states", self._cb, 10)
            self.get_logger().info("Monitoring /joint_states rate... (Ctrl+C after ~10s)")
        
        def _cb(self, msg):
            now = time.monotonic()
            self._times.append(now)
            
            pos = np.array(msg.position, dtype=np.float32)
            if self._last_pos is not None:
                if np.max(np.abs(pos - self._last_pos)) > 1e-7:
                    self._change_times.append(now)
            self._last_pos = pos.copy()
            
            # Print live stats every 2 seconds
            elapsed = now - self._start
            if len(self._times) > 10 and int(elapsed) % 2 == 0 and len(self._times) % 20 == 0:
                cb_rate = len(self._times) / elapsed
                change_rate = len(self._change_times) / elapsed if self._change_times else 0
                self.get_logger().info(
                    f"  callback rate: {cb_rate:.1f} Hz | "
                    f"actual update rate: {change_rate:.1f} Hz | "
                    f"elapsed: {elapsed:.0f}s")
        
        def dump(self):
            elapsed = time.monotonic() - self._start
            times = list(self._times)
            changes = list(self._change_times)
            
            print(f"\n{'='*50}")
            print(f"  /joint_states RATE REPORT ({elapsed:.1f}s)")
            print(f"{'='*50}")
            
            if len(times) > 1:
                rate = (len(times) - 1) / (times[-1] - times[0])
                print(f"  Callback rate:      {rate:.1f} Hz")
            
            if len(changes) > 1:
                rate = (len(changes) - 1) / (changes[-1] - changes[0])
                print(f"  Actual update rate: {rate:.1f} Hz  ← THIS MATTERS")
            
            print(f"  Total callbacks:    {len(times)}")
            print(f"  Actual changes:     {len(changes)}")
            if times and changes:
                print(f"  Duplicate ratio:    {1 - len(changes)/len(times):.1%}")
            print(f"{'='*50}")
    
    rclpy.init()
    node = SensorRateMonitor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.dump()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    standalone_sensor_rate_test()
