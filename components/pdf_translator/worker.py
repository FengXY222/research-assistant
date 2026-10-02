"""One document per process. Protocol: JSON lines on stdin/stdout, logs on stderr."""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
from pathlib import Path
import sys
import threading
import subprocess


class PDFHelperProcess:
    """Pinned BabelDOC helper adapter without Windows multiprocessing bootstrap.

    Retains the upstream timeouts and process isolation for native PDF operations.
    """
    def __init__(self, target, args):
        self.operation = {"_subset_fonts_process": "subset", "_save_pdf_clean_process": "save"}[target.__name__]
        self.args = args
        self.process = None

    def start(self):
        self.process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "--pdf-helper"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.process.stdin.write(json.dumps({"operation": self.operation, "args": self.args}, default=str).encode("utf-8"))
        self.process.stdin.close()

    def is_alive(self):
        return self.process.poll() is None

    @property
    def exitcode(self):
        return self.process.poll()

    def join(self, timeout=None):
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass

    def terminate(self):
        self.process.terminate()

    def kill(self):
        self.process.kill()


def pdf_helper():
    import pymupdf
    config = json.loads(sys.stdin.read())
    args = config["args"]
    with pymupdf.open(args[0]) as document:
        if config["operation"] == "subset":
            document.subset_fonts(fallback=False)
            document.save(args[1])
        elif config["operation"] == "save":
            document.save(args[1], garbage=args[2], deflate=args[3], clean=args[4], deflate_fonts=args[5], linear=args[6])
        else:
            raise ValueError("Unknown PDF helper")
    return 0


def emit(event):
    sys.__stdout__.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    sys.__stdout__.flush()


async def run(config):
    # Keep engine prints away from the machine-readable channel.
    sys.stdout = sys.stderr
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = "2"
    # ONNX does not honor OMP_NUM_THREADS for its own inference thread pool.
    import onnxruntime as ort
    import cv2
    cv2.setNumThreads(2)
    original_session = ort.InferenceSession

    class LimitedSession(original_session):
        def __init__(self, path_or_bytes, sess_options=None, *args, **kwargs):
            options = sess_options or ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            super().__init__(path_or_bytes, options, *args, **kwargs)

    ort.InferenceSession = LimitedSession  # This process exits after one document.
    from pdf2zh_next.config.model import SettingsModel, TranslationSettings, PDFSettings
    from pdf2zh_next.config.translate_engine_model import OpenAISettings
    emit({"type": "progress_start", "stage": "正在准备独立翻译引擎", "overall_progress": 0})
    # Pin this adapter with the engine versions. The outer process already supplies
    # isolation; a second multiprocessing spawn stalled on this Windows machine.
    from pdf2zh_next.high_level import create_babeldoc_config
    from babeldoc.format.pdf.high_level import async_translate
    from babeldoc.format.pdf.document_il.backend import pdf_creater
    pdf_creater.Process = PDFHelperProcess

    settings = SettingsModel(
        report_interval=0.5,
        translation=TranslationSettings(
            lang_in=config.get("lang_in", "en"), lang_out=config.get("lang_out", "zh-CN"),
            output=config["output"], qps=int(config.get("qps", 2)),
            pool_max_workers=int(config.get("workers", 2)),
            no_auto_extract_glossary=True,
        ),
        pdf=PDFSettings(pages=config.get("pages") or None, watermark_output_mode="no_watermark"),
        translate_engine_settings=OpenAISettings(
            openai_model=config["model"], openai_base_url=config["base_url"],
            openai_api_key=config["api_key"], openai_timeout="60",
        ),
    )
    from urllib.parse import urlsplit
    if (urlsplit(config["base_url"]).hostname or "").lower() == "api.deepseek.com":
        settings.translate_engine_settings._openai_extra_body = {"thinking": {"type": "disabled"}}

    settings.validate_settings()
    translation_config = None
    cancelled = False

    async def translate():
        nonlocal translation_config
        translation_config = await asyncio.to_thread(create_babeldoc_config, settings, Path(config["input"]))
        # SDK and translator retries are bounded, so a failing service cannot
        # silently occupy the single-document queue for hundreds of attempts.
        from tenacity import stop_after_attempt
        for translator in (translation_config.translator, translation_config.term_extraction_translator):
            if translator is None:
                continue
            client = getattr(translator, "client", None)
            if client is not None and hasattr(client, "with_options"):
                translator.client = client.with_options(max_retries=1)
            for name in ("do_translate", "do_llm_translate"):
                method = getattr(translator, name, None)
                if method is not None and hasattr(method, "retry"):
                    method.retry.stop = stop_after_attempt(3)
        if cancelled:
            translation_config.cancel_translation()
            raise asyncio.CancelledError
        async for event in async_translate(translation_config):
            if event.get("type") == "finish":
                result = event.get("translate_result")
                if result is None:
                    result = event.get("result")
                paths = {}
                for key in ("mono_pdf_path", "dual_pdf_path", "no_watermark_mono_pdf_path", "no_watermark_dual_pdf_path"):
                    value = result.get(key) if isinstance(result, dict) else getattr(result, key, None)
                    if value:
                        paths[key] = str(value)
                import pymupdf
                for path in dict.fromkeys(paths.values()):
                    with pymupdf.open(path) as document:
                        if not len(document):
                            raise RuntimeError("翻译结果没有页面。")
                        if config.get("lang_out", "zh-CN").lower().startswith("zh"):
                            if not any(any('\u4e00' <= char <= '\u9fff' for char in page.get_text()) for page in document):
                                raise RuntimeError("翻译结果中未检测到中文，不能将原文回退当作成功。")
                if not paths:
                    raise RuntimeError("翻译引擎未返回可用的输出文件。")
                emit({"type": "finish", "paths": paths})
            elif event.get("type") == "error":
                raise RuntimeError(str(event.get("error", "PDF 翻译失败")))
            else:
                emit({key: value for key, value in event.items() if key not in ("translate_result", "result")})

    task = asyncio.create_task(translate())
    loop = asyncio.get_running_loop()

    def commands():
        for line in sys.stdin:
            try:
                command = json.loads(line)
            except ValueError:
                continue
            if command.get("command") == "cancel":
                def cancel():
                    nonlocal cancelled
                    cancelled = True
                    if translation_config is not None:
                        translation_config.cancel_translation()
                    task.cancel()
                loop.call_soon_threadsafe(cancel)
                return

    threading.Thread(target=commands, daemon=True).start()
    try:
        await task
        if cancelled:
            emit({"type": "cancelled"})
            return 2
        return 0
    except asyncio.CancelledError:
        emit({"type": "cancelled"})
        return 2


def main():
    # Embedded Python's isolated _pth mode ignores PYTHONIOENCODING.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        if "--pdf-helper" in sys.argv:
            return pdf_helper()
        config = json.loads(sys.stdin.readline())
        Path(config["output"]).mkdir(parents=True, exist_ok=True)
        return asyncio.run(run(config))
    except Exception as error:
        # Avoid leaking API credentials through errors or URLs.
        message = str(error)
        key = locals().get("config", {}).get("api_key", "")
        if key:
            message = message.replace(key, "[redacted]")
        emit({"type": "error", "error": message[:2000]})
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
