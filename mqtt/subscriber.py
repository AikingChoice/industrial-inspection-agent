"""
MQTT 订阅者
监听传感器数据，转发给巡检 Agent 处理
"""
import json
import paho.mqtt.client as mqtt
from config import MQTT_BROKER, MQTT_PORT, MQTT_TOPIC


class SensorSubscriber:
    def __init__(self, on_message_callback):
        """
        on_message_callback: 回调函数，签名 (device_id: str, data: dict) -> None
        """
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.callback = on_message_callback

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        print(f"[MQTT] 已连接 Broker: {MQTT_BROKER}:{MQTT_PORT}")
        client.subscribe(MQTT_TOPIC)
        print(f"[MQTT] 已订阅主题: {MQTT_TOPIC}")

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            device_id = data.get("device_id", "unknown")
            print(f"[MQTT] 收到数据: {device_id}")
            self.callback(device_id, data)
        except Exception as e:
            print(f"[MQTT] 解析消息失败: {e}")

    def start(self):
        self.client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        self.client.loop_forever()

    def stop(self):
        self.client.disconnect()
