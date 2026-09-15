"""Small v11 entry point that reuses the confirmed Research Inbox write flow."""

from __future__ import annotations

from typing import Any

from ui.workflow_dialogs import ResearchInboxDialog


def classify_quick_capture_locally(text: str) -> dict[str, Any]:
    """Offer a reversible local draft; callers must still explicitly save it."""
    clean = str(text or "").strip()
    folded = clean.casefold()
    if any(token in folded for token in ("期刊", "journal", "投稿", "外审", "审稿")):
        kind = "paper"
    elif any(token in folded for token in ("阅读", "待读", "paper", "论文题目", "文献")) and not any(
        token in folded for token in ("查一下", "想法", "可以研究")
    ):
        kind = "reading"
    elif any(token in folded for token in ("修改", "回复", "绘制", "完成", "今天", "截止", "会议")):
        kind = "task"
    else:
        kind = "inspiration"
    return {"kind": kind, "text": clean, "confirmed": False, "source": "local"}


class QuickCaptureDialog(ResearchInboxDialog):
    """A familiar confirmed-write dialog with a lightweight suggested destination."""

    def __init__(self, parent=None, initial_text: str = "") -> None:
        self.draft = classify_quick_capture_locally(initial_text)
        super().__init__(parent, preset=str(self.draft["kind"]), initial_text=str(self.draft["text"]))
        self.setWindowTitle("快速记录 · 科研助手")
        self.recognition_hint.setText("已按一句话给出保存位置建议；你可修改类别，点击“收入收件箱”后才会写入本地数据。")
