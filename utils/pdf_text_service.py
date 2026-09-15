"""Local hybrid PDF text extraction for research-profile calibration.

PDFs remain at the path chosen by the user.  This module reads text only and
never uploads a file itself; the caller decides whether extracted text may be
sent to the user's configured AI provider.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from pypdf import PdfReader


class PdfTextExtractionError(RuntimeError):
    """A concise, user-readable error for a locally linked PDF."""


def pdf_fingerprint(path: str | Path) -> str:
    """Return a cheap fingerprint so unchanged PDFs are not analysed twice."""
    target = Path(path).expanduser()
    stat = target.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _clean_page_text(value: object) -> str:
    return "\n".join(line.strip() for line in str(value or "").splitlines() if line.strip())


def _has_useful_native_text(text: str, threshold: int = 8) -> bool:
    meaningful = sum(character.isalnum() or "\u4e00" <= character <= "\u9fff" for character in text)
    return meaningful >= threshold


class _PdfiumPageRenderer:
    def __init__(self, path: Path) -> None:
        try:
            import pypdfium2 as pdfium
        except ImportError as error:
            raise PdfTextExtractionError("本机 PDF 渲染组件未安装") from error
        try:
            self._document = pdfium.PdfDocument(str(path))
        except Exception as error:  # noqa: BLE001 - PDFium error types vary
            raise PdfTextExtractionError("无法渲染扫描版 PDF") from error

    def render(self, _path: Path, page_index: int) -> Any:
        try:
            page = self._document[page_index]
            bitmap = page.render(scale=300 / 72)
            image = bitmap.to_pil().convert("RGB")
            page.close()
            return image
        except Exception as error:  # noqa: BLE001 - PDFium error types vary
            raise PdfTextExtractionError(f"第 {page_index + 1} 页渲染失败") from error

    def close(self) -> None:
        try:
            self._document.close()
        except Exception:  # noqa: BLE001 - best-effort native resource cleanup
            pass


def _create_rapidocr_engine() -> Callable[[Any], Any]:
    try:
        from rapidocr import RapidOCR
    except ImportError as error:
        raise PdfTextExtractionError("本机 OCR 组件未安装，请重新安装科研助手") from error
    try:
        return RapidOCR()
    except Exception as error:  # noqa: BLE001 - model/backend errors vary
        raise PdfTextExtractionError("本机 OCR 模型加载失败") from error


def _normalise_ocr_result(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        text = _clean_page_text(raw.get("text", ""))
        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return {"text": text, "confidence": max(0.0, min(1.0, confidence)), "model": str(raw.get("model", ""))}

    texts = getattr(raw, "txts", None)
    scores = getattr(raw, "scores", None)
    if isinstance(texts, (list, tuple)):
        clean_texts = [_clean_page_text(value) for value in texts if _clean_page_text(value)]
        clean_scores = [float(value) for value in scores or [] if isinstance(value, (int, float))]
        return {
            "text": "\n".join(clean_texts),
            "confidence": sum(clean_scores) / len(clean_scores) if clean_scores else 0.0,
            "model": "rapidocr",
        }

    rows = raw
    if isinstance(raw, tuple) and raw:
        rows = raw[0]
    if isinstance(rows, list):
        collected_text: list[str] = []
        collected_scores: list[float] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            text = _clean_page_text(row[1])
            if text:
                collected_text.append(text)
            if len(row) >= 3 and isinstance(row[2], (int, float)):
                collected_scores.append(float(row[2]))
        return {
            "text": "\n".join(collected_text),
            "confidence": sum(collected_scores) / len(collected_scores) if collected_scores else 0.0,
            "model": "rapidocr",
        }
    return {"text": "", "confidence": 0.0, "model": "rapidocr"}


def _page_fingerprint(document_fingerprint: str, page_number: int, source: str, text: str) -> str:
    value = f"{document_fingerprint}:{page_number}:{source}:{text}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_OCR_PAGE_CACHE: dict[tuple[str, int, str], dict[str, Any]] = {}


def extract_pdf_full_text(
    path: str | Path,
    *,
    ocr_engine: Callable[[Any], Any] | None = None,
    page_renderer: Callable[[Path, int], Any] | None = None,
    progress: Callable[[str, int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Read native pages and OCR only pages without a useful text layer."""
    target = Path(path).expanduser()
    if not target.is_file():
        raise PdfTextExtractionError("文件不存在或已被移动")
    if target.suffix.casefold() != ".pdf":
        raise PdfTextExtractionError("选择的文件不是 PDF")
    renderer_owner: _PdfiumPageRenderer | None = None
    try:
        reader = PdfReader(str(target))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise PdfTextExtractionError("PDF 已加密，无法读取全文")
            except Exception as error:  # noqa: BLE001 - pypdf varies by encryption mode
                if isinstance(error, PdfTextExtractionError):
                    raise
                raise PdfTextExtractionError("PDF 已加密，无法读取全文") from error
        total_pages = len(reader.pages)
        if cancelled and cancelled():
            raise PdfTextExtractionError("已取消 OCR")
        fingerprint = pdf_fingerprint(target)
        pages: list[dict[str, Any]] = []
        warnings: list[str] = []
        ocr_page_count = 0
        engine = ocr_engine
        for number, page in enumerate(reader.pages, 1):
            if cancelled and cancelled():
                raise PdfTextExtractionError("已取消 OCR")
            try:
                native_text = _clean_page_text(page.extract_text() or "")
            except Exception:  # A damaged page should not hide text on other pages.
                native_text = ""
            if _has_useful_native_text(native_text):
                page_result = {
                    "page_number": number,
                    "text": native_text,
                    "source": "native",
                    "confidence": 1.0,
                    "fingerprint": _page_fingerprint(fingerprint, number, "native", native_text),
                }
                pages.append(page_result)
                if progress:
                    progress(f"已读取第 {number} 页文字层", number, total_pages, "native")
                continue

            ocr_page_count += 1
            if page_renderer is None:
                if renderer_owner is None:
                    renderer_owner = _PdfiumPageRenderer(target)
                render = renderer_owner.render
            else:
                render = page_renderer
            if engine is None:
                engine = _create_rapidocr_engine()
            model_id = str(getattr(engine, "model_name", "rapidocr"))
            cache_key = (fingerprint, number, model_id)
            cached = _OCR_PAGE_CACHE.get(cache_key) if ocr_engine is None else None
            if cached is None:
                image = render(target, number - 1)
                if cancelled and cancelled():
                    raise PdfTextExtractionError("已取消 OCR")
                try:
                    ocr_result = _normalise_ocr_result(engine(image))
                except PdfTextExtractionError:
                    raise
                except Exception as error:  # noqa: BLE001 - OCR engines vary
                    raise PdfTextExtractionError(f"第 {number} 页 OCR 失败") from error
                if ocr_engine is None:
                    _OCR_PAGE_CACHE[cache_key] = dict(ocr_result)
            else:
                ocr_result = dict(cached)
            text = _clean_page_text(ocr_result.get("text", ""))
            confidence = float(ocr_result.get("confidence", 0.0) or 0.0)
            if text and confidence < 0.55:
                warnings.append(f"第 {number} 页 OCR 置信度较低（{confidence:.0%}），请核对关键词")
            if not text:
                warnings.append(f"第 {number} 页未识别到文字")
            pages.append(
                {
                    "page_number": number,
                    "text": text,
                    "source": "ocr",
                    "confidence": confidence,
                    "fingerprint": _page_fingerprint(fingerprint, number, "ocr", text),
                }
            )
            if progress:
                progress(f"已完成第 {number} 页 OCR", number, total_pages, "ocr")
    except PdfTextExtractionError:
        raise
    except Exception as error:  # noqa: BLE001 - show a clean desktop message
        raise PdfTextExtractionError("无法读取 PDF；文件可能损坏或不受支持") from error
    finally:
        if renderer_owner is not None:
            renderer_owner.close()
    text_pages = [page for page in pages if str(page.get("text", "")).strip()]
    full_text = "\n\n".join(
        f"[第 {page['page_number']} 页]\n{page['text']}" for page in text_pages
    ).strip()
    if not full_text:
        raise PdfTextExtractionError("未提取到可读文字；OCR 未能识别该 PDF")
    if progress:
        progress("PDF 文字提取完成", total_pages, total_pages, "complete")
    return {
        "path": str(target),
        "name": target.name,
        "page_count": total_pages,
        "text_page_count": len(text_pages),
        "ocr_page_count": ocr_page_count,
        "char_count": len(full_text),
        "fingerprint": fingerprint,
        "text": full_text,
        "pages": pages,
        "warnings": warnings,
    }


def split_pdf_text_for_ai(text: str, chunk_size: int = 14_000) -> list[str]:
    """Split the complete extracted text at natural boundaries without dropping it."""
    source = str(text or "").strip()
    if not source:
        return []
    pieces: list[str] = []
    while len(source) > chunk_size:
        boundary = source.rfind("\n\n", int(chunk_size * 0.58), chunk_size)
        if boundary < int(chunk_size * 0.58):
            boundary = source.rfind("\n", int(chunk_size * 0.58), chunk_size)
        if boundary < int(chunk_size * 0.58):
            boundary = chunk_size
        pieces.append(source[:boundary].strip())
        source = source[boundary:].lstrip()
    if source:
        pieces.append(source)
    return pieces
