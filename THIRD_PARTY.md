# 第三方组件声明 / Third-Party Notices

本项目（StutterTriage）使用以下第三方组件。各组件版权归其各自所有者所有，
并受其各自许可证条款约束。

> 本文件随 EXE 一起分发（`build_exe.ps1` 会把副本放进 `dist/`），
> Release 附件也包含同名文件。

---

## 随包分发的二进制

### PresentMon

| 项 | 值 |
| --- | --- |
| 组件 | `external/PresentMon/PresentMon-x64.exe`（打包后解包到 `_MEIPASS/PresentMon/`） |
| 版本 | 2.6.0（x64 官方发行版，Intel Corporation） |
| 官方发布页 | https://github.com/GameTechDev/PresentMon/releases/tag/v2.6.0 |
| 官方资产 | https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe |
| 文件大小 | 980,320 字节 |
| SHA-256 | `b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af` |
| 许可证 | MIT License |
| 用途 | StutterTriage 只**启动**它并把 CSV 输出解析成帧记录，不修改其代码 |

仓库内副本与官方资产逐字节一致（2026-11-11 用 `Get-FileHash -Algorithm SHA256`
对本地文件与 GitHub Release API 的 asset digest 双向核对）。

MIT 许可证原文：

```
MIT License

Copyright (c) Intel Corporation

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Qt / PyQt6 运行时（EXE 内实际打入的组件）

正式 EXE 由 PyInstaller 打包，随包带入 **PyQt6 6.12.0 / Qt 6.12.0** 的运行库。
下面这份清单来自 2026-11-11 的 `-KeepWork` 构建（PyInstaller `Analysis-00.toc` 实测），
不是按经验猜的：

| 类别 | 实际打入的文件 |
| --- | --- |
| Qt 模块 | `Qt6Core`、`Qt6Gui`、`Qt6Widgets`、`Qt6Network`、`Qt6OpenGL`、`Qt6OpenGLWidgets`、`Qt6Svg`、`Qt6Test` |
| 平台插件 | `platforms/qwindows.dll`、`platforms/qoffscreen.dll`、`platforms/qminimal.dll` |
| 样式插件 | `styles/qmodernwindowsstyle.dll` |
| 图像格式插件 | `imageformats/qgif|qicns|qico|qjpeg|qsvg|qtga|qtiff|qwbmp|qwebp.dll` |
| 图标引擎 | `iconengines/qsvgicon.dll` |
| 输入插件 | `generic/qtuiotouchplugin.dll` |
| 图形回退 | `opengl32sw.dll`（Qt 自带的软件 OpenGL 回退） |
| MSVC 运行库 | `MSVCP140.dll`、`MSVCP140_1.dll`、`MSVCP140_2.dll`、`VCRUNTIME140.dll`、`VCRUNTIME140_1.dll` |

- **PyQt6 6.12.0** —— Riverbank Computing，**GPL-3.0 / 商业双许可**。
  许可说明：https://www.riverbankcomputing.com/software/pyqt/intro#license
- **Qt 6.12.0** —— The Qt Company，**LGPL-3.0 / GPL-2.0+ / 商业许可**三选一。
  许可说明：https://doc.qt.io/qt-6/licensing.html
- **Qt for Python（PySide6）** 为 LGPL-3.0，是闭源场景的替代方案（本项目未使用）：
  https://doc.qt.io/qtforpython-6/licenses.html

> ⚠️ **许可边界**：本项目源码以 **MIT** 发布，但随包分发的 PyQt6/Qt 运行库仍受上述许可约束。
> 若要把 StutterTriage 用于**闭源商业软件**，需要：改用 PySide6（LGPL-3.0），
> 或向 Riverbank / Qt Company 购买商业许可。分发 EXE 前请自行核对这三个层面：
> 源码许可（MIT）、GUI 框架许可（PyQt6）、Qt 运行库许可（Qt 6）。

---

## Python 依赖

精确版本见仓库根目录 `requirements-lock.txt`（CI 与发布构建都用它）。

| 组件 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| [PyQt6](https://pypi.org/project/PyQt6/) | 6.12.0 | GPL-3.0 / 商业双许可 | GUI 框架 |
| [PyQt6-Qt6](https://pypi.org/project/PyQt6-Qt6/) | 6.12.0 | LGPL-3.0 / GPL / 商业 | Qt 6 运行库轮子 |
| [PyQt6-sip](https://pypi.org/project/PyQt6-sip/) | 13.13.0 | BSD-2-Clause | PyQt 的 C++ 绑定运行时 |
| [pyqtgraph](https://pypi.org/project/pyqtgraph/) | 0.14.0 | MIT | 图表渲染 |
| [numpy](https://pypi.org/project/numpy/) | 2.4.6 | BSD-3-Clause | 数值计算 |

### 构建期依赖（不随包分发）

| 组件 | 版本 | 许可证 |
| --- | --- | --- |
| [PyInstaller](https://pypi.org/project/pyinstaller/) | 6.22.3 | GPL-2.0+（附例外条款，允许打包任意许可的软件） |
| [pyinstaller-hooks-contrib](https://pypi.org/project/pyinstaller-hooks-contrib/) | 2026.9 | Apache-2.0 |

---

## 运行时调用的系统组件（不随包分发）

- **Windows ETW / PresentMon 驱动栈** —— Microsoft Windows 系统组件。
- **AMD ADL**（`atiadlxx.dll`）、**NVIDIA NVML**（`nvml.dll`）—— 显卡厂商驱动，
  StutterTriage 仅在运行时动态加载，不分发这些文件。
- **Windows PDH 性能计数器** —— 系统组件，用于 pagefile / 硬缺页 / 磁盘延迟 / 队列 / DWM 读数。
