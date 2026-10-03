"""
LangGraph 多智能体工作流
定义状态图：异常检测 → 故障诊断 → 工单生成 → 通知
"""
from typing import TypedDict, Optional, Annotated
from langgraph.graph import StateGraph, END

from agents.anomaly_detector import detect_anomaly
from agents.fault_diagnoser import diagnose_fault
from agents.work_order_generator import generate_work_order
from logger import log_inspection


# ========== 状态定义 ==========
class InspectionState(TypedDict):
    """巡检 Agent 的全局状态"""
    sensor_data: dict                    # 原始传感器数据
    anomaly_result: Optional[dict]       # 异常检测结果
    diagnosis: Optional[dict]            # 故障诊断结果
    work_order: Optional[dict]           # 生成的工单
    notification_sent: bool              # 是否已发送通知


# ========== 节点函数 ==========
def notify(state: dict) -> dict:
    """当前仅产生需审核的本地草稿，不向外部渠道发送通知。"""
    work_order = state.get("work_order")
    if work_order:
        print(f"\n{'='*60}")
        print(f"📋 维修工单草稿已生成，等待人工审核")
        print(f"   工单号: {work_order['order_id']}")
        print(f"   设备:   {work_order['device_id']}")
        print(f"   优先级: {work_order['priority']}")
        print(f"   故障:   {work_order['fault_type']}")
        print(f"   指派:   {work_order['assignee'] or '待人工分派'}")
        print(f"   建议:   {work_order['recommended_action'][:80]}")
        print(f"{'='*60}\n")
    return {"notification_sent": False}


def route_data_quality_review(state: dict) -> dict:
    """质量不合格的数据不得进入故障诊断或工单建议。"""
    issues = state.get("anomaly_result", {}).get("data_quality_issues", [])
    return {
        "diagnosis": {
            "fault_type": "遥测数据质量异常",
            "confidence": 0.0,
            "cause": "输入数据不完整、测点未配置或单位不匹配，不能据此判断设备状态。",
            "solution": "请检查采集链路、测点配置和单位定义，并由设备工程师复核原始读数。",
            "reasoning": "数据质量校验未通过，已跳过故障诊断。",
            "need_manual_review": True,
            "review_reason": "遥测数据质量未通过校验",
            "data_quality_issues": issues,
            "llm_used": False,
        },
        "work_order": None,
        "notification_sent": False,
    }


# ========== 条件路由 ==========
def should_diagnose(state: dict) -> str:
    """根据异常检测结果决定是否进入诊断流程"""
    anomaly = state.get("anomaly_result", {})
    if not anomaly.get("data_quality_ok", True):
        return "quality_review"
    if anomaly.get("is_anomaly", False):
        return "diagnose"
    return "end"


# ========== 构建工作流图 ==========
def build_inspection_graph() -> StateGraph:
    """
    构建巡检 Agent 的 LangGraph 状态图

    流程:
    detect_anomaly ──(有异常)──→ diagnose_fault ──→ generate_work_order ──→ notify ──→ END
           │
           └──(无异常)──→ END
    """
    graph = StateGraph(InspectionState)

    # 注册节点
    graph.add_node("detect_anomaly", detect_anomaly)
    graph.add_node("diagnose_fault", diagnose_fault)
    graph.add_node("generate_work_order", generate_work_order)
    graph.add_node("notify", notify)

    # 设置入口
    graph.set_entry_point("detect_anomaly")

    # 条件边：异常检测后根据结果路由
    graph.add_conditional_edges(
        "detect_anomaly",
        should_diagnose,
        {
            "diagnose": "diagnose_fault",
            "quality_review": "quality_review",
            "end": END,
        },
    )

    graph.add_node("quality_review", route_data_quality_review)
    graph.add_edge("quality_review", END)

    # 线性边：诊断 → 工单 → 通知 → 结束
    graph.add_edge("diagnose_fault", "generate_work_order")
    graph.add_edge("generate_work_order", "notify")
    graph.add_edge("notify", END)

    return graph.compile()


# ========== 便捷入口 ==========
def run_inspection(sensor_data: dict) -> dict:
    """
    运行一次完整的巡检流程
    返回最终状态
    """
    graph = build_inspection_graph()

    initial_state: InspectionState = {
        "sensor_data": sensor_data,
        "anomaly_result": None,
        "diagnosis": None,
        "work_order": None,
        "notification_sent": False,
    }

    result = graph.invoke(initial_state)

    # 记录巡检日志
    log_inspection(result)

    return result
