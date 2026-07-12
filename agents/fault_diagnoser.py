"""
故障诊断 Agent
结合 RAG 知识库检索 + LLM 推理，对异常进行故障诊断
置信度低于阈值时自动标记为需人工审核
"""
import json
import logging
from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage, HumanMessage
from rag.knowledge_base import FaultKnowledgeBase
from config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL

logger = logging.getLogger(__name__)

# 置信度阈值：低于此值自动升级为人工审核
CONFIDENCE_THRESHOLD = 0.5


def _build_prompt(symptom_text: str, matched_cases: list[dict]) -> str:
    """构造 LLM 诊断提示词"""
    cases_text = ""
    for i, case in enumerate(matched_cases, 1):
        cases_text += f"\n案例{i}:\n{case['document']}\n"

    return f"""你是一名工业设备故障诊断专家。请根据传感器异常数据和历史故障案例，给出诊断结论。

## 当前异常
{symptom_text}

## 知识库匹配的历史案例
{cases_text}

## 要求
请以 JSON 格式输出诊断结果，字段如下：
{{
  "fault_type": "最可能的故障类型",
  "confidence": 0.0到1.0之间的置信度,
  "cause": "根本原因分析（2-3句话）",
  "solution": "具体维修建议（分步骤）",
  "reasoning": "推理过程（说明为什么判断为此故障，引用传感器数据和案例）"
}}

只输出 JSON，不要其他内容。"""


def diagnose_fault(state: dict) -> dict:
    """
    根据异常检测结果，检索知识库 + LLM 推理，生成诊断报告
    输入 state: {sensor_data, anomaly_result}
    输出: 追加 diagnosis 字段
    """
    sensor_data = state["sensor_data"]
    anomaly_result = state["anomaly_result"]

    # 1. RAG 检索（优先 ChromaDB 向量检索，失败降级为关键词匹配）
    symptom_text = None
    try:
        kb = FaultKnowledgeBase(mode="chromadb")
        kb.load_cases()
        symptom_text = kb.build_symptom_text(sensor_data, anomaly_result)
        matched_cases = kb.query(symptom_text, n_results=3)
        print(f"[Agent-故障诊断] RAG 检索到 {len(matched_cases)} 条相关案例 (模式: chromadb)")
    except Exception as e:
        logger.warning(f"ChromaDB 不可用({e})，降级为关键词匹配")
        kb = FaultKnowledgeBase(mode="keyword")
        kb.load_cases()
        symptom_text = kb.build_symptom_text(sensor_data, anomaly_result)
        matched_cases = kb.query(symptom_text, n_results=3)
        print(f"[Agent-故障诊断] RAG 检索到 {len(matched_cases)} 条相关案例 (模式: keyword 降级)")

    # 2. LLM 推理
    model = init_chat_model(
        f"openai:{LLM_MODEL}",
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        temperature=0.1,
        max_tokens=800,
    )
    prompt = _build_prompt(symptom_text, matched_cases)

    try:
        messages = [
            SystemMessage(content="你是工业设备故障诊断专家，输出严格 JSON 格式。"),
            HumanMessage(content=prompt),
        ]
        response = model.invoke(messages)
        raw = response.content.strip()
        # 提取 JSON（兼容 markdown code block）
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        llm_result = json.loads(raw)

        diagnosis = {
            "fault_type": llm_result.get("fault_type", "未知"),
            "confidence": llm_result.get("confidence", 0.5),
            "severity": matched_cases[0].get("severity", "中") if matched_cases else "中",
            "cause": llm_result.get("cause", ""),
            "solution": llm_result.get("solution", ""),
            "reasoning": llm_result.get("reasoning", ""),
            "matched_cases": [
                {"fault_id": c["fault_id"], "fault_type": c["fault_type"], "distance": round(c["distance"], 4)}
                for c in matched_cases
            ],
            "llm_used": True,
        }
        print(f"[Agent-故障诊断] LLM 诊断: {diagnosis['fault_type']} (置信度: {diagnosis['confidence']:.0%})")
        print(f"[Agent-故障诊断] 推理: {diagnosis['reasoning'][:100]}...")

    except Exception as e:
        # LLM 调用失败时降级为纯 RAG 结果
        print(f"[Agent-故障诊断] LLM 调用失败({e})，降级为 RAG 匹配结果")
        best = matched_cases[0] if matched_cases else {}
        diagnosis = {
            "fault_type": best.get("fault_type", "未知故障"),
            "confidence": max(0, 1 - best.get("distance", 1)),
            "severity": best.get("severity", "中"),
            "cause": best.get("document", "").split("根本原因: ")[1].split("\n")[0] if "根本原因:" in best.get("document", "") else "",
            "solution": best.get("solution", "建议人工排查"),
            "reasoning": "LLM 不可用，基于知识库关键词匹配",
            "matched_cases": [
                {"fault_id": c["fault_id"], "fault_type": c["fault_type"], "distance": round(c["distance"], 4)}
                for c in matched_cases
            ],
            "llm_used": False,
        }

    # 置信度过低，升级为人工审核（LLM 成功和降级路径都走这里）
    if diagnosis["confidence"] < CONFIDENCE_THRESHOLD:
        diagnosis["need_manual_review"] = True
        diagnosis["review_reason"] = f"置信度 {diagnosis['confidence']:.0%} 低于阈值 {CONFIDENCE_THRESHOLD:.0%}，建议人工复核"
        logger.warning(f"置信度过低({diagnosis['confidence']:.0%})，已标记为人工审核")
        print(f"[Agent-故障诊断] ⚠️ 置信度过低，已升级为人工审核")

    return {"diagnosis": diagnosis}
