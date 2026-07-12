"""
工业设备智能巡检 Agent - 入口
支持两种运行模式：
  1. demo  - 使用模拟数据演示完整流程
  2. mqtt  - 连接 MQTT Broker 处理实时传感器数据
"""
import sys
import json
from datetime import datetime


def run_demo():
    """演示模式：生成模拟数据，运行完整巡检流程"""
    from mqtt.simulator import generate_batch
    from graph.workflow import run_inspection

    print("=" * 60)
    print("🏭 工业设备智能巡检 Agent - 演示模式")
    print("=" * 60)

    # 生成模拟传感器数据
    readings = generate_batch(5)
    results = {"total": len(readings), "anomalies": 0, "orders": []}

    for i, reading in enumerate(readings, 1):
        device = reading["device_id"]
        sensors = reading["sensors"]
        print(f"\n{'─'*60}")
        print(f"📡 第 {i}/{len(readings)} 条数据 | 设备: {device}")
        print(f"   温度: {sensors['temperature']}°C | 振动: {sensors['vibration']}mm/s | "
              f"压力: {sensors['pressure']}MPa | 转速: {sensors['rpm']}r/min")

        # 运行巡检流程
        result = run_inspection(reading)

        if result.get("anomaly_result", {}).get("is_anomaly"):
            results["anomalies"] += 1
        if result.get("work_order"):
            results["orders"].append(result["work_order"]["order_id"])

    # 输出汇总
    print(f"\n{'='*60}")
    print(f"📊 巡检汇总")
    print(f"   扫描设备数据: {results['total']} 条")
    print(f"   发现异常:     {results['anomalies']} 条")
    print(f"   生成工单:     {len(results['orders'])} 个")
    for oid in results["orders"]:
        print(f"     - {oid}")
    print(f"{'='*60}")

    return results


def run_mqtt():
    """MQTT 模式：连接 Broker，实时处理传感器数据"""
    from mqtt.subscriber import SensorSubscriber
    from graph.workflow import run_inspection

    print("🏭 工业设备智能巡检 Agent - MQTT 模式")
    print("正在连接 MQTT Broker...")

    def on_sensor_data(device_id, data):
        print(f"\n{'─'*60}")
        print(f"📡 收到设备数据: {device_id}")
        run_inspection(data)

    subscriber = SensorSubscriber(on_message_callback=on_sensor_data)
    try:
        subscriber.start()
    except KeyboardInterrupt:
        subscriber.stop()
        print("\n巡检 Agent 已停止")


def run_single(data_path: str):
    """单次模式：从 JSON 文件读取一条数据运行"""
    from graph.workflow import run_inspection

    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"🏭 巡检设备: {data.get('device_id', '未知')}")
    result = run_inspection(data)
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:")
        print("  python main.py demo          # 模拟数据演示")
        print("  python main.py mqtt          # 连接 MQTT Broker")
        print("  python main.py single <file> # 从 JSON 文件运行单次巡检")
        print("\n默认运行 demo 模式...\n")
        run_demo()
    elif sys.argv[1] == "demo":
        run_demo()
    elif sys.argv[1] == "mqtt":
        run_mqtt()
    elif sys.argv[1] == "single" and len(sys.argv) > 2:
        run_single(sys.argv[2])
    else:
        print(f"未知命令: {sys.argv[1]}")
        sys.exit(1)
