"""Hybrid native-text and local OCR extraction for text and scan PDFs."""

from __future__ import annotations

from pathlib import Path

import pytest

from utils import pdf_text_service
from utils.pdf_text_service import PdfTextExtractionError, extract_pdf_full_text


class FakePage:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def extract_text(self) -> str:
        return self.text


class FakeReader:
    def __init__(self, texts: list[str], *, encrypted: bool = False) -> None:
        self.pages = [FakePage(text) for text in texts]
        self.is_encrypted = encrypted

    def decrypt(self, password: str) -> int:
        return 0


@pytest.fixture
def dummy_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "fixture.pdf"
    path.write_bytes(b"%PDF-test-fixture")
    return path


def test_native_pages_never_render_or_call_ocr(dummy_pdf: Path, monkeypatch) -> None:
    monkeypatch.setattr(pdf_text_service, "PdfReader", lambda path: FakeReader(["Native soil carbon text"]));

    def forbidden(*args, **kwargs):
        raise AssertionError("native text page must not use OCR")

    result = extract_pdf_full_text(dummy_pdf, ocr_engine=forbidden, page_renderer=forbidden)

    assert result["ocr_page_count"] == 0
    assert result["pages"][0]["source"] == "native"
    assert result["pages"][0]["text"] == "Native soil carbon text"


def test_image_only_page_is_rendered_and_ocr_text_is_returned(dummy_pdf: Path, monkeypatch) -> None:
    monkeypatch.setattr(pdf_text_service, "PdfReader", lambda path: FakeReader([""]))
    rendered = []
    calls = []

    def render(path: Path, page_index: int):
        rendered.append((path, page_index))
        return f"image-{page_index}"

    def ocr(image):
        calls.append(image)
        return {"text": "soil organic carbon", "confidence": 0.93, "model": "fixture"}

    result = extract_pdf_full_text(dummy_pdf, ocr_engine=ocr, page_renderer=render)

    assert rendered == [(dummy_pdf, 0)]
    assert calls == ["image-0"]
    assert result["ocr_page_count"] == 1
    assert result["pages"][0]["source"] == "ocr"
    assert result["pages"][0]["confidence"] == pytest.approx(0.93)
    assert "soil organic carbon" in result["text"]


def test_mixed_pdf_preserves_page_order_and_emits_progress(dummy_pdf: Path, monkeypatch) -> None:
    monkeypatch.setattr(pdf_text_service, "PdfReader", lambda path: FakeReader(["native first", "", "native third"]))
    progress = []

    result = extract_pdf_full_text(
        dummy_pdf,
        ocr_engine=lambda image: {"text": "ocr second", "confidence": 0.88},
        page_renderer=lambda path, index: f"page-{index}",
        progress=lambda message, current, total, stage: progress.append((current, total, stage)),
    )

    assert [page["text"] for page in result["pages"]] == ["native first", "ocr second", "native third"]
    assert result["text"].index("native first") < result["text"].index("ocr second") < result["text"].index("native third")
    assert progress[-1] == (3, 3, "complete")


def test_cancellation_stops_before_the_next_ocr_page(dummy_pdf: Path, monkeypatch) -> None:
    monkeypatch.setattr(pdf_text_service, "PdfReader", lambda path: FakeReader(["", ""]))
    checks = iter([False, False, True])

    with pytest.raises(PdfTextExtractionError, match="已取消 OCR"):
        extract_pdf_full_text(
            dummy_pdf,
            ocr_engine=lambda image: {"text": "first", "confidence": 0.9},
            page_renderer=lambda path, index: f"page-{index}",
            cancelled=lambda: next(checks, True),
        )


def test_low_confidence_ocr_is_kept_with_a_visible_warning(dummy_pdf: Path, monkeypatch) -> None:
    monkeypatch.setattr(pdf_text_service, "PdfReader", lambda path: FakeReader([""]))

    result = extract_pdf_full_text(
        dummy_pdf,
        ocr_engine=lambda image: {"text": "uncertain phrase", "confidence": 0.31},
        page_renderer=lambda path, index: object(),
    )

    assert result["pages"][0]["text"] == "uncertain phrase"
    assert result["warnings"]
    assert "置信度" in result["warnings"][0]


def test_encrypted_pdf_fails_before_rendering(dummy_pdf: Path, monkeypatch) -> None:
    monkeypatch.setattr(pdf_text_service, "PdfReader", lambda path: FakeReader([""], encrypted=True))

    with pytest.raises(PdfTextExtractionError, match="已加密"):
        extract_pdf_full_text(
            dummy_pdf,
            ocr_engine=lambda image: {"text": "must not run", "confidence": 1},
            page_renderer=lambda path, index: (_ for _ in ()).throw(AssertionError("must not render")),
        )
