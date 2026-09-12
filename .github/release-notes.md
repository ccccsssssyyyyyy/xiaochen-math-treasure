## 小陈的数学宝藏

面向中学数学老师的本地题库与组卷备课工具。数据全部保存在本机，无需联网。

### 下载与启动

| 平台 | 文件 | 启动方式 |
|---|---|---|
| Windows | `xiaochen-math-treasure-Windows-x64.zip` | 解压到普通文件夹，双击 `启动题库系统.bat` |
| macOS（Apple 芯片） | `xiaochen-math-treasure-macOS-AppleSilicon.zip` | 解压到普通文件夹，双击 `启动题库系统.command` |
| macOS（Intel 芯片） | `xiaochen-math-treasure-macOS-Intel.zip` | 解压到普通文件夹，双击 `启动题库系统.command` |

三个包都**自带 Python 运行时**：不需要预先安装 Python，也不需要联网安装依赖，解压双击即跑。

Mac 不确定自己是哪种芯片：左上角  →「关于本机」，「芯片」一行写着 Apple M* 就下 AppleSilicon 版，写着 Intel 就下 Intel 版。下错了启动器会提示该换哪个包。

启动后浏览器打开 <http://127.0.0.1:8000>。

> **macOS 首次启动前必做一步**：从浏览器下载的 zip 会被系统打上隔离标记，直接双击会弹「Apple 无法验证」。在解压后的目录里打开终端执行一次
> `xattr -dr com.apple.quarantine .`
> 或者：**按住 Control 点启动器 → 选「打开」→ 再点「打开」**。只需做一次。启动器自身也会尝试自动解除。

> 覆盖升级前请先备份 `*.db`、`.env`、`data_backup/`、`static/uploads/`。可以在网页里点【设置 → 关于 → 立即备份】一键生成快照。详细步骤见 README 的「版本升级与数据备份」。

### 本次更新（2.3.0）

针对「上手门槛」做了一轮集中改造：

- **空题库冷启动引导**：首次进入、题库为空时自动弹出三步引导卡（配 Key → 载入示例题 → 选题组卷），不再是一片空白。
- **内置示例题库**：可一键把 8 道示例题（单选 / 多选 / 填空 / 解答）导入题库。**不配 API Key 也能完整体验选题、组卷、预览、导出源码。**
- **运行环境自检**：新增【设置 → 关于 → 运行环境自检】，一眼看出 LaTeX / LibreOffice / pandoc / PDF 解析器是否就绪，缺什么给什么命令。记不清依赖时不用再翻文档。
- **依赖引导弹窗**：导出 PDF 时若缺 LaTeX，弹出安装向导（含国内镜像），不再只报一句「未检测到编译器」。
- **升级向导**：更新弹窗里加入三步升级清单，可一键在页面内先备份，并明确提示「合并时不要选『替换整个文件夹』，否则 `.env` 与数据库会被删掉」。
- **发布包改名**：`MathBank-*.zip` → `xiaochen-math-treasure-*.zip`。2.2.3 及更早版本的包名保持不变，旧链接仍可下载。

### 首次使用

点网页右上角 **⚙️ 设置**，填入**你自己的** API Key（DeepSeek / 阿里百炼 / 硅基流动等）。程序不内置、不代填任何密钥。

未配置 Key 时，示例题导入、录入、预览、检索、组卷、导出源码都能正常使用；AI 拆解 / 解题 / OCR 需要有效 Key。

### 可选依赖

只有用到 **TikZ 几何重绘** 或 **一键导出 PDF** 时才需要本地 LaTeX：macOS 装 [MacTeX](https://www.tug.org/mactex/)，Windows 装 [TeX Live](https://www.tug.org/texlive/) 或 MiKTeX。

完整的安装、配置与使用说明见 [README](https://github.com/ccccsssssyyyyyy/xiaochen-math-treasure#快速开始)。
