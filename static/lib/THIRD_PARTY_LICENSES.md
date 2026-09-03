# 第三方依赖许可证与归属声明

本项目在 `static/lib/` 下 vendored 了若干前端依赖以实现纯本地运行（无需 CDN）。这些库与字体保留其原始许可证。本文件列出各组件的许可证类型、归属要求与获取方式，便于合规审计。

如您在自己的派生中替换/移除了这些文件，请相应删除本文件对应段落。

---

## JavaScript 库

| 库 | 版本 | 许可证 | 来源 |
|---|---|---|---|
| **DOMPurify** | 3.0.5 | Apache License 2.0 / Mozilla Public License 2.0（双许可） | https://github.com/cure53/DOMPurify |
| **Font Awesome**（CSS 与 webfonts） | 6.x（2023 Fonticons, Inc.） | **代码 MIT · 字体 SIL OFL 1.1 · 图标 CC BY 4.0** | https://fontawesome.com |
| **KaTeX** | 见 `static/lib/katex/` 实际版本 | MIT | https://katex.org |
| **marked** | 4.3.0 | MIT | https://github.com/markedjs/marked |
| **Tailwind CSS** | 见 `static/lib/tailwind/tailwind.min.js` | MIT | https://tailwindcss.com |

### 归属要点

- **DOMPurify**：保留 `LICENSE` 文件中的版权声明即可，无附加署名要求。
- **Font Awesome**：图标的 **CC BY 4.0** 部分要求署名。可在应用界面「关于」或文档中保留「Font Awesome by Fonticons, Inc.」字样；本项目 README 致谢段落中已包含。
- **KaTeX / marked / Tailwind**：MIT，仅需保留版权声明（已包含在 vendored 文件首部）。

---

## 字体（webfonts）

| 字体 | 许可证 | 来源 |
|---|---|---|
| **Inter**（`inter-latin.woff2`） | SIL Open Font License 1.1 | https://github.com/rsms/inter |
| **Outfit**（`outfit-latin.woff2`） | SIL Open Font License 1.1 | https://github.com/sharanda/outfit |

SIL OFL 1.1 允许自由使用、嵌入、再分发，禁止单独出售字体文件。如您在自己的派生中替换字体，请相应删除对应条目。

---

## Python 依赖

Python 依赖（`requirements.txt`、`requirements-dev.txt`、`requirements-windows.txt`）通过 PyPI 安装，每个依赖保留其原始许可证。完整列表请见：
- https://docs.fastapi.io/
- https://www.sqlalchemy.org/
- https://pymupdf.io/（**PyMuPDF 自 1.24 起为 AGPL-3.0**，与本项目 AGPL-3.0 兼容）
- https://github.com/firecrawl/pdf-inspector（MIT）
- 其他依赖详见各自的 PyPI 页面。

---

## 本项目 AGPL-3.0 与 vendored 组件的兼容性

本项目按 **GNU AGPL-3.0** 分发。vendored 组件均为宽松许可（MIT / Apache-2.0 / MPL-2.0 / SIL OFL / CC BY），与 AGPL-3.0 兼容；其中 PyMuPDF 同为 AGPL-3.0，强弱兼容。

如您对本项目的合规细节有疑问，欢迎在仓库 Issue 区提问。