"""
工业设备智能巡检 Agent - 配置文件
敏感配置从 .env 文件读取，复制 .env.example 为 .env 并填入真实值
"""
import json
import hashlib
import math
import os
import re
from urllib.parse import parse_qs, urlparse
from dotenv import load_dotenv

load_dotenv()

# ========== API 安全配置 ==========
APP_ENV = os.getenv("APP_ENV", "production").strip().lower()
API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "").strip()
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))

if APP_ENV not in {"development", "test", "staging", "production"}:
    raise ValueError("APP_ENV 必须是 development、test、staging 或 production")

if MAX_UPLOAD_BYTES <= 0:
    raise ValueError("MAX_UPLOAD_BYTES 必须大于 0")

if APP_ENV in {"staging", "production"} and len(API_AUTH_TOKEN) < 32:
    raise RuntimeError("staging/production 必须配置至少 32 个字符的 API_AUTH_TOKEN")

THRESHOLD_PROFILE_ID = os.getenv(
    "THRESHOLD_PROFILE_ID", "" if APP_ENV in {"staging", "production"} else "demo-default"
).strip()
FAULT_KNOWLEDGE_VERSION = os.getenv(
    "FAULT_KNOWLEDGE_VERSION", "unversioned-dev" if APP_ENV in {"development", "test"} else ""
).strip()
_allowed_devices_json = os.getenv("ALLOWED_DEVICE_IDS_JSON", "").strip()
REQUIRED_SENSORS = set()

_default_thresholds = {
    "temperature": {"min": 15, "max": 85, "unit": "°C"},
    "vibration": {"min": 0, "max": 12, "unit": "mm/s"},
    "pressure": {"min": 0.8, "max": 3.5, "unit": "MPa"},
    "rpm": {"min": 500, "max": 3500, "unit": "r/min"},
}
_thresholds_json = os.getenv("THRESHOLDS_JSON", "").strip()
_required_sensors_json = os.getenv("REQUIRED_SENSORS_JSON", "").strip()

if APP_ENV in {"staging", "production"} and (
    not _thresholds_json or not _required_sensors_json or not _allowed_devices_json
):
    raise RuntimeError(
        "受控环境必须配置设备白名单、THRESHOLDS_JSON 和 REQUIRED_SENSORS_JSON"
    )

try:
    THRESHOLDS = json.loads(_thresholds_json) if _thresholds_json else _default_thresholds
    _required_sensors = json.loads(_required_sensors_json) if _required_sensors_json else []
    _allowed_devices = json.loads(_allowed_devices_json) if _allowed_devices_json else []
except (json.JSONDecodeError, TypeError) as exc:
    raise ValueError("阈值和必需测点配置必须是有效 JSON") from exc

if not isinstance(_required_sensors, list) or (
    APP_ENV in {"staging", "production"} and not _required_sensors
) or any(
    not isinstance(name, str) or not name for name in _required_sensors
):
    raise ValueError("REQUIRED_SENSORS_JSON 必须是非空测点名称数组")
REQUIRED_SENSORS = set(_required_sensors)
if len(REQUIRED_SENSORS) != len(_required_sensors):
    raise ValueError("REQUIRED_SENSORS_JSON 中不能有重复测点")
if not isinstance(_allowed_devices, list) or (
    APP_ENV in {"staging", "production"} and not _allowed_devices
) or any(
    not isinstance(device_id, str)
    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", device_id)
    for device_id in _allowed_devices
):
    raise ValueError("ALLOWED_DEVICE_IDS_JSON 必须是有效设备标识数组")
ALLOWED_DEVICE_IDS = set(_allowed_devices)
if len(ALLOWED_DEVICE_IDS) != len(_allowed_devices):
    raise ValueError("ALLOWED_DEVICE_IDS_JSON 中不能有重复设备标识")

if not isinstance(THRESHOLDS, dict) or not THRESHOLDS:
    raise ValueError("THRESHOLDS_JSON 必须是非空对象")
for sensor_name, threshold in THRESHOLDS.items():
    if not isinstance(sensor_name, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", sensor_name):
        raise ValueError("测点名称只能包含字母、数字和下划线，最长 64 个字符")
    if not isinstance(threshold, dict) or not {"min", "max", "unit"}.issubset(threshold):
        raise ValueError(f"测点 {sensor_name} 的阈值配置必须包含 min、max 和 unit")
    low, high = threshold["min"], threshold["max"]
    if (
        isinstance(low, bool) or isinstance(high, bool)
        or not isinstance(low, (int, float)) or not isinstance(high, (int, float))
        or not math.isfinite(low) or not math.isfinite(high) or low >= high
        or not isinstance(threshold["unit"], str) or not threshold["unit"].strip()
    ):
        raise ValueError(f"测点 {sensor_name} 的阈值或单位无效")
    state_limits = threshold.get("by_state", {})
    if not isinstance(state_limits, dict):
        raise ValueError(f"测点 {sensor_name} 的 by_state 必须是对象")
    for state_name, limits in state_limits.items():
        if (
            not isinstance(state_name, str) or not state_name.strip()
            or not isinstance(limits, dict) or not {"min", "max"}.issubset(limits)
        ):
            raise ValueError(f"测点 {sensor_name} 的运行状态阈值配置无效")
        state_low, state_high = limits["min"], limits["max"]
        if (
            isinstance(state_low, bool) or isinstance(state_high, bool)
            or not isinstance(state_low, (int, float)) or not isinstance(state_high, (int, float))
            or not math.isfinite(state_low) or not math.isfinite(state_high)
            or state_low >= state_high
        ):
            raise ValueError(f"测点 {sensor_name} 的运行状态阈值无效")

if REQUIRED_SENSORS and not REQUIRED_SENSORS.issubset(THRESHOLDS):
    raise ValueError("REQUIRED_SENSORS_JSON 中的测点必须全部配置阈值和单位")
THRESHOLD_CONFIG_SHA256 = hashlib.sha256(
    json.dumps(
        {
            "profile_id": THRESHOLD_PROFILE_ID,
            "required_sensors": sorted(REQUIRED_SENSORS),
            "thresholds": THRESHOLDS,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
).hexdigest()
if APP_ENV in {"staging", "production"} and not THRESHOLD_PROFILE_ID:
    raise RuntimeError("生产环境必须配置 THRESHOLD_PROFILE_ID 以便审计阈值版本")
if APP_ENV in {"staging", "production"} and (
    THRESHOLD_PROFILE_ID.startswith("demo-")
    or not re.fullmatch(r"[a-fA-F0-9]{64}", FAULT_KNOWLEDGE_VERSION)
):
    raise RuntimeError(
        "staging/production 必须配置非演示 THRESHOLD_PROFILE_ID 和故障案例文件 SHA-256"
    )

# ========== MQTT 配置 ==========
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "factory/+/sensors"  # 通配符匹配所有设备

# ========== 传感器阈值 ==========
# ========== LLM 配置 ==========
# 支持 DeepSeek / OpenAI 兼容接口
ENABLE_LLM_DIAGNOSIS = os.getenv(
    "ENABLE_LLM_DIAGNOSIS", "false" if APP_ENV in {"staging", "production"} else "true"
).strip().lower() in {"1", "true", "yes", "on"}
ENABLE_VECTOR_SEARCH = os.getenv(
    "ENABLE_VECTOR_SEARCH", "false" if APP_ENV in {"staging", "production"} else "true"
).strip().lower() in {"1", "true", "yes", "on"}
if APP_ENV in {"staging", "production"} and ENABLE_VECTOR_SEARCH:
    raise RuntimeError("受控环境暂不支持 Chroma/Ollama 向量检索；请使用离线关键词检索")
LLM_BASE_URL = os.getenv(
    "LLM_BASE_URL", "" if APP_ENV in {"staging", "production"} else "https://api.deepseek.com"
).strip()
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "15"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if LLM_TIMEOUT_SECONDS <= 0 or not math.isfinite(LLM_TIMEOUT_SECONDS):
    raise ValueError("LLM_TIMEOUT_SECONDS 必须是有限正数")
if APP_ENV in {"staging", "production"} and ENABLE_LLM_DIAGNOSIS and not LLM_BASE_URL:
    raise RuntimeError("启用生产 LLM 诊断时必须显式配置经审批的 LLM_BASE_URL")
LLM_APPROVED_HOSTS = {
    host.strip().lower()
    for host in os.getenv("LLM_APPROVED_HOSTS", "").split(",")
    if host.strip()
}
if APP_ENV in {"staging", "production"} and ENABLE_LLM_DIAGNOSIS:
    llm_url = urlparse(LLM_BASE_URL)
    llm_host = (llm_url.hostname or "").lower()
    if not llm_host or llm_host not in LLM_APPROVED_HOSTS:
        raise RuntimeError("生产 LLM 端点主机必须显式列入 LLM_APPROVED_HOSTS")
    if llm_url.scheme != "https" and llm_host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("生产环境的非本机 LLM 端点必须使用 HTTPS")
if APP_ENV in {"staging", "production"}:
    database_url = urlparse(DATABASE_URL)
    ssl_modes = parse_qs(database_url.query).get("sslmode", [])
    if (
        database_url.scheme not in {"postgres", "postgresql"}
        or not database_url.hostname
        or not database_url.path.strip("/")
        or ssl_modes != ["verify-full"]
    ):
        raise RuntimeError("生产环境必须配置启用 sslmode=verify-full 的 PostgreSQL DATABASE_URL")

# ========== ChromaDB 配置 ==========
CHROMA_PERSIST_DIR = "./chroma_data"
CHROMA_COLLECTION = "fault_cases"

# ========== 文档上传配置 ==========
UPLOAD_DIR = "./uploads"
CHUNK_SIZE = 500       # 文本分块大小（字符数）
CHUNK_OVERLAP = 50     # 分块重叠字符数

# ========== 工单配置 ==========
WORK_ORDER_PREFIX = "WO"
