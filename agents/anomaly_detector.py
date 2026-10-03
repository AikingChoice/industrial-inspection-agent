"""
异常检测 Agent
基于规则引擎对传感器数据进行阈值检测，识别异常
"""
import math

from config import APP_ENV, REQUIRED_SENSORS, THRESHOLD_CONFIG_SHA256, THRESHOLD_PROFILE_ID, THRESHOLDS


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
    sensor_units = sensor_data.get("sensor_units", {})
    operating_state = sensor_data.get("operating_state")
    anomaly_details = []
    data_quality_issues = []
    max_severity = "正常"

    missing_sensors = sorted(REQUIRED_SENSORS - set(sensors))
    if missing_sensors:
        data_quality_issues.append({
            "type": "missing_required_sensors",
            "sensors": missing_sensors,
        })

    unknown_sensors = sorted(set(sensors) - set(THRESHOLDS))
    if unknown_sensors:
        data_quality_issues.append({
            "type": "unconfigured_sensors",
            "sensors": unknown_sensors,
        })

    if APP_ENV in {"staging", "production"} and not sensor_units:
        data_quality_issues.append({"type": "missing_sensor_units"})

    state_sensitive_sensors = {
        name for name, threshold in THRESHOLDS.items()
        if threshold.get("by_state") and name in sensors
    }
    if state_sensitive_sensors and not operating_state:
        data_quality_issues.append({
            "type": "missing_operating_state",
            "sensors": sorted(state_sensitive_sensors),
        })

    for name, value in sensors.items():
        if name not in THRESHOLDS:
            continue

        threshold = THRESHOLDS[name]
        low, high = threshold["min"], threshold["max"]
        state_limits = threshold.get("by_state", {})
        if state_limits:
            if operating_state not in state_limits:
                data_quality_issues.append({
                    "type": "unconfigured_operating_state",
                    "sensor": name,
                    "operating_state": operating_state,
                })
                continue
            low, high = state_limits[operating_state]["min"], state_limits[operating_state]["max"]

        unit = sensor_units.get(name)
        if unit is not None and unit != threshold["unit"]:
            data_quality_issues.append({
                "type": "unit_mismatch",
                "sensor": name,
                "expected": threshold["unit"],
                "received": unit,
            })
            continue

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

    if data_quality_issues:
        max_severity = "严重"

    is_anomaly = bool(anomaly_details or data_quality_issues)

    result = {
        "is_anomaly": is_anomaly,
        "anomaly_count": len(anomaly_details),
        "anomaly_details": anomaly_details,
        "overall_severity": max_severity,
        "data_quality_ok": not data_quality_issues,
        "data_quality_issues": data_quality_issues,
        "threshold_profile_id": THRESHOLD_PROFILE_ID,
        "threshold_config_sha256": THRESHOLD_CONFIG_SHA256,
    }

    print(f"[Agent-异常检测] {sensor_data['device_id']}: "
          f"{'发现异常' if is_anomaly else '正常'} "
          f"({len(anomaly_details)} 项, 严重程度: {max_severity})")

    return {"anomaly_result": result}
