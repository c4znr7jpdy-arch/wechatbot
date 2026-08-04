# -*- coding: utf-8 -*-
"""Extract bounded plain text from documents downloaded by the WeChat bridge.

This module deliberately has a tiny dependency surface.  The bridge invokes it
with AstrBot's Python (which provides pypdf); DOCX and text formats only need the
standard library.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".doc",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".log",
    ".xml",
    ".yaml",
    ".yml",
}

_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".log",
    ".xml",
    ".yaml",
    ".yml",
}


def _decode_text(data: bytes) -> tuple[str, str]:
    """Decode common Chinese and Unicode text files without corrupting content."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return data.decode("utf-32"), "utf-32"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"

    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue

    try:
        from charset_normalizer import from_bytes

        match = from_bytes(data).best()
        if match is not None:
            return str(match), match.encoding or "auto"
    except Exception:
        pass
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def _normalize_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _sample_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Keep evenly distributed windows when a document exceeds the prompt budget."""
    if len(text) <= max_chars:
        return text, False

    section_count = 10
    marker_budget = section_count * 42 + 160
    section_size = max(200, (max_chars - marker_budget) // section_count)
    last_start = max(0, len(text) - section_size)
    sections: list[str] = []
    for index in range(section_count):
        start = round(last_start * index / (section_count - 1))
        end = min(len(text), start + section_size)
        sections.append(
            f"\n[文档抽样片段 {index + 1}/{section_count}，原文字符 {start + 1}-{end}]\n"
            + text[start:end]
        )
    notice = (
        f"[文档过长：原文共 {len(text)} 字符，以下为覆盖全文首尾及中间位置的均匀抽样。]"
    )
    return (notice + "".join(sections))[:max_chars], True


def _extract_pdf(path: Path) -> tuple[str, dict]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF 解析组件 pypdf 未安装") from exc

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            if reader.decrypt("") == 0:
                raise RuntimeError("PDF 已加密，无法读取正文")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("PDF 已加密，无法读取正文") from exc

    pages: list[str] = []
    image_pages: list[int] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            if len(page.images) > 0:
                image_pages.append(index)
        except Exception:
            # Image discovery is only an optimization for page selection.  A
            # page can still be rendered even if its resource tree is unusual.
            pass
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            page_text = f"[第 {index} 页解析失败：{type(exc).__name__}]"
        if page_text.strip():
            pages.append(f"[第 {index} 页]\n{page_text.strip()}")
    return "\n\n".join(pages), {
        "page_count": len(reader.pages),
        "image_pages": image_pages,
    }


def _select_evenly(values: list[int], count: int) -> list[int]:
    if count <= 0 or not values:
        return []
    if len(values) <= count:
        return list(values)
    if count == 1:
        return [values[0]]
    indices = {
        round(index * (len(values) - 1) / (count - 1))
        for index in range(count)
    }
    return [values[index] for index in sorted(indices)]


def _select_pdf_render_pages(
    page_count: int,
    image_pages: list[int],
    max_pages: int,
) -> list[int]:
    """Prioritize pages containing raster images, then fill gaps across the PDF."""
    if page_count <= 0 or max_pages <= 0:
        return []
    all_pages = list(range(1, page_count + 1))
    if page_count <= max_pages:
        return all_pages

    valid_image_pages = sorted({page for page in image_pages if 1 <= page <= page_count})
    if len(valid_image_pages) >= max_pages:
        return _select_evenly(valid_image_pages, max_pages)

    selected = set(valid_image_pages)
    remaining = [page for page in all_pages if page not in selected]
    selected.update(_select_evenly(remaining, max_pages - len(selected)))
    return sorted(selected)


def _render_pdf_pages(
    path: Path,
    output_prefix: Path,
    page_numbers: list[int],
    max_dimension: int,
    quality: int,
) -> list[dict]:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError("PDF 页面渲染组件 pypdfium2 未安装") from exc

    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(str(path))
    rendered: list[dict] = []
    try:
        for page_number in page_numbers:
            page = document[page_number - 1]
            bitmap = None
            image = None
            try:
                width, height = page.get_size()
                longest_side = max(float(width), float(height), 1.0)
                scale = min(3.0, max(1.0, max_dimension / longest_side))
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil().convert("RGB")
                output_path = Path(f"{output_prefix}_page_{page_number:04d}.jpg")
                image.save(
                    output_path,
                    format="JPEG",
                    quality=quality,
                    optimize=True,
                )
                rendered.append({
                    "page": page_number,
                    "path": str(output_path.resolve()),
                    "width": image.width,
                    "height": image.height,
                })
            finally:
                if image is not None:
                    image.close()
                if bitmap is not None:
                    bitmap.close()
                page.close()
    finally:
        document.close()
    return rendered


def _extract_docx(path: Path) -> tuple[str, dict]:
    with zipfile.ZipFile(path) as archive:
        total_uncompressed = sum(item.file_size for item in archive.infolist())
        if total_uncompressed > 128 * 1024 * 1024:
            raise RuntimeError("DOCX 解压后内容过大，已拒绝解析")
        try:
            document_xml = archive.read("word/document.xml")
        except KeyError as exc:
            raise RuntimeError("文件不是有效的 DOCX 文档") from exc

    root = ET.fromstring(document_xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    lines: list[str] = []
    for paragraph in root.iter(namespace + "p"):
        fragments: list[str] = []
        for node in paragraph.iter():
            if node.tag == namespace + "t" and node.text:
                fragments.append(node.text)
            elif node.tag == namespace + "tab":
                fragments.append("\t")
            elif node.tag in {namespace + "br", namespace + "cr"}:
                fragments.append("\n")
        line = "".join(fragments).strip()
        if line:
            lines.append(line)
    return "\n".join(lines), {"paragraph_count": len(lines)}


def _extract_doc(path: Path) -> tuple[str, dict]:
    antiword = shutil.which("antiword")
    if not antiword:
        raise RuntimeError("旧版 DOC 解析工具 antiword 不可用；可另存为 DOCX 后重试")
    completed = subprocess.run(
        [antiword, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        error, _ = _decode_text(completed.stderr)
        raise RuntimeError(f"旧版 DOC 解析失败：{error.strip()[:200] or '未知错误'}")
    text, encoding = _decode_text(completed.stdout)
    return text, {"encoding": encoding}


def extract_document(
    path: str | Path,
    max_chars: int = 100_000,
    *,
    pdf_render_prefix: str | Path | None = None,
    max_pdf_pages: int = 8,
    render_max_dimension: int = 1600,
    render_quality: int = 82,
) -> dict:
    file_path = Path(path)
    extension = file_path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise RuntimeError(f"暂不支持 {extension or '无扩展名'} 格式")
    if not file_path.is_file():
        raise RuntimeError("下载后的文档文件不存在")

    metadata: dict = {}
    if extension == ".pdf":
        text, metadata = _extract_pdf(file_path)
    elif extension == ".docx":
        text, metadata = _extract_docx(file_path)
    elif extension == ".doc":
        text, metadata = _extract_doc(file_path)
    elif extension in _TEXT_EXTENSIONS:
        text, encoding = _decode_text(file_path.read_bytes())
        metadata = {"encoding": encoding}
    else:  # Kept explicit so additions to the whitelist cannot silently misparse.
        raise RuntimeError(f"暂不支持 {extension} 格式")

    text = _normalize_text(text)
    total_chars = len(text)
    rendered_pages: list[dict] = []
    render_error = ""
    if extension == ".pdf" and pdf_render_prefix:
        page_numbers = _select_pdf_render_pages(
            int(metadata.get("page_count") or 0),
            list(metadata.get("image_pages") or []),
            min(12, max(1, max_pdf_pages)),
        )
        try:
            rendered_pages = _render_pdf_pages(
                file_path,
                Path(pdf_render_prefix),
                page_numbers,
                min(2400, max(800, render_max_dimension)),
                min(95, max(60, render_quality)),
            )
        except Exception as exc:
            render_error = str(exc)

    if not text:
        if extension == ".pdf" and rendered_pages:
            text = (
                "[PDF 未提取到文本层。请主要依据随消息附上的 PDF 页面图像进行理解和总结。]"
            )
        elif extension == ".pdf":
            detail = f"；页面渲染失败：{render_error}" if render_error else ""
            raise RuntimeError(f"PDF 未提取到可读文字，可能是扫描件或纯图片 PDF{detail}")
        else:
            raise RuntimeError("文档中未提取到可读文字")

    sampled_text, truncated = _sample_text(text, max_chars)
    return {
        "ok": True,
        "file_name": file_path.name,
        "extension": extension,
        "text": sampled_text,
        "total_chars": total_chars,
        "included_chars": len(sampled_text),
        "truncated": truncated,
        "rendered_pages": rendered_pages,
        "render_error": render_error,
        **metadata,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--max-chars", type=int, default=100_000)
    parser.add_argument("--pdf-render-prefix")
    parser.add_argument("--max-pdf-pages", type=int, default=8)
    parser.add_argument("--render-max-dimension", type=int, default=1600)
    parser.add_argument("--render-quality", type=int, default=82)
    args = parser.parse_args()
    max_chars = min(500_000, max(1_000, args.max_chars))
    try:
        result = extract_document(
            args.path,
            max_chars=max_chars,
            pdf_render_prefix=args.pdf_render_prefix,
            max_pdf_pages=args.max_pdf_pages,
            render_max_dimension=args.render_max_dimension,
            render_quality=args.render_quality,
        )
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
