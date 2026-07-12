"""
FastAPI 接口
提供 RESTful API 运行巡检流程，支持单次巡检和批量巡检
支持文档上传入库，扩充 RAG 知识库
"""
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from graph.workflow import run_inspection
from mqtt.simulator import generate_sensor_reading, generate_batch
from rag.knowledge_base import FaultKnowledgeBase
from config import UPLOAD_DIR, CHUNK_SIZE, CHUNK_OVERLAP

app = FastAPI(
    title="工业设备智能巡检 Agent API",
    description="基于 LangGraph 多智能体编排的工业设备巡检系统",
    version="1.0.0",
)


# ========== 数据模型 ==========

class SensorData(BaseModel):
    device_id: str
    sensors: dict[str, float]
    timestamp: Optional[str] = None

class InspectionResponse(BaseModel):
    device_id: str
    is_anomaly: bool
    anomaly_count: int
    overall_severity: str
    diagnosis: Optional[dict] = None
    work_order: Optional[dict] = None
    notification_sent: bool


# ========== 接口 ==========

@app.get("/")
def root():
    return {
        "service": "工业设备智能巡检 Agent",
        "version": "1.0.0",
        "endpoints": {
            "POST /inspect": "提交传感器数据进行巡检",
            "POST /inspect/simulate": "使用模拟数据巡检",
            "POST /inspect/batch": "批量模拟巡检",
            "GET /health": "健康检查",
        },
    }


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.post("/inspect", response_model=InspectionResponse)
def inspect(data: SensorData):
    """提交传感器数据，运行完整巡检流程"""
    sensor_data = {
        "device_id": data.device_id,
        "sensors": data.sensors,
        "timestamp": data.timestamp or datetime.now().isoformat(),
    }
    result = run_inspection(sensor_data)
    anomaly = result.get("anomaly_result", {})
    return InspectionResponse(
        device_id=data.device_id,
        is_anomaly=anomaly.get("is_anomaly", False),
        anomaly_count=anomaly.get("anomaly_count", 0),
        overall_severity=anomaly.get("overall_severity", "正常"),
        diagnosis=result.get("diagnosis"),
        work_order=result.get("work_order"),
        notification_sent=result.get("notification_sent", False),
    )


@app.post("/inspect/simulate")
def inspect_simulate():
    """使用模拟数据运行单次巡检"""
    reading = generate_sensor_reading("CNC-Machine-01")
    result = run_inspection(reading)
    return {
        "sensor_data": reading,
        "anomaly_result": result.get("anomaly_result"),
        "diagnosis": result.get("diagnosis"),
        "work_order": result.get("work_order"),
    }


@app.post("/inspect/batch")
def inspect_batch(count: int = 5):
    """批量模拟巡检"""
    readings = generate_batch(min(count, 20))
    results = []
    for reading in readings:
        result = run_inspection(reading)
        results.append({
            "device_id": reading["device_id"],
            "is_anomaly": result.get("anomaly_result", {}).get("is_anomaly", False),
            "work_order_id": result.get("work_order", {}).get("order_id"),
            "fault_type": result.get("diagnosis", {}).get("fault_type"),
        })
    summary = {
        "total": len(results),
        "anomalies": sum(1 for r in results if r["is_anomaly"]),
        "work_orders": sum(1 for r in results if r["work_order_id"]),
    }
    return {"summary": summary, "details": results}


# ========== 知识库管理 ==========

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md"}


@app.post("/knowledge/upload")
async def upload_document(file: UploadFile = File(...)):
    """
    上传文档到知识库
    支持格式：PDF、Word (.docx)、TXT、Markdown
    """
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {suffix}，支持: {', '.join(SUPPORTED_EXTENSIONS)}",
        )

    # 保存到临时目录
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    temp_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}{suffix}")

    try:
        content = await file.read()
        with open(temp_path, "wb") as f:
            f.write(content)

        # 解析 + 入库
        kb = FaultKnowledgeBase(mode="chromadb")
        kb.load_cases()
        chunk_count = kb.ingest_file(temp_path, CHUNK_SIZE, CHUNK_OVERLAP)

        return {
            "status": "success",
            "filename": file.filename,
            "chunks_added": chunk_count,
            "message": f"文档 '{file.filename}' 已成功解析为 {chunk_count} 个分块并写入知识库",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文档处理失败: {str(e)}")

    finally:
        # 清理临时文件
        if os.path.exists(temp_path):
            os.remove(temp_path)


@app.get("/knowledge/stats")
def knowledge_stats():
    """查看知识库统计信息"""
    try:
        kb = FaultKnowledgeBase(mode="chromadb")
        kb.load_cases()
        stats = kb.get_stats()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取统计失败: {str(e)}")