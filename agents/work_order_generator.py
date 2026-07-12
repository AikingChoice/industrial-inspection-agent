"""
工单生成 Agent
根据诊断结果自动生成维修工单
"""
import uuid
from datetime import datetime
from config import WORK_ORDER_PREFIX


# 优先级映射
SEVERITY_PRIORITY = {
    "严重": "紧急",
    "警告": "高",
    "低": "中",
}

# 维修班组分配（按设备类型）
ASSIGNEE_MAP = {
    "CNC-Machine": "机械维修A组",
    "RoboticArm": "自动化维修B组",
    "default": "综合维修组",
}


def _get_assignee(device_id: str) -> str:
    for prefix, team in ASSIGNEE_MAP.items():
        if prefix in device_id:
            return team
    return ASSIGNEE_MAP["default"]


def generate_work_order(state: dict) -> dict:
    """
    根据诊断结果生成维修工单
    输入 state: {sensor_data, anomaly_result, diagnosis}
    输出: 追加 work_order 字段
    """
    sensor_data = state["sensor_data"]
    anomaly_result = state["anomaly_result"]
    diagnosis = state["diagnosis"]

    device_id = sensor_data["device_id"]
    severity = anomaly_result.get("overall_severity", "警告")
    fault_type = diagnosis.get("fault_type", "未知故障")
    solution = diagnosis.get("solution", "待人工排查")

    work_order = {
        "order_id": f"{WORK_ORDER_PREFIX}-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}",
        "created_at": datetime.now().isoformat(),
        "device_id": device_id,
        "priority": SEVERITY_PRIORITY.get(severity, "中"),
        "fault_type": fault_type,
        "severity": severity,
        "description": f"设备 {device_id} 检测到 {fault_type}，严重程度: {severity}",
        "diagnosis_detail": diagnosis.get("cause", ""),
        "recommended_action": solution,
        "assignee": _get_assignee(device_id),
        "status": "待处理",
        "sensor_snapshot": sensor_data.get("sensors", {}),
    }

    print(f"[Agent-工单生成] 工单 {work_order['order_id']} 已创建")
    print(f"  优先级: {work_order['priority']} | 指派: {work_order['assignee']}")
    print(f"  故障: {fault_type} | 建议: {solution[:60]}...")

    return {"work_order": work_order}

