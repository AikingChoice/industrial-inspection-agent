"""
巡检日志记录器
每次巡检结果写入 JSON Lines 文件，便于后续分析和审计
"""
import json
import logging
from datetime import datetime
from pathlib import Path

# 日志文件路径
LOG_DIR = Path(__file__).parent / "logs"
LOG_FILE = LOG_DIR / "inspection.jsonl"

# 配置控制台日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("inspection")


def log_inspection(result: dict):
    """
    将巡检结果追加写入 JSONL 文件
    每行一条 JSON 记录，方便用 pandas/工具链分析
    """
    LOG_DIR.mkdir(exist_ok=True)

    diagnosis = result.get("diagnosis") or {}
    work_order = result.get("work_order") or {}

    record = {
        "timestamp": datetime.now().isoformat(),
        "device_id": result.get("sensor_data", {}).get("device_id", "unknown"),
        "is_anomaly": result.get("anomaly_result", {}).get("is_anomaly", False),
        "anomaly_count": result.get("anomaly_result", {}).get("anomaly_count", 0),
        "severity": result.get("anomaly_result", {}).get("overall_severity", "正常"),
        "fault_type": diagnosis.get("fault_type"),
        "confidence": diagnosis.get("confidence"),
        "llm_used": diagnosis.get("llm_used"),
        "need_manual_review": diagnosis.get("need_manual_review", False),
        "work_order_id": work_order.get("order_id"),
        "priority": work_order.get("priority"),
        "assignee": work_order.get("assignee"),
    }

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info(
        f"巡检记录: 设备={record['device_id']} "
        f"异常={record['is_anomaly']} "
        f"故障={record['fault_type'] or '-'} "
        f"工单={record['work_order_id'] or '-'}"
    )
