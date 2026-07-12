"""
RAG 故障知识库
支持两种检索模式：
  1. keyword 模式（默认）: 基于关键词匹配，离线可用，面试演示首选
  2. chromadb 模式: 基于向量检索，需要首次运行时下载嵌入模型
"""
import json
from pathlib import Path


class FaultKnowledgeBase:
    def __init__(self, mode: str = "keyword"):
        """
        mode: "keyword" 或 "chromadb"
        """
        self.mode = mode
        self.cases = []

        if mode == "chromadb":
            import chromadb
            from chromadb.utils import embedding_functions
            from config import CHROMA_PERSIST_DIR, CHROMA_COLLECTION
            self.client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
            # 优先用 Ollama 中文 Embedding 模型，不可用则降级为内置模型
            try:
                self.ef = embedding_functions.OllamaEmbeddingFunction(
                    url="http://localhost:11434",
                    model_name="shaw/dmeta-embedding-zh",
                )
                # 测试连通性
                self.ef(["测试"])
                print("[RAG] 使用 Ollama dmeta-embedding-zh 中文模型")
            except Exception:
                self.ef = embedding_functions.DefaultEmbeddingFunction()
                print("[RAG] Ollama 不可用，降级为 ChromaDB 内置模型")
            self.collection = self.client.get_or_create_collection(
                name=CHROMA_COLLECTION,
                embedding_function=self.ef,
                metadata={"hnsw:space": "cosine"},
            )

    def load_cases(self, json_path: str = None):
        """从 JSON 文件加载故障案例"""
        if json_path is None:
            json_path = str(Path(__file__).parent / "data" / "fault_cases.json")

        with open(json_path, "r", encoding="utf-8") as f:
            self.cases = json.load(f)

        if self.mode == "chromadb":
            self._load_to_chroma()

        print(f"[RAG] 已加载 {len(self.cases)} 条故障案例 (模式: {self.mode})")

    def _load_to_chroma(self):
        """加载案例到 ChromaDB"""
        documents, metadatas, ids = [], [], []
        for case in self.cases:
            doc_text = (
                f"故障类型: {case['fault_type']}\n"
                f"症状表现: {case['symptoms']}\n"
                f"根本原因: {case['root_cause']}\n"
                f"解决方案: {case['solution']}"
            )
            documents.append(doc_text)
            metadatas.append({
                "fault_id": case["id"],
                "fault_type": case["fault_type"],
                "severity": case["severity"],
                "affected_sensors": ",".join(case["affected_sensors"]),
                "solution": case["solution"],
            })
            ids.append(case["id"])
        self.collection.upsert(documents=documents, metadatas=metadatas, ids=ids)

    def query(self, symptom_text: str, n_results: int = 3) -> list[dict]:
        """根据症状检索最相关的故障案例"""
        if self.mode == "chromadb":
            return self._query_chroma(symptom_text, n_results)
        return self._query_keyword(symptom_text, n_results)

    def _query_chroma(self, symptom_text: str, n_results: int) -> list[dict]:
        """ChromaDB 向量检索"""
        results = self.collection.query(
            query_texts=[symptom_text], n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
        cases = []
        for i in range(len(results["ids"][0])):
            cases.append({
                "fault_id": results["ids"][0][i],
                "document": results["documents"][0][i],
                "distance": results["distances"][0][i],
                **results["metadatas"][0][i],
            })
        return cases

    def _query_keyword(self, symptom_text: str, n_results: int) -> list[dict]:
        """关键词匹配检索（离线可用）"""
        # 从症状描述中提取关键词
        keywords = set()
        for word in symptom_text.replace("\n", " ").split():
            # 提取传感器名称和数值关键词
            word_lower = word.lower().strip("：:，,。（）()")
            if word_lower:
                keywords.add(word_lower)

        scored = []
        for case in self.cases:
            score = 0
            case_text = f"{case['fault_type']} {case['symptoms']} {' '.join(case['affected_sensors'])}"

            # 传感器匹配
            for sensor in case["affected_sensors"]:
                if sensor in symptom_text.lower():
                    score += 3

            # 症状关键词匹配
            symptoms_keywords = case["symptoms"].replace("，", " ").replace("(", " ").replace(")", " ").split()
            for kw in symptoms_keywords:
                kw = kw.strip("，。、()（）")
                if len(kw) >= 2 and kw in symptom_text:
                    score += 2

            # 故障类型关键词
            for char_group in case["fault_type"].split("/"):
                if char_group in symptom_text:
                    score += 5

            if score > 0:
                # 构造与 chromadb 模式一致的文档格式
                doc_text = (
                    f"故障类型: {case['fault_type']}\n"
                    f"症状表现: {case['symptoms']}\n"
                    f"根本原因: {case['root_cause']}\n"
                    f"解决方案: {case['solution']}"
                )
                scored.append({
                    "fault_id": case["id"],
                    "fault_type": case["fault_type"],
                    "severity": case["severity"],
                    "affected_sensors": ",".join(case["affected_sensors"]),
                    "solution": case["solution"],
                    "document": doc_text,
                    "distance": max(0.01, 1.0 - score * 0.1),  # 分数转距离
                    "score": score,
                })

        # 按分数降序排列
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:n_results] if scored else [self._default_case()]

    def _default_case(self) -> dict:
        """无匹配时的默认返回"""
        return {
            "fault_id": "UNKNOWN",
            "fault_type": "未知故障",
            "severity": "中",
            "affected_sensors": "",
            "solution": "建议人工现场排查，记录异常参数后反馈至知识库",
            "document": "知识库中未找到匹配的故障模式",
            "distance": 1.0,
            "score": 0,
        }

    def add_documents(self, chunks: list[dict]) -> int:
        """
        将文档分块写入 ChromaDB 向量库
        chunks: [{"text": "...", "metadata": {"source": "文件名", ...}}, ...]
        返回: 写入的分块数量
        """
        if self.mode != "chromadb":
            raise RuntimeError("文档入库仅支持 chromadb 模式，请使用 mode='chromadb'")

        documents, metadatas, ids = [], [], []
        for i, chunk in enumerate(chunks):
            doc_id = f"doc_{chunk['metadata'].get('source', 'unknown')}_{i}"
            documents.append(chunk["text"])
            metadatas.append({
                "source": chunk["metadata"].get("source", "unknown"),
                "type": chunk["metadata"].get("type", "document"),
                "fault_type": "文档知识",
                "severity": "未知",
                "affected_sensors": "",
                "solution": "",
            })
            ids.append(doc_id)

        self.collection.upsert(documents=documents, metadatas=metadatas, ids=ids)
        return len(chunks)

    def ingest_file(self, file_path: str, chunk_size: int = 500, chunk_overlap: int = 50) -> int:
        """
        一条龙：解析文件 → 分块 → 写入 ChromaDB
        返回: 写入的分块数量
        """
        from rag.document_parser import parse_file

        chunks = parse_file(file_path, chunk_size, chunk_overlap)
        count = self.add_documents(chunks)
        print(f"[RAG] 文件 {Path(file_path).name} 已入库: {count} 个分块")
        return count

    def get_stats(self) -> dict:
        """获取知识库统计信息"""
        if self.mode == "chromadb":
            total = self.collection.count()
            # 获取所有文档的来源
            results = self.collection.get(include=["metadatas"])
            sources = set()
            for meta in results["metadatas"]:
                if meta.get("source"):
                    sources.add(meta["source"])
            return {
                "mode": "chromadb",
                "total_documents": total,
                "sources": sorted(sources),
            }
        return {
            "mode": "keyword",
            "total_documents": len(self.cases),
            "sources": ["fault_cases.json"],
        }

    def build_symptom_text(self, sensor_data: dict, anomaly_info: dict) -> str:
        """将传感器数据和异常信息转换为自然语言症状描述"""
        sensors = sensor_data.get("sensors", {})
        device = sensor_data.get("device_id", "未知设备")
        anomalies = anomaly_info.get("anomaly_details", [])

        parts = [f"设备 {device} 出现以下异常:"]
        for a in anomalies:
            sensor = a["sensor"]
            value = a["value"]
            threshold = a["threshold"]
            direction = "超过上限" if a["type"] == "high" else "低于下限"
            parts.append(f"- {sensor} 读数 {value}，{direction} {threshold}")

        return "\n".join(parts)
