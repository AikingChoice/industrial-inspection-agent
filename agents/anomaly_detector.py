"""
异常检测 Agent
基于规则引擎对传感器数据进行阈值检测，识别异常
"""
import math

from config import THRESHOLDS


def _deviation_ratio(value: float, boundary: float, low: float, high: float) -> float:
    """Return a stable relative deviation, including when a boundary is zero."""
    scale = max(abs(boundary), abs(high - low), 1.0)
    return abs(value - boundary) / scale


def detect_anomaly(state: dict) -> dict:
    """
    检测传感器数据是否异常
    输入 state: {sensor_data: {device_id, sensors: {temperature, vibration, ...}}}
    输出: 追加 anomaly_result 字段
    """
    sensor_data = state["sensor_data"]
    sensors = sensor_data.get("sensors", {})
    anomaly_details = []
    max_severity = "正常"

    for name, value in sensors.items():
        if name not in THRESHOLDS:
            continue

        threshold = THRESHOLDS[name]
        low, high = threshold["min"], threshold["max"]

        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            anomaly_details.append({
                "sensor": name,
                "value": value,
                "threshold": {"min": low, "max": high},
                "type": "invalid",
                "severity": "严重",
                "reason": "传感器读数不是有效有限数值",
            })
            continue

        if value > high:
            ratio = _deviation_ratio(value, high, low, high)
            severity = "严重" if ratio > 0.3 else "警告"
            anomaly_details.append({
                "sensor": name,
                "value": value,
                "threshold": high,
                "type": "high",
                "severity": severity,
            })
        elif value < low:
            ratio = _deviation_ratio(value, low, low, high)
            severity = "严重" if ratio > 0.3 else "警告"
            anomaly_details.append({
                "sensor": name,
                "value": value,
                "threshold": low,
                "type": "low",
                "severity": severity,
            })

    # 判断整体严重程度
    if any(d["severity"] == "严重" for d in anomaly_details):
        max_severity = "严重"
    elif anomaly_details:
        max_severity = "警告"

    is_anomaly = len(anomaly_details) > 0

    result = {
        "is_anomaly": is_anomaly,
        "anomaly_count": len(anomaly_details),
        "anomaly_details": anomaly_details,
        "overall_severity": max_severity,
    }

    print(f"[Agent-异常检测] {sensor_data['device_id']}: "
          f"{'发现异常' if is_anomaly else '正常'} "
          f"({len(anomaly_details)} 项, 严重程度: {max_severity})")

    return {"anomaly_result": result}
