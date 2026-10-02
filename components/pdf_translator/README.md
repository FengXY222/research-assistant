# 独立 PDF 翻译组件

## 使用

侧栏“文献”下面的“工具”进入“工作 → 工具”。第一次打开“翻译设置”时可借用增强服务的地址、模型及已加密密钥作为初始值；保存后是组件自己的配置，主软件 AI 启用开关、全局每秒 5 次限流、空闲任务取消机制均不控制它。

添加 PDF 即开始翻译，其余文件按加入时间排队。默认并发 2、请求速率 2 次/秒，进度事件间隔 0.5 秒。仅打开工具页不会加载翻译模型。翻译结束后进程退出，模型内存归还系统。系统代理默认关闭，可单独开启。

选中任务后可取消、重试、打开输出文件夹。取消先发 JSON 指令，由异步任务调用 `cancel()` 并通知内核；6 秒后仍未退出则清理该任务所属 Windows Job Object 的完整进程树。取消正在翻译的任务会暂停队列，可点击“继续队列”。普通页面操作不取消手动翻译。

关闭应用会保存中断状态；排队和中断任务在重启后不会自动产生 API 消费，需手动继续或重试。重试保留原任务参数、输出目录，使用引擎的翻译缓存；不是逐页精确断点续传。密钥仅在组件设置中用 Windows DPAPI 加密保存，不在任务参数或日志中保存明文。旧任务始终使用保存的地址和模型，但会读取组件当前密钥，因此重试旧任务时需要保证新密钥适用于该地址。

## 环境与部署边界

当前独立运行环境在本目录 `runtime/`，使用 Python 3.12；主程序 `.venv` 不安装翻译引擎。锁定 pdf2zh-next 2.9.0、BabelDOC 0.6.2、PyMuPDF 1.25.2，完整依赖见 `requirements.lock`。模型及字体首次使用才下载到引擎默认 `%USERPROFILE%/.cache/babeldoc`，译文缓存保存在 `%USERPROFILE%/.cache/pdf2zh_next`；离线首次运行不能完成资产下载。

可运行 `install_runtime.ps1 -Python <Python3.12完整路径>`，默认安装到 `%LOCALAPPDATA%/科研助手/Components/pdf_translator/runtime`。组件 Python 需要其所依赖的基础 Python 3.12 持续存在。开发环境的 venv 不能直接复制到别人的电脑作为便携运行环境。

主程序构建仅收集轻量工具页与 worker 源文件，不将大型引擎混入主环境。13.1.8 目录发行版及安装包另外携带独立的 Python 3.12.10 嵌入式环境和锁定依赖，位于应用目录 components/pdf_translator/runtime，不依赖系统 Python。发行后的主程序优先查找这套便携环境；组件缺失时明确提示，不会误报翻译成功。模型、字体和用户译文缓存不打入安装包，首次翻译仍需联网获取模型资产。

## 实现与稳定性

主窗口 → 懒加载工具页 → QProcess 控制器 → 独立 worker → 异步翻译内核。队列及参数存入单独的 `UserData/pdf-translator/jobs.sqlite`（WAL）；每篇结果存入 `results/<任务ID>/`，不覆盖源论文。

真实测试中 next 高层接口的第二层 Windows multiprocessing 子进程停留在启动状态，所以采用固定版本的 `create_babeldoc_config` 适配器与 BabelDOC `async_translate`：仍使用 next 的服务配置、翻译器和缓存，外层 worker 提供隔离，无需再生成嵌套翻译进程。此适配器升级依赖时必须重新回归，不自动升级。

字体压缩和 PDF 保存也采用显式启动的轻量辅助进程，保留上游超时和隔离，避免同样的 multiprocessing 启动停滞。辅助进程只读写本次翻译的中间文件，并继承任务进程树归属；取消时一并清理。

默认关闭自动术语抽取，避免额外的逐段 AI 调用；ONNX 推理线程限制为 2，OpenCV 线程限制为 2。DeepSeek 官方地址单独关闭思考模式，其他兼容服务不发送该私有参数。SDK 重试和限流重试有上限。低并发不等于零内存消耗，版式模型、字体及 PDF 中间结构仍会占用独立进程内存。

模型取消可能需等待底层请求返回，进程树超时清理是最终保障。输出检查包含文件可打开、非空页面、中文检测，不能替代逐句翻译质量审查。

组件上游 pdf2zh-next 与 BabelDOC 声明 AGPL-3.0。后续分发需保留许可与源码信息，独立进程部署不意味着可以忽略许可证要求。

对应上游源码：[PDFMathTranslate-next](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next)、[BabelDOC](https://github.com/funstory-ai/BabelDOC)。发行依赖的具体版本见 requirements.lock；依赖包自己的 LICENSE 和元数据随便携环境一起保留。嵌入式 Python 来自 python.org 官方 3.12.10 Windows x64 发行包，其许可证同样保留。

## 验证

`tests/test_pdf_translation_component.py` 覆盖队列、恢复、参数无密钥、协作取消、超时清理子进程、工具路由与主进程不导入引擎。

`tools/probe_pdf_translation.py` 用独立 Python 运行真实论文测试，报告包含页数、中文字符数、SHA256、源文件一致性、进程树内存峰值及残留进程；只从当前用户已加密配置读取密钥，不输出密钥。报告保存在指定测试输出目录。
