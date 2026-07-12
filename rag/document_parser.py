"""
文档解析器
支持 PDF、Word、TXT 文件的文本提取和分块
用于将设备手册、维修记录等文档导入 RAG 知识库
"""
import re
from pathlib import Path


def parse_pdf(file_path: str) -> list[dict]:
    """
    解析 PDF 文件，逐页提取文本
    返回: [{"text": "页内容", "metadata": {"source": "文件名", "page": 1}}, ...]
    """
    from PyPDF2 import PdfReader

    reader = PdfReader(file_path)
    source = Path(file_path).name
    pages = []

    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text()
        if text and text.strip():
            pages.append({
                "text": text.strip(),
                "metadata": {"source": source, "page": i, "type": "pdf"},
            })

    if not pages:
        raise ValueError(f"PDF 文件无法提取文本: {file_path}")

    return pages


def parse_docx(file_path: str) -> list[dict]:
    """
    解析 Word 文件，按段落提取文本
    返回: [{"text": "段落内容", "metadata": {"source": "文件名", "paragraph": 1}}, ...]
    """
    from docx import Document

    doc = Document(file_path)
    source = Path(file_path).name
    paragraphs = []

    for i, para in enumerate(doc.paragraphs, 1):
        text = para.text.strip()
        if text and len(text) > 10:  # 跳过过短的段落（标题、空行等）
            paragraphs.append({
                "text": text,
                "metadata": {"source": source, "paragraph": i, "type": "docx"},
            })

    if not paragraphs:
        raise ValueError(f"Word 文件无法提取有效段落: {file_path}")

    return paragraphs


def parse_txt(file_path: str) -> list[dict]:
    """
    解析纯文本文件，按段落分割
    返回: [{"text": "段落内容", "metadata": {"source": "文件名", "paragraph": 1}}, ...]
    """
    source = Path(file_path).name
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 按空行分割段落
    raw_paragraphs = re.split(r"\n\s*\n", content)
    paragraphs = []

    for i, para in enumerate(raw_paragraphs, 1):
        text = para.strip()
        if text and len(text) > 10:
            paragraphs.append({
                "text": text,
                "metadata": {"source": source, "paragraph": i, "type": "txt"},
            })

    if not paragraphs:
        raise ValueError(f"文本文件无法提取有效段落: {file_path}")

    return paragraphs


def chunk_text(
    text_blocks: list[dict],
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[dict]:
    """
    将文本块进一步分块，控制每块大小
    - chunk_size: 每块最大字符数
    - chunk_overlap: 块之间重叠字符数
    """
    chunks = []

    for block in text_blocks:
        text = block["text"]
        metadata = block["metadata"]

        if len(text) <= chunk_size:
            chunks.append({"text": text, "metadata": metadata})
            continue

        # 长文本按 chunk_size 切分，带重叠
        start = 0
        chunk_idx = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]

            # 尝试在句号/换行处断开，避免截断句子
            if end < len(text):
                for sep in ["\n", "。", "；", ".", ";", "，", ","]:
                    last_sep = chunk.rfind(sep)
                    if last_sep > chunk_size * 0.3:  # 至少保留 30% 内容
                        chunk = chunk[:last_sep + 1]
                        end = start + len(chunk)
                        break

            chunk_metadata = {**metadata, "chunk_index": chunk_idx}
            chunks.append({"text": chunk.strip(), "metadata": chunk_metadata})

            start = end - chunk_overlap
            chunk_idx += 1

    return chunks


def parse_file(file_path: str, chunk_size: int = 500, chunk_overlap: int = 50) -> list[dict]:
    """
    统一入口：根据文件扩展名自动选择解析器，解析+分块
    """
    suffix = Path(file_path).suffix.lower()

    parser_map = {
        ".pdf": parse_pdf,
        ".docx": parse_docx,
        ".doc": parse_docx,
        ".txt": parse_txt,
        ".md": parse_txt,
    }

    parser = parser_map.get(suffix)
    if not parser:
        raise ValueError(f"不支持的文件格式: {suffix}（支持 PDF/Word/TXT/MD）")

    text_blocks = parser(file_path)
    chunks = chunk_text(text_blocks, chunk_size, chunk_overlap)

    return chunks
