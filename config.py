"""
工业设备智能巡检 Agent - 配置文件
敏感配置从 .env 文件读取，复制 .env.example 为 .env 并填入真实值
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ========== MQTT 配置 ==========
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "factory/+/sensors"  # 通配符匹配所有设备

# ========== 传感器阈值 ==========
THRESHOLDS = {
    "temperature": {"min": 15, "max": 85, "unit": "°C"},
    "vibration":   {"min": 0,  "max": 12, "unit": "mm/s"},
    "pressure":    {"min": 0.8, "max": 3.5, "unit": "MPa"},
    "rpm":         {"min": 500, "max": 3500, "unit": "r/min"},
}

# ========== LLM 配置 ==========
# 支持 DeepSeek / OpenAI 兼容接口
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# ========== ChromaDB 配置 ==========
CHROMA_PERSIST_DIR = "./chroma_data"
CHROMA_COLLECTION = "fault_cases"

# ========== 文档上传配置 ==========
UPLOAD_DIR = "./uploads"
CHUNK_SIZE = 500       # 文本分块大小（字符数）
CHUNK_OVERLAP = 50     # 分块重叠字符数

# ========== 工单配置 ==========
WORK_ORDER_PREFIX = "WO"
