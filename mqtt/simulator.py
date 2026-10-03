"""
MQTT 传感器数据模拟器
模拟工厂设备的温度、振动、压力、转速数据，随机注入异常
"""
import json
import random
import time
from datetime import datetime, timezone


# 模拟设备列表
DEVICES = ["CNC-Machine-01", "CNC-Machine-02", "RoboticArm-03"]

# 正常范围基准
NORMAL_RANGES = {
    "temperature": (45, 75),    # °C
    "vibration":   (1.0, 6.0),  # mm/s
    "pressure":    (1.2, 2.8),  # MPa
    "rpm":         (1200, 2800),# r/min
}

SENSOR_UNITS = {
    "temperature": "°C",
    "vibration": "mm/s",
    "pressure": "MPa",
    "rpm": "r/min",
}

# 异常注入概率
ANOMALY_PROBABILITY = 0.15  # 15% 概率产生异常数据


def generate_sensor_reading(device_id: str) -> dict:
    """生成单条传感器数据，有一定概率产生异常"""
    is_anomaly = random.random() < ANOMALY_PROBABILITY

    reading = {
        "device_id": device_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sensors": {},
        "sensor_units": SENSOR_UNITS,
    }

    for sensor, (low, high) in NORMAL_RANGES.items():
        if is_anomaly and random.random() < 0.6:
            # 异常：超出正常范围
            if random.random() < 0.5:
                value = round(random.uniform(high, high * 1.5), 2)
            else:
                value = round(random.uniform(low * 0.3, low), 2)
        else:
            # 正常值 + 小幅波动
            value = round(random.uniform(low, high) + random.gauss(0, 0.5), 2)

        reading["sensors"][sensor] = value

    reading["is_anomaly_ground_truth"] = is_anomaly  # 仅用于调试，实际场景不会有
    return reading


def run_simulator(mqtt_client=None, interval: float = 2.0, count: int = None):
    """
    运行模拟器
    - mqtt_client: 如果提供 MQTT 客户端，则发布到 MQTT broker
    - interval: 数据发送间隔（秒）
    - count: 发送次数，None 为无限
    """
    sent = 0
    try:
        while True:
            device = random.choice(DEVICES)
            reading = generate_sensor_reading(device)
            topic = f"factory/{device}/sensors"
            payload = json.dumps(reading, ensure_ascii=False)

            if mqtt_client:
                mqtt_client.publish(topic, payload)
                print(f"[MQTT] {topic} -> {payload}")
            else:
                # 无 MQTT 时直接 yield 给调用方
                yield reading

            sent += 1
            if count and sent >= count:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n模拟器已停止")


def generate_batch(n: int = 10) -> list[dict]:
    """批量生成测试数据（不经过 MQTT）"""
    return [generate_sensor_reading(random.choice(DEVICES)) for _ in range(n)]
