"""
工单生成 Agent
根据诊断结果生成待人工审核的维修工单草稿
"""
import uuid
from datetime import datetime, timezone
from config import APP_ENV, FAULT_KNOWLEDGE_VERSION, THRESHOLD_CONFIG_SHA256, WORK_ORDER_PREFIX


# 优先级映射
SEVERITY_PRIORITY = {
    "严重": "紧急",
    "警告": "高",
    "低": "中",
}

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
    suggested_priority = SEVERITY_PRIORITY.get(severity, "中")
    priority = (
        "待人工定级"
        if APP_ENV in {"staging", "production"}
        else suggested_priority
    )

    work_order = {
        "order_id": f"{WORK_ORDER_PREFIX}-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:12].upper()}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "device_id": device_id,
        "event_id": sensor_data.get("event_id"),
        "priority": priority,
        "suggested_priority": suggested_priority,
        "fault_type": fault_type,
        "severity": severity,
        "description": f"设备 {device_id} 出现异常信号，候选诊断为 {fault_type}；需由设备工程师确认",
        "diagnosis_detail": diagnosis.get("cause", ""),
        "recommended_action": solution,
        "assignee": None,
        "status": "待处理",
        "approval_status": "待人工审核",
        "is_draft": True,
        "human_approval_required": True,
        "sensor_snapshot": sensor_data.get("sensors", {}),
        "sensor_units": sensor_data.get("sensor_units", {}),
        "threshold_profile_id": anomaly_result.get("threshold_profile_id"),
        "threshold_config_sha256": THRESHOLD_CONFIG_SHA256,
        "knowledge_base_version": FAULT_KNOWLEDGE_VERSION,
        "evidence": diagnosis.get("matched_cases", []),
    }

    print(f"[Agent-工单生成] 草稿 {work_order['order_id']} 已生成，等待人工审核")
    print(f"  优先级: {work_order['priority']} | 指派: 待人工分派")
    print(f"  故障: {fault_type} | 建议: {solution[:60]}...")

    return {"work_order": work_order}

