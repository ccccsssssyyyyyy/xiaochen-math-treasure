## 小陈的数学宝藏

面向中学数学老师的本地题库与组卷备课工具。数据全部保存在本机，无需联网。

### 下载与启动

| 平台 | 文件 | 启动方式 |
|---|---|---|
| Windows | `MathBank-Windows-x64.zip` | 解压到普通文件夹，双击 `启动题库系统.bat` |
| macOS（Apple 芯片） | `MathBank-macOS-AppleSilicon.zip` | 解压到普通文件夹，双击 `启动题库系统.command` |
| macOS（Intel 芯片） | `MathBank-macOS-Intel.zip` | 解压到普通文件夹，双击 `启动题库系统.command` |

三个包都**自带 Python 运行时**：不需要预先安装 Python，也不需要联网安装依赖，解压双击即跑。

Mac 不确定自己是哪种芯片：左上角  →「关于本机」，「芯片」一行写着 Apple M* 就下 AppleSilicon 版，写着 Intel 就下 Intel 版。下错了启动器会提示该换哪个包。

启动后浏览器打开 <http://127.0.0.1:8000>。

> 覆盖升级前请先备份 `*.db`、`.env`、`data_backup/`、`static/uploads/`。详细步骤见 README 的「版本升级与数据备份」。

### 首次使用

点网页右上角 **⚙️ 设置**，填入**你自己的** API Key（DeepSeek / 阿里百炼 / 硅基流动等）。程序不内置、不代填任何密钥。

未配置 Key 时，录入、预览、检索、导出源码仍可正常使用；AI 拆解 / 解题 / OCR 需要有效 Key。

### 可选依赖

只有用到 **TikZ 几何重绘** 或 **一键导出 PDF** 时才需要本地 LaTeX：macOS 装 [MacTeX](https://www.tug.org/mactex/)，Windows 装 [TeX Live](https://www.tug.org/texlive/) 或 MiKTeX。

完整的安装、配置与使用说明见 [README](https://github.com/ccccsssssyyyyyy/xiaochen-math-treasure#快速开始)。
