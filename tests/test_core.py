"""
单元测试 - 覆盖核心模块
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from agents.anomaly_detector import detect_anomaly
from agents.fault_diagnoser import diagnose_fault
from agents.work_order_generator import generate_work_order
from rag.knowledge_base import FaultKnowledgeBase
from mqtt.simulator import generate_sensor_reading, generate_batch


class TestAnomalyDetector:
    def test_normal_data(self):
        state = {"sensor_data": {"device_id": "CNC-Machine-01", "sensors": {"temperature": 60, "vibration": 4.0, "pressure": 2.0, "rpm": 2000}}}
        result = detect_anomaly(state)
        assert result["anomaly_result"]["is_anomaly"] is False
        assert result["anomaly_result"]["anomaly_count"] == 0

    def test_high_temperature(self):
        state = {"sensor_data": {"device_id": "CNC-Machine-01", "sensors": {"temperature": 100, "vibration": 4.0, "pressure": 2.0, "rpm": 2000}}}
        result = detect_anomaly(state)
        assert result["anomaly_result"]["is_anomaly"] is True
        assert any(d["sensor"] == "temperature" for d in result["anomaly_result"]["anomaly_details"])

    def test_low_pressure(self):
        state = {"sensor_data": {"device_id": "RoboticArm-03", "sensors": {"temperature": 50, "vibration": 3.0, "pressure": 0.3, "rpm": 1500}}}
        result = detect_anomaly(state)
        assert result["anomaly_result"]["is_anomaly"] is True
        assert any(d["sensor"] == "pressure" for d in result["anomaly_result"]["anomaly_details"])

    def test_multiple_anomalies(self):
        state = {"sensor_data": {"device_id": "CNC-Machine-01", "sensors": {"temperature": 100, "vibration": 15.0, "pressure": 2.0, "rpm": 2000}}}
        result = detect_anomaly(state)
        assert result["anomaly_result"]["anomaly_count"] == 2

    def test_severity_levels(self):
        state_w = {"sensor_data": {"device_id": "CNC-Machine-01", "sensors": {"temperature": 88, "vibration": 4.0, "pressure": 2.0, "rpm": 2000}}}
        state_c = {"sensor_data": {"device_id": "CNC-Machine-01", "sensors": {"temperature": 120, "vibration": 4.0, "pressure": 2.0, "rpm": 2000}}}
        assert detect_anomaly(state_w)["anomaly_result"]["overall_severity"] == "警告"
        assert detect_anomaly(state_c)["anomaly_result"]["overall_severity"] == "严重"

    def test_zero_lower_bound_handles_negative_reading(self):
        state = {
            "sensor_data": {
                "device_id": "CNC-Machine-01",
                "sensors": {"vibration": -1.0},
            }
        }

        result = detect_anomaly(state)["anomaly_result"]

        assert result["is_anomaly"] is True
        assert result["anomaly_details"][0]["type"] == "low"

    def test_invalid_numeric_reading_is_flagged(self):
        state = {
            "sensor_data": {
                "device_id": "CNC-Machine-01",
                "sensors": {"temperature": float("nan")},
            }
        }

        result = detect_anomaly(state)["anomaly_result"]

        assert result["overall_severity"] == "严重"
        assert result["anomaly_details"][0]["type"] == "invalid"


class TestKnowledgeBase:
    @pytest.fixture
    def kb(self):
        k = FaultKnowledgeBase(mode="keyword")
        k.load_cases()
        return k

    def test_load_cases(self, kb):
        assert len(kb.cases) == 8

    def test_query_vibration(self, kb):
        results = kb.query("设备 CNC-Machine-01 出现异常:\n- vibration 读数 14.5，超过上限 12")
        assert len(results) > 0
        fault_types = [r["fault_type"] for r in results]
        assert "轴承磨损" in fault_types

    def test_query_temperature(self, kb):
        results = kb.query("设备 CNC-Machine-01 出现异常:\n- temperature 读数 95，超过上限 85")
        assert len(results) > 0

    def test_query_pressure(self, kb):
        results = kb.query("设备 RoboticArm-03 出现异常:\n- pressure 读数 0.5，低于下限 0.8")
        assert len(results) > 0
        fault_types = [r["fault_type"] for r in results]
        assert "气压系统泄漏" in fault_types

    def test_default_case(self, kb):
        results = kb.query("完全无关的文本 xyz")
        assert len(results) > 0


class TestFaultDiagnoser:
    def test_model_initialization_failure_uses_rag_fallback(self, monkeypatch):
        class FakeKnowledgeBase:
            def __init__(self, mode):
                self.mode = mode

            def load_cases(self):
                return None

            def build_symptom_text(self, sensor_data, anomaly_result):
                return "temperature 读数 95，超过上限 85"

            def query(self, symptom_text, n_results):
                return [{
                    "fault_id": "F001",
                    "fault_type": "冷却系统故障",
                    "distance": 0.2,
                    "severity": "高",
                    "document": "根本原因: 冷却液不足\n解决方案: 补充冷却液",
                    "solution": "检查并补充冷却液",
                }]

        def fail_model_initialization(*args, **kwargs):
            raise RuntimeError("missing API key")

        monkeypatch.setattr("agents.fault_diagnoser.FaultKnowledgeBase", FakeKnowledgeBase)
        monkeypatch.setattr("agents.fault_diagnoser.init_chat_model", fail_model_initialization)
        state = {
            "sensor_data": {"device_id": "CNC-Machine-01", "sensors": {"temperature": 95}},
            "anomaly_result": {
                "anomaly_details": [{
                    "sensor": "temperature",
                    "value": 95,
                    "threshold": 85,
                    "type": "high",
                }]
            },
        }

        diagnosis = diagnose_fault(state)["diagnosis"]

        assert diagnosis["llm_used"] is False
        assert diagnosis["fault_type"] == "冷却系统故障"
        assert diagnosis["confidence"] == pytest.approx(0.8)


class TestWorkOrderGenerator:
    def test_generate_order(self):
        state = {
            "sensor_data": {"device_id": "CNC-Machine-01", "sensors": {"temperature": 100}},
            "anomaly_result": {"overall_severity": "警告", "anomaly_details": []},
            "diagnosis": {"fault_type": "冷却系统故障", "cause": "冷却液不足", "solution": "检查冷却液液位"},
        }
        result = generate_work_order(state)
        order = result["work_order"]
        assert order["device_id"] == "CNC-Machine-01"
        assert order["fault_type"] == "冷却系统故障"
        assert order["status"] == "待处理"
        assert order["order_id"].startswith("WO-")

    def test_priority_mapping(self):
        for severity, expected in [("严重", "紧急"), ("警告", "高"), ("低", "中")]:
            state = {
                "sensor_data": {"device_id": "CNC-Machine-01", "sensors": {}},
                "anomaly_result": {"overall_severity": severity, "anomaly_details": []},
                "diagnosis": {"fault_type": "测试", "cause": "", "solution": ""},
            }
            assert generate_work_order(state)["work_order"]["priority"] == expected


class TestSimulator:
    def test_generate_reading(self):
        reading = generate_sensor_reading("CNC-Machine-01")
        assert "device_id" in reading
        assert "sensors" in reading
        assert set(reading["sensors"].keys()) == {"temperature", "vibration", "pressure", "rpm"}

    def test_generate_batch(self):
        assert len(generate_batch(5)) == 5

    def test_values_are_numeric(self):
        for value in generate_sensor_reading("Test")["sensors"].values():
            assert isinstance(value, (int, float))
