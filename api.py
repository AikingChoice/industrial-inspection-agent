"""
FastAPI 接口
提供 RESTful API 运行巡检流程，支持单次巡检和批量巡检
开发环境支持文档上传入库，扩充 RAG 知识库
"""
import logging
import math
import os
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Depends, Query, Header
from fastapi.security import APIKeyHeader
from starlette.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from graph.workflow import run_inspection
from mqtt.simulator import generate_sensor_reading, generate_batch
from rag.knowledge_base import FaultKnowledgeBase
from config import (
    API_AUTH_TOKEN,
    ALLOWED_DEVICE_IDS,
    APP_ENV,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    ENABLE_VECTOR_SEARCH,
    FAULT_KNOWLEDGE_VERSION,
    MAX_UPLOAD_BYTES,
    THRESHOLD_CONFIG_SHA256,
    UPLOAD_DIR,
)
from storage import EventConflict, EventUnavailable, claim_event, complete_event, fail_event, verify_storage

logger = logging.getLogger(__name__)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class _PayloadTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Bound request bodies before multipart parsing can spool them to disk."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        limit = MAX_UPLOAD_BYTES + 1024 * 1024 if path == "/knowledge/upload" else 1024 * 1024
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                declared_length = int(content_length)
                if declared_length < 0:
                    raise ValueError
                if declared_length > limit:
                    response = JSONResponse(status_code=413, content={"detail": "Request body is too large"})
                    await response(scope, receive, send)
                    return
            except ValueError:
                response = JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
                await response(scope, receive, send)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _PayloadTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _PayloadTooLarge:
            response = JSONResponse(status_code=413, content={"detail": "Request body is too large"})
            await response(scope, receive, send)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if APP_ENV in {"staging", "production"}:
        verify_storage()
    yield

app = FastAPI(
    title="工业设备智能巡检 Agent API",
    description="基于 LangGraph 多智能体编排的工业设备巡检系统",
    version="1.0.0",
    docs_url=None if APP_ENV in {"staging", "production"} else "/docs",
    redoc_url=None if APP_ENV in {"staging", "production"} else "/redoc",
    openapi_url=None if APP_ENV in {"staging", "production"} else "/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(RequestBodyLimitMiddleware)


def require_api_key(api_key: Optional[str] = Depends(api_key_header)) -> None:
    """生产环境保护会触发巡检或修改知识库的接口。"""
    if not API_AUTH_TOKEN:
        if APP_ENV in {"staging", "production"}:
            # config.py 会提前拒绝此配置；这里保留 fail-closed 保护。
            raise HTTPException(status_code=503, detail="API 身份验证未配置")
        return
    if api_key is None or not secrets.compare_digest(api_key, API_AUTH_TOKEN):
        raise HTTPException(status_code=401, detail="API Key 无效")


def require_non_production() -> None:
    if APP_ENV in {"staging", "production"}:
        raise HTTPException(status_code=404, detail="Not found")


# ========== 数据模型 ==========

class SensorData(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)

    device_id: str = Field(min_length=1, max_length=128)
    sensors: dict[str, float]
    sensor_units: Optional[dict[str, str]] = None
    timestamp: Optional[datetime] = None
    operating_state: Optional[str] = Field(default=None, max_length=64)
    job_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("sensors", mode="before")
    @classmethod
    def validate_sensor_values(cls, sensors: dict) -> dict:
        if not isinstance(sensors, dict) or not 1 <= len(sensors) <= 32:
            raise ValueError("sensors 必须包含 1 到 32 个测点")
        for value in sensors.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("传感器值必须是有限数字，不能是布尔值")
        return sensors

    @field_validator("sensors")
    @classmethod
    def validate_sensor_names(cls, sensors: dict[str, float]) -> dict[str, float]:
        for name in sensors:
            if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", name):
                raise ValueError("传感器名称只能包含字母、数字和下划线")
        return sensors

    @model_validator(mode="after")
    def validate_sensor_units(self):
        if ALLOWED_DEVICE_IDS and self.device_id not in ALLOWED_DEVICE_IDS:
            raise ValueError("device_id 未在此试点的设备白名单中")
        if self.sensor_units is None:
            if APP_ENV in {"staging", "production"}:
                raise ValueError("生产环境必须为每个测点提供 sensor_units")
            return self
        if set(self.sensor_units) != set(self.sensors):
            raise ValueError("sensor_units 的测点必须与 sensors 完全对应")
        if any(not unit.strip() or len(unit) > 32 for unit in self.sensor_units.values()):
            raise ValueError("测点单位不能为空且不能超过 32 个字符")
        return self

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, timestamp: Optional[datetime]) -> Optional[datetime]:
        if timestamp is not None and timestamp.utcoffset() is None:
            raise ValueError("timestamp 必须包含时区，例如 2026-10-03T10:00:00Z")
        return timestamp

class InspectionResponse(BaseModel):
    device_id: str
    is_anomaly: bool
    anomaly_count: int
    overall_severity: str
    diagnosis: Optional[dict] = None
    work_order: Optional[dict] = None
    notification_sent: bool
    review_required: bool
    data_quality_ok: bool
    data_quality_issues: list[dict]
    threshold_profile_id: Optional[str] = None
    threshold_config_sha256: str
    knowledge_base_version: str


# ========== 接口 ==========

@app.get("/")
def root():
    endpoints = {
        "POST /inspect": "提交带唯一事件 ID 的传感器数据进行巡检",
        "GET /health": "进程存活检查",
        "GET /ready": "依赖就绪检查",
    }
    if APP_ENV not in {"staging", "production"}:
        endpoints.update({
            "POST /inspect/simulate": "使用模拟数据巡检",
            "POST /inspect/batch": "批量模拟巡检",
            "POST /knowledge/upload": "开发环境知识库文档上传",
        })
    return {
        "service": "工业设备智能巡检 Agent",
        "version": "1.0.0",
        "endpoints": endpoints,
    }


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/ready")
def ready():
    if APP_ENV in {"staging", "production"}:
        try:
            verify_storage()
        except EventUnavailable as exc:
            raise HTTPException(status_code=503, detail="Required storage is unavailable") from exc
    return {"status": "ready"}


@app.post("/inspect", response_model=InspectionResponse, dependencies=[Depends(require_api_key)])
def inspect(data: SensorData, event_id: Optional[str] = Header(default=None, alias="X-Event-ID")):
    """提交传感器数据，运行完整巡检流程"""
    sensor_data = {
        "device_id": data.device_id,
        "event_id": event_id,
        "sensors": data.sensors,
        "sensor_units": data.sensor_units or {},
        "timestamp": (data.timestamp or datetime.now(timezone.utc)).isoformat(),
        "operating_state": data.operating_state,
        "job_id": data.job_id,
    }
    if APP_ENV in {"staging", "production"}:
        if event_id is None or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", event_id):
            raise HTTPException(status_code=400, detail="生产请求必须提供有效的 X-Event-ID")
        try:
            replay = claim_event(event_id, sensor_data)
        except EventConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except EventUnavailable as exc:
            logger.exception("Inspection storage could not claim event")
            raise HTTPException(status_code=503, detail="Inspection storage is unavailable") from exc
        if replay is not None:
            return replay

    try:
        result = run_inspection(sensor_data)
    except Exception as exc:
        if APP_ENV in {"staging", "production"} and event_id:
            fail_event(event_id, "inspection_failed")
        logger.exception("Inspection processing failed")
        raise HTTPException(status_code=503, detail="Inspection processing failed; operator review may be required") from exc
    anomaly = result.get("anomaly_result", {})
    response = InspectionResponse(
        device_id=data.device_id,
        is_anomaly=anomaly.get("is_anomaly", False),
        anomaly_count=anomaly.get("anomaly_count", 0),
        overall_severity=anomaly.get("overall_severity", "正常"),
        diagnosis=result.get("diagnosis"),
        work_order=result.get("work_order"),
        notification_sent=False,
        data_quality_ok=anomaly.get("data_quality_ok", True),
        data_quality_issues=anomaly.get("data_quality_issues", []),
        review_required=(
            not anomaly.get("data_quality_ok", True)
            or bool((result.get("diagnosis") or {}).get("need_manual_review", False))
        ),
        threshold_profile_id=anomaly.get("threshold_profile_id"),
        threshold_config_sha256=anomaly.get("threshold_config_sha256", THRESHOLD_CONFIG_SHA256),
        knowledge_base_version=FAULT_KNOWLEDGE_VERSION,
    )
    if APP_ENV in {"staging", "production"} and event_id:
        try:
            complete_event(event_id, response.model_dump(mode="json"))
        except EventUnavailable as exc:
            logger.exception("Inspection response could not be persisted")
            raise HTTPException(status_code=503, detail="Inspection result could not be durably stored") from exc
    return response


@app.post("/inspect/simulate", dependencies=[Depends(require_api_key), Depends(require_non_production)])
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


@app.post("/inspect/batch", dependencies=[Depends(require_api_key), Depends(require_non_production)])
def inspect_batch(count: int = Query(default=5, ge=1, le=20)):
    """批量模拟巡检"""
    readings = generate_batch(count)
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


@app.post("/knowledge/upload", dependencies=[Depends(require_api_key), Depends(require_non_production)])
async def upload_document(file: UploadFile = File(...)):
    """
    上传文档到知识库
    支持格式：PDF、Word (.docx)、TXT、Markdown
    """
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {suffix}，支持: {', '.join(SUPPORTED_EXTENSIONS)}",
        )

    # 保存到临时目录
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    temp_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}{suffix}")

    try:
        total_bytes = 0
        with open(temp_path, "wb") as f:
            while chunk := await file.read(64 * 1024):
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过大小限制（{MAX_UPLOAD_BYTES} 字节）",
                    )
                f.write(chunk)

        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="上传文件不能为空")

        # 解析 + 入库
        kb = FaultKnowledgeBase(mode="chromadb")
        kb.load_cases()
        chunk_count = kb.ingest_file(temp_path, CHUNK_SIZE, CHUNK_OVERLAP)

        return {
            "status": "success",
            "filename": Path(filename).name,
            "chunks_added": chunk_count,
            "message": f"文档已成功解析为 {chunk_count} 个分块并写入知识库",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("知识库文档处理失败")
        raise HTTPException(status_code=500, detail="文档处理失败，请检查服务日志") from e

    finally:
        # 清理临时文件
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                logger.exception("临时知识库文件清理失败")


@app.get("/knowledge/stats", dependencies=[Depends(require_api_key)])
def knowledge_stats():
    """查看知识库统计信息"""
    try:
        kb = FaultKnowledgeBase(mode="chromadb" if ENABLE_VECTOR_SEARCH else "keyword")
        kb.load_cases()
        stats = kb.get_stats()
        return stats
    except Exception as e:
        logger.exception("获取知识库统计失败")
        raise HTTPException(status_code=500, detail="获取统计失败，请检查服务日志") from e
