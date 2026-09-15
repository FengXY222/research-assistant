# 科研助手 v12.1 第三方项目说明

特刊发现流程参考了 `kanxh/call4paper-mcp` 的 OpenAlex 期刊路由、出版社专用解析和静态请求失败后切换 HTTP 客户端的设计。科研助手没有嵌入该项目的 MCP 服务进程、Playwright 浏览器或运行时依赖，而是按桌面应用的数据结构重新实现了所需逻辑。

- 项目：https://github.com/kanxh/call4paper-mcp
- 许可证：MIT
- Copyright (c) 2026 KaiH1124

MIT License 允许使用、复制、修改、合并、发布、分发、再许可和销售软件副本，但须在软件的所有副本或重要部分中保留版权声明和许可声明。软件按“原样”提供，不附带任何明示或默示担保。

科研助手还通过公开接口读取 OpenAlex 元数据、Taylor & Francis 官方征稿数据，并在 ScienceDirect 限制直接自动访问时使用 Jina Reader 作为发现通道。经只读通道发现的征稿不会冒充官网已核验结果。
