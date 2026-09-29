# bno085_imu

BNO085 IMU driver node (I2C).
Reads Game Rotation Vector / calibrated gyroscope / accelerometer and publishes `sensor_msgs/Imu`.

The IMU is not required for driving, so the node does not exit on I2C errors — it keeps retrying the connection.
Wire it to a **separate I2C bus** from the PCA9685 (see [docs/hardware.md](../../../docs/hardware.md)).

## Node: `bno085_imu_node`

### Published Topics

| Topic | Type | Description |
|-------|------|-------------|
| `/imu/data` | `sensor_msgs/Imu` | Orientation (Game Rotation Vector, no magnetometer), angular velocity (rad/s), acceleration incl. gravity (m/s²). `frame_id = imu_link` |

`bno085_imu.launch.py` also publishes the static TF `base_link -> imu_link`.

### Parameters

| Name | Default | Description |
|------|---------|-------------|
| `i2c_bus` | `1` | I2C bus number (`/dev/i2c-N`) |
| `i2c_address` | `0x4A` | `0x4B` when DI is pulled high |
| `frame_id` | `imu_link` | |
| `topic` | `/imu/data` | |
| `rate_hz` | `100.0` | Sensor report rate |
| `orientation_stddev` | `0.02` | rad |
| `angular_velocity_stddev` | `0.01` | rad/s |
| `linear_acceleration_stddev` | `0.1` | m/s² |
| `max_consecutive_errors` | `10` | Reconnect after this many read errors in a row |
| `stale_timeout_sec` | `1.0` | Reconnect if data stops updating |
| `reconnect_interval_sec` | `2.0` | Retry interval while disconnected |

Launch arguments `imu_x`, `imu_y`, `imu_z` (m) and `imu_roll`, `imu_pitch`, `imu_yaw` (rad) set the mounting pose relative to `base_link`.

### Usage

```bash
i2cdetect -y -r 1          # 0x4A should appear
ros2 launch bno085_imu bno085_imu.launch.py imu_x:=0.05
ros2 topic hz /imu/data
```

### Dependencies

```bash
pip3 install adafruit-circuitpython-bno08x adafruit-extended-bus
```
