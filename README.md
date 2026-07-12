# 🏭 工业设备智能巡检 Agent

基于 **LangGraph 多智能体编排** + **RAG 知识库检索** + **LLM 推理** 的工业设备巡检系统，实现从传感器数据采集、异常检测、故障诊断到维修工单自动生成的全流程自动化。

---

## 学习目标

通过本项目，你将学习：

1. **LangGraph 状态机编排**
   - 如何用 `StateGraph` 定义多 Agent 工作流
   - 条件路由：根据中间结果动态决定下一步
   - 状态传递：节点间如何共享和更新数据

2. **LangChain 模型调用**
   - 使用 `init_chat_model` 统一接口初始化 LLM
   - `SystemMessage` / `HumanMessage` 消息类型
   - `invoke` 方法调用模型并解析返回值

3. **RAG 检索增强生成**
   - 关键词匹配模式（离线可用，面试演示首选）
   - ChromaDB 向量检索模式（生产环境推荐）
   - 将检索结果注入 LLM Prompt 提升诊断准确率

4. **多 Agent 协作模式**
   - 每个 Agent 单一职责，通过状态图串联
   - 异常检测 → 故障诊断 → 工单生成的流水线
   - 降级容错：LLM 不可用时自动回退到 RAG 匹配

---

## 架构设计

```
传感器数据 (MQTT / 模拟器 / API)
       │
       ▼
┌─────────────────┐
│  异常检测 Agent  │  ← 规则引擎 + 阈值检测
└────────┬────────┘
         │
    ┌────┴────┐
    │ 有异常？ │
    └────┬────┘
    Yes  │  No → END
         ▼
┌─────────────────┐
│  故障诊断 Agent  │  ← RAG 知识库检索 + LLM 推理
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  工单生成 Agent  │  ← 自动派单 + 优先级评估
└────────┬────────┘
         │
         ▼
    通知 & 记录 → END
```

---

## 核心概念详解

### 1. LangGraph 状态图 - 多 Agent 编排

LangGraph 是 LangChain 生态中的**状态机框架**，用于编排多个 Agent 的执行流程。

#### 状态定义

```python
from typing import TypedDict, Optional

class InspectionState(TypedDict):
    """巡检 Agent 的全局状态 - 所有节点共享"""
    sensor_data: dict                    # 原始传感器数据
    anomaly_result: Optional[dict]       # 异常检测结果
    diagnosis: Optional[dict]            # 故障诊断结果
    work_order: Optional[dict]           # 生成的工单
    notification_sent: bool              # 是否已发送通知
```

**关键点：**
- 每个 Agent 从 state 中读取输入，将输出写回 state
- 状态是**不可变传递**的，每个节点返回一个 dict 来更新状态
- `Optional` 表示该字段在流程初期可能为空

#### 节点函数

每个节点是一个普通 Python 函数，接收 state，返回更新：

```python
def detect_anomaly(state: dict) -> dict:
    """异常检测节点 - 从 state 读取传感器数据，返回异常结果"""
    sensors = state["sensor_data"]["sensors"]
    # ... 检测逻辑 ...
    return {"anomaly_result": result}  # 只返回要更新的字段
```

#### 条件路由

根据中间结果动态决定下一步，这是 LangGraph 的核心能力：

```python
from langgraph.graph import StateGraph, END

def should_diagnose(state: dict) -> str:
    """条件路由函数：有异常走诊断，无异常直接结束"""
    if state["anomaly_result"]["is_anomaly"]:
        return "diagnose"
    return "end"

# 构建图
graph = StateGraph(InspectionState)
graph.add_node("detect_anomaly", detect_anomaly)
graph.add_node("diagnose_fault", diagnose_fault)

# 条件边：检测后根据结果路由
graph.add_conditional_edges(
    "detect_anomaly",
    should_diagnose,
    {
        "diagnose": "diagnose_fault",  # 有异常 → 诊断
        "end": END,                     # 无异常 → 结束
    },
)
```

**为什么用条件路由？**
- 正常数据不需要调用 LLM，节省 API 费用
- 流程更清晰，每个分支职责明确
- 易于扩展（比如加一个"紧急异常直接报警"的分支）

---

### 2. init_chat_model - LangChain 统一模型接口

`init_chat_model` 是 LangChain 1.0 推荐的模型初始化方式，一个接口适配所有 LLM 提供商。

#### 基本用法

```python
from langchain.chat_models import init_chat_model

# 格式: "provider:model_name"
model = init_chat_model(
    "openai:deepseek-v4-flash",       # OpenAI 兼容接口
    api_key="sk-xxx",
    base_url="https://api.deepseek.com",
    temperature=0.1,
    max_tokens=800,
)
```

#### 支持的提供商

```python
# DeepSeek (OpenAI 兼容)
init_chat_model("openai:deepseek-v4-flash", base_url="https://api.deepseek.com")

# Groq
init_chat_model("groq:llama-3.3-70b-versatile")

# 本地 Ollama
init_chat_model("ollama:qwen2.5")

# OpenAI
init_chat_model("openai:gpt-4")
```

**切换模型只需改一个字符串，不用动业务代码。**

#### 调用模型

```python
from langchain_core.messages import SystemMessage, HumanMessage

messages = [
    SystemMessage(content="你是工业设备故障诊断专家"),
    HumanMessage(content="温度95°C，振动15mm/s，是什么故障？"),
]

response = model.invoke(messages)
print(response.content)       # AI 回复文本
print(response.usage_metadata) # Token 使用情况
```

#### 对比原生 OpenAI 客户端

```python
# ❌ 原生方式 - 与提供商耦合
from openai import OpenAI
client = OpenAI(base_url="https://api.deepseek.com", api_key="sk-xxx")
response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[{"role": "user", "content": "..."}],
)
raw = response.choices[0].message.content  # 手动取值

# ✅ LangChain 方式 - 统一接口
from langchain.chat_models import init_chat_model
model = init_chat_model("openai:deepseek-v4-flash", api_key="sk-xxx", base_url="...")
response = model.invoke([HumanMessage(content="...")])
raw = response.content  # 直接取值
```

---

### 3. RAG 知识库 - 检索增强生成

RAG (Retrieval-Augmented Generation) 让 LLM 基于知识库回答问题，而不是凭"记忆"瞎猜。

#### 两种检索模式

| 模式 | 原理 | 优点 | 缺点 | 适用场景 |
|------|------|------|------|----------|
| `keyword` | 关键词匹配 + 评分排序 | 离线可用、无需额外依赖 | 匹配精度较低 | 面试演示、快速验证 |
| `chromadb` | 向量嵌入 + 余弦相似度 | 语义匹配、精度高 | 首次需下载嵌入模型 | 生产环境 |

#### 知识库数据结构

```json
{
  "id": "F001",
  "fault_type": "轴承磨损",
  "symptoms": "振动值异常升高(>10mm/s)，伴随温度小幅上升",
  "root_cause": "轴承长期运行导致润滑不足或金属疲劳",
  "solution": "1. 停机检查轴承状态 2. 更换磨损轴承 3. 补充润滑脂",
  "severity": "高",
  "affected_sensors": ["vibration", "temperature"]
}
```

#### RAG + LLM 协作流程

```
传感器异常数据
      │
      ▼
┌──────────────┐
│ RAG 检索     │ ← 从 8 条故障案例中匹配最相关的 3 条
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────────────┐
│ LLM Prompt = 异常描述 + 匹配的历史案例       │
│                                              │
│ "当前异常：温度95°C，振动15mm/s..."          │
│ "历史案例1：轴承磨损，振动>10..."            │
│ "请给出诊断结论..."                          │
└──────────────────────────────────────────────┘
       │
       ▼
  结构化诊断结果 (JSON)
```

**为什么不直接问 LLM？**
- LLM 没有你们工厂的历史故障数据
- RAG 提供了"参考资料"，让 LLM 基于事实推理
- 即使 LLM 调用失败，RAG 匹配结果也能作为兜底

---

### 4. 异常检测 Agent - 规则引擎

基于阈值的规则检测，不需要 LLM，速度快、确定性强：

```python
THRESHOLDS = {
    "temperature": {"min": 15, "max": 85, "unit": "°C"},
    "vibration":   {"min": 0,  "max": 12, "unit": "mm/s"},
    "pressure":    {"min": 0.8, "max": 3.5, "unit": "MPa"},
    "rpm":         {"min": 500, "max": 3500, "unit": "r/min"},
}
```

#### 严重程度判断

```python
ratio = (value - threshold) / threshold
severity = "严重" if ratio > 0.3 else "警告"
```

| 超标程度 | 严重程度 | 说明 |
|----------|----------|------|
| 超出阈值 0-30% | 警告 | 需要关注，可安排计划性检修 |
| 超出阈值 >30% | 严重 | 需要立即处理，可能影响生产安全 |

---

### 5. 工单生成 Agent - 自动派单

根据设备类型自动分配维修班组：

```python
ASSIGNEE_MAP = {
    "CNC-Machine": "机械维修A组",
    "RoboticArm": "自动化维修B组",
    "default": "综合维修组",
}
```

优先级映射：

| 异常严重程度 | 工单优先级 |
|-------------|-----------|
| 严重 | 紧急 |
| 警告 | 高 |
| 低 | 中 |

---

## 完整流程示例

### 输入：传感器数据

```json
{
  "device_id": "CNC-Machine-01",
  "timestamp": "2026-07-12T10:00:00",
  "sensors": {
    "temperature": 95.0,
    "vibration": 15.0,
    "pressure": 0.5,
    "rpm": 2000
  }
}
```

### Agent 1: 异常检测

```
[Agent-异常检测] CNC-Machine-01: 发现异常 (3 项, 严重程度: 严重)
  - temperature: 95.0 > 85 (上限) → 警告
  - vibration: 15.0 > 12 (上限) → 警告
  - pressure: 0.5 < 0.8 (下限) → 严重
```

### Agent 2: 故障诊断

```
[RAG] 已加载 8 条故障案例 (模式: keyword)
[Agent-故障诊断] RAG 检索到 3 条相关案例
[Agent-故障诊断] LLM 诊断: 轴承磨损 (置信度: 85%)
[Agent-故障诊断] 推理: 振动15.0远超阈值，伴随温度升高，符合轴承磨损特征...
```

### Agent 3: 工单生成

```
[Agent-工单生成] 工单 WO-20260712-F72944 已创建
  优先级: 紧急 | 指派: 机械维修A组
  故障: 轴承磨损
  建议: 1. 立即停机，检查轴承状态 2. 补充润滑脂 3. 校准同轴度...
```

### 输出：完整结果

```json
{
  "anomaly_result": {
    "is_anomaly": true,
    "anomaly_count": 3,
    "overall_severity": "严重"
  },
  "diagnosis": {
    "fault_type": "轴承磨损",
    "confidence": 0.85,
    "cause": "轴承长期运行导致润滑不足或金属疲劳",
    "solution": "1. 立即停机 2. 检查轴承状态 3. 更换磨损轴承...",
    "llm_used": true
  },
  "work_order": {
    "order_id": "WO-20260712-F72944",
    "priority": "紧急",
    "assignee": "机械维修A组",
    "status": "待处理"
  }
}
```

---

## 技术栈

| 组件 | 技术 | 作用 |
|------|------|------|
| Agent 编排 | LangGraph | 状态机 + 条件路由 |
| LLM 推理 | LangChain + DeepSeek API | 故障诊断推理 |
| 知识库 | ChromaDB / 关键词匹配 | RAG 检索历史案例 |
| 通信协议 | paho-mqtt | 工业物联网数据接入 |
| API 服务 | FastAPI + Uvicorn | RESTful 接口 |
| 测试 | Pytest | 15 个单元测试 |

---

## 项目结构

```
industrial-inspection-agent/
├── main.py                      # 入口（demo / mqtt / single）
├── api.py                       # FastAPI 接口（巡检 + 知识库管理）
├── config.py                    # 全局配置（从 .env 读取）
├── logger.py                    # 巡检日志记录器（JSONL 格式）
├── requirements.txt
├── .env                         # 敏感配置（不提交 git）
├── .env.example                 # 配置模板
│
├── agents/
│   ├── anomaly_detector.py      # Agent 1: 规则引擎异常检测
│   ├── fault_diagnoser.py       # Agent 2: RAG + LLM 故障诊断（含置信度检查）
│   └── work_order_generator.py  # Agent 3: 工单自动生成
│
├── graph/
│   └── workflow.py              # LangGraph 状态图定义
│
├── mqtt/
│   ├── simulator.py             # 传感器数据模拟器
│   └── subscriber.py            # MQTT 订阅者
│
├── rag/
│   ├── knowledge_base.py        # 故障知识库（keyword / chromadb）
│   ├── document_parser.py       # 文档解析器（PDF/Word/TXT → 分块）
│   └── data/
│       └── fault_cases.json     # 8 种常见工业故障案例
│
├── logs/                        # 巡检日志输出目录
│   └── inspection.jsonl         # JSONL 格式巡检记录
│
└── tests/
    ├── test_core.py             # 核心模块测试（15 个）
    └── test_document.py         # 文档解析测试（11 个）
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API 密钥

复制 `.env.example` 为 `.env`，填入真实配置：

```bash
cp .env.example .env
```

```env
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=sk-xxx
LLM_MODEL=deepseek-v4-flash
```

### 3. 运行演示

```bash
# 模拟数据演示（无需外部服务）
python main.py demo

# 启动 API 服务
uvicorn api:app --reload --port 8000

# 运行测试
pytest tests/ -v
```

> **Windows 用户注意：** 如果遇到 emoji 编码错误，运行时加环境变量：
> ```bash
> PYTHONIOENCODING=utf-8 python main.py demo
> ```

---

## API 接口

启动服务后访问 http://localhost:8000/docs 查看交互式文档。

| 接口 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 服务信息 |
| `/health` | GET | 健康检查 |
| `/inspect` | POST | 提交传感器数据巡检 |
| `/inspect/simulate` | POST | 模拟数据单次巡检 |
| `/inspect/batch?count=5` | POST | 批量模拟巡检 |
| `/knowledge/upload` | POST | 上传文档到知识库（PDF/Word/TXT） |
| `/knowledge/stats` | GET | 查看知识库统计 |

### 调用示例

```bash
# 单次巡检
curl -X POST http://localhost:8000/inspect \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "CNC-Machine-01",
    "sensors": {
      "temperature": 95.0,
      "vibration": 15.0,
      "pressure": 0.5,
      "rpm": 2000
    }
  }'

# 模拟巡检
curl -X POST http://localhost:8000/inspect/simulate

# 批量巡检
curl -X POST "http://localhost:8000/inspect/batch?count=10"

# 上传设备手册到知识库
curl -X POST http://localhost:8000/knowledge/upload -F "file=@设备维护手册.pdf"

# 查看知识库统计
curl http://localhost:8000/knowledge/stats
```

---

## 运行模式

### 模式 1：Demo 演示

```bash
python main.py demo
```

生成 5 条模拟传感器数据，运行完整巡检流程。**无需任何外部服务**，适合快速验证和面试演示。

### 模式 2：MQTT 实时

```bash
python main.py mqtt
```

连接 MQTT Broker，实时处理传感器数据。需要先启动 MQTT Broker（如 Mosquitto）。

### 模式 3：单次巡检

```bash
python main.py single data.json
```

从 JSON 文件读取一条数据运行巡检。

### 模式 4：API 服务

```bash
uvicorn api:app --reload --port 8000
```

启动 RESTful API，支持 HTTP 调用。

---

## 常见问题 (FAQ)

### Q1: LLM 调用失败怎么办？

**A:** 系统自动降级为纯 RAG 关键词匹配结果，不会崩溃。输出中 `llm_used: false` 表示走了降级逻辑。

```python
try:
    response = model.invoke(messages)
    # ... 解析 LLM 结果
except Exception:
    # 降级：直接用 RAG 匹配的最佳案例
    diagnosis = {"fault_type": matched_cases[0]["fault_type"], ...}
```

### Q2: keyword 模式和 chromadb 模式怎么选？

**A:**

| 场景 | 推荐模式 |
|------|----------|
| 面试演示、快速验证 | `keyword`（离线可用，无需下载模型） |
| 生产环境、案例多 | `chromadb`（语义匹配，精度高） |

切换方式：

```python
# keyword 模式（默认）
kb = FaultKnowledgeBase(mode="keyword")

# chromadb 模式
kb = FaultKnowledgeBase(mode="chromadb")
```

### Q3: 如何添加新的故障案例？

**A:** 编辑 `rag/data/fault_cases.json`，按格式添加：

```json
{
  "id": "F009",
  "fault_type": "新故障类型",
  "symptoms": "症状描述",
  "root_cause": "根本原因",
  "solution": "解决方案",
  "severity": "高",
  "affected_sensors": ["temperature"],
  "device_types": ["CNC-Machine"]
}
```

### Q4: 如何切换 LLM 提供商？

**A:** 只需修改 `.env` 文件：

```env
# DeepSeek
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash

# Groq (需要安装 langchain-groq)
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile

# 本地 Ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5
```

### Q5: Windows 下 emoji 报错怎么解决？

**A:** 设置环境变量：

```bash
# 临时设置
set PYTHONIOENCODING=utf-8

# 或在 PowerShell 中
$env:PYTHONIOENCODING="utf-8"
```

### Q6: 测试怎么跑？

**A:**

```bash
# 运行全部测试（跳过需要下载模型的慢测试）
pytest tests/ -v -m "not slow"

# 运行全部测试（包括 ChromaDB 向量检索测试）
pytest tests/ -v

# 运行特定测试类
pytest tests/ -v -k "TestAnomalyDetector"
```

### Q7: 怎么上传设备文档到知识库？

**A:** 启动 API 服务后，通过接口上传：

```bash
# 上传 PDF 设备手册
curl -X POST http://localhost:8000/knowledge/upload -F "file=@设备维护手册.pdf"

# 上传 Word 维修记录
curl -X POST http://localhost:8000/knowledge/upload -F "file=@维修记录.docx"

# 查看知识库统计
curl http://localhost:8000/knowledge/stats
```

支持的格式：PDF、Word (.docx)、TXT、Markdown。

上传后文档会被解析、分块、写入 ChromaDB 向量库，后续巡检诊断时会自动检索这些文档内容。

---

## 最佳实践

### 1. 使用 .env 管理密钥

```python
# ✅ 推荐：从环境变量读取
import os
from dotenv import load_dotenv
load_dotenv()
api_key = os.getenv("LLM_API_KEY")

# ❌ 避免：硬编码密钥
api_key = "sk-xxx"  # 千万不要这样写
```

### 2. LLM 调用加降级逻辑

```python
try:
    response = model.invoke(messages)
    result = parse_llm_response(response.content)
except Exception:
    result = fallback_to_rag(matched_cases)  # 永远有兜底
```

### 3. 状态图设计原则

- 每个节点**单一职责**，不要在一个节点里做太多事
- 节点函数**无副作用**，只通过 state 传数据
- 条件路由**尽早退出**，避免不必要的 LLM 调用

---

## 知识库

### 内置故障案例

| ID | 故障类型 | 严重程度 | 影响传感器 |
|----|---------|----------|-----------|
| F001 | 轴承磨损 | 高 | vibration, temperature |
| F002 | 冷却系统故障 | 高 | temperature |
| F003 | 气压系统泄漏 | 中 | pressure |
| F004 | 主轴电机过载 | 高 | temperature, vibration, rpm |
| F005 | 传感器漂移 | 低 | 全部 |
| F006 | 传动皮带松弛 | 中 | rpm, vibration |
| F007 | 润滑系统异常 | 中 | temperature, vibration |
| F008 | 电气接触不良 | 中 | 全部 |

### 文档上传

除了内置案例，支持上传真实设备文档扩充知识库：

```bash
# 上传设备手册（PDF/Word/TXT/MD）
curl -X POST http://localhost:8000/knowledge/upload -F "file=@设备手册.pdf"
```

**处理流程：**
```
上传文件 → 解析提取文本 → 按段落分块 → 向量化 → 写入 ChromaDB
                                                      ↓
                              巡检诊断时自动检索相关内容
```

支持格式：PDF、Word (.docx)、TXT、Markdown

---

## 扩展方向

- [ ] 接入真实 MQTT Broker（Mosquitto / EMQX）
- [ ] 工单写入数据库（SQLite / PostgreSQL）
- [ ] 通知集成（钉钉 / 企业微信 / 邮件）
- [ ] ChromaDB 向量检索模式完善
- [ ] 前端仪表盘（数据可视化）
- [ ] 更多故障案例扩充知识库

---

## 项目难点与解决方案

### 难点 1: LLM 调用不可靠

**问题：** LLM API 可能超时、限流、返回格式错误，不能把它当确定性服务用。

**解决：** 三层容错策略
```
LLM 正常返回 → 解析 JSON → 使用诊断结果
       ↓ 失败
降级为 RAG 关键词匹配结果 → 仍然可用
       ↓ 无匹配
返回默认案例 → 提示人工排查
```

**关键代码：** `fault_diagnoser.py` 的 try-except + 置信度检查
```python
try:
    response = model.invoke(messages)
    diagnosis = parse_llm_response(response.content)
except Exception:
    diagnosis = fallback_to_rag(matched_cases)

# 置信度过低，升级为人工审核
if diagnosis["confidence"] < 0.5:
    diagnosis["need_manual_review"] = True
```

### 难点 2: 异常数据不一定要走 LLM

**问题：** 正常数据调 LLM 纯烧钱，但又不能不检测。

**解决：** LangGraph 条件路由，正常数据直接跳过诊断和工单环节
```python
graph.add_conditional_edges(
    "detect_anomaly",
    should_diagnose,       # 有异常才走诊断
    {"diagnose": "diagnose_fault", "end": END},
)
```

### 难点 3: RAG 检索精度 vs 离线可用性

**问题：** ChromaDB 向量检索精度高但需要下载嵌入模型，面试演示时可能网络不通。

**解决：** 双模式设计，keyword 模式离线可用，chromadb 模式生产环境用
```python
kb = FaultKnowledgeBase(mode="keyword")    # 离线，面试用
kb = FaultKnowledgeBase(mode="chromadb")   # 在线，生产用
```

### 难点 4: 多传感器异常的优先级判断

**问题：** 温度超一点和压力超很多，严重程度不一样。

**解决：** 按超标比例分级，取最高严重程度
```python
ratio = (value - threshold) / threshold
severity = "严重" if ratio > 0.3 else "警告"  # 30% 为分界线
```

### 难点 5: Windows 兼容性

**问题：** emoji 在 Windows GBK 编码下报错。

**解决：** 启动时加 `PYTHONIOENCODING=utf-8` 环境变量，或在代码中处理。

---

## 参考资源

- [LangGraph 官方文档](https://langchain-ai.github.io/langgraph/)
- [LangChain 1.0 文档](https://docs.langchain.com/oss/python/langchain/quickstart)
- [DeepSeek API 文档](https://platform.deepseek.com/docs)
- [ChromaDB 文档](https://docs.trychroma.com/)
- [FastAPI 文档](https://fastapi.tiangolo.com/)
