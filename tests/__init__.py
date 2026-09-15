"""科研助手的隔离自动化测试包。"""

# Package-style test invocation imports this module before any test code.  Keep
# the data-root override here so persistence modules cannot bind to the source
# tree during an isolated test run.
from . import _data_root as _data_root  # noqa: F401
