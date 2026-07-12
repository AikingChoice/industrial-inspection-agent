"""
单元测试 - 文档解析和知识库入库
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import tempfile
from pathlib import Path
from rag.document_parser import chunk_text, parse_txt, parse_file


class TestChunkText:
    def test_short_text_no_chunking(self):
        blocks = [{"text": "这是一段短文本，不需要分块。", "metadata": {"source": "test.txt"}}]
        chunks = chunk_text(blocks, chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0]["text"] == blocks[0]["text"]

    def test_long_text_chunking(self):
        long_text = "A" * 1000
        blocks = [{"text": long_text, "metadata": {"source": "test.txt"}}]
        chunks = chunk_text(blocks, chunk_size=300, chunk_overlap=50)
        assert len(chunks) >= 3
        # 每块不超过 chunk_size（允许一定误差，因为断句）
        for chunk in chunks:
            assert len(chunk["text"]) <= 350

    def test_metadata_preserved(self):
        blocks = [{"text": "短文本", "metadata": {"source": "manual.pdf", "page": 5}}]
        chunks = chunk_text(blocks, chunk_size=500)
        assert chunks[0]["metadata"]["source"] == "manual.pdf"
        assert chunks[0]["metadata"]["page"] == 5

    def test_empty_blocks(self):
        chunks = chunk_text([], chunk_size=500)
        assert len(chunks) == 0


class TestParseTxt:
    def test_parse_txt_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("第一段内容，这是一个测试段落，内容足够长。\n\n")
            f.write("第二段内容，这也是一个测试段落，用来验证分段。\n\n")
            f.write("短")  # 太短，应该被跳过
            temp_path = f.name

        try:
            blocks = parse_txt(temp_path)
            assert len(blocks) == 2
            assert "第一段" in blocks[0]["text"]
            assert "第二段" in blocks[1]["text"]
            assert blocks[0]["metadata"]["type"] == "txt"
        finally:
            os.remove(temp_path)

    def test_parse_empty_txt(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("")
            temp_path = f.name

        try:
            with pytest.raises(ValueError, match="无法提取"):
                parse_txt(temp_path)
        finally:
            os.remove(temp_path)


class TestParseFile:
    def test_unsupported_format(self):
        with pytest.raises(ValueError, match="不支持的文件格式"):
            parse_file("test.xyz")

    def test_txt_end_to_end(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("设备维护手册第一章：日常巡检要点。\n\n")
            f.write("第二章：故障排查指南。温度异常时首先检查冷却系统。\n\n")
            temp_path = f.name

        try:
            chunks = parse_file(temp_path, chunk_size=100, chunk_overlap=20)
            assert len(chunks) >= 1
            assert all("text" in c for c in chunks)
            assert all("metadata" in c for c in chunks)
        finally:
            os.remove(temp_path)


class TestKnowledgeBaseIngest:
    def test_keyword_mode_rejects_ingest(self):
        from rag.knowledge_base import FaultKnowledgeBase
        kb = FaultKnowledgeBase(mode="keyword")
        kb.load_cases()
        with pytest.raises(RuntimeError, match="仅支持 chromadb"):
            kb.add_documents([{"text": "test", "metadata": {}}])

    @pytest.fixture
    def chromadb_kb(self):
        """创建临时 ChromaDB 知识库（首次运行需下载嵌入模型）"""
        import tempfile
        import shutil
        from rag.knowledge_base import FaultKnowledgeBase as KB
        tmp_dir = tempfile.mkdtemp()
        # 临时修改配置
        import config
        old_dir = config.CHROMA_PERSIST_DIR
        config.CHROMA_PERSIST_DIR = tmp_dir

        try:
            kb = KB(mode="chromadb")
            kb.load_cases()
        except Exception as e:
            pytest.skip(f"ChromaDB 初始化失败（可能需要下载嵌入模型）: {e}")

        yield kb

        # 清理
        config.CHROMA_PERSIST_DIR = old_dir
        shutil.rmtree(tmp_dir, ignore_errors=True)

    @pytest.mark.slow
    def test_add_documents(self, chromadb_kb):
        chunks = [
            {"text": "设备温度过高时，首先检查冷却液液位和循环泵状态", "metadata": {"source": "manual.pdf", "type": "pdf"}},
            {"text": "振动异常通常与轴承磨损或传动部件松动有关", "metadata": {"source": "manual.pdf", "type": "pdf"}},
        ]
        count = chromadb_kb.add_documents(chunks)
        assert count == 2

        stats = chromadb_kb.get_stats()
        assert stats["total_documents"] >= 2

    @pytest.mark.slow
    def test_query_after_ingest(self, chromadb_kb):
        chunks = [
            {"text": "CNC 机床主轴轴承磨损的典型症状是振动值持续升高", "metadata": {"source": "维修手册.pdf"}},
        ]
        chromadb_kb.add_documents(chunks)

        results = chromadb_kb.query("设备振动异常升高", n_results=3)
        assert len(results) > 0
