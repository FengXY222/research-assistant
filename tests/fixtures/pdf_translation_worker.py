"""Small process fixture: no AI/network/model loading."""
import json
from pathlib import Path
import subprocess
import sys
import time

config = json.loads(sys.stdin.readline())
mode = sys.argv[1] if len(sys.argv) > 1 else "complete"
if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    print(json.dumps({"type": "child", "pid": child.pid}), flush=True)
    time.sleep(300)
elif mode == "cancel":
    for line in sys.stdin:
        if json.loads(line).get("command") == "cancel":
            print(json.dumps({"type": "cancelled"}), flush=True)
            raise SystemExit(2)
else:
    time.sleep(0.1)
    output = Path(config["output"])
    output.mkdir(parents=True, exist_ok=True)
    path = output / "test.pdf"
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(path)
    print(json.dumps({"type": "finish", "paths": {"mono_pdf_path": str(path)}}), flush=True)
