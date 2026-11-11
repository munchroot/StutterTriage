# 开发笔记

这里放不适合首页的详细记录：踩坑、实测结论、口径说明。想留的个人吐槽也可以写在这里。

## 更多已知限制

- **1% Low 口径**：用帧耗时 P99 换算。样本不足 1000 帧时，0.1% Low 会退化为 P99（界面会标注）。
- **最高帧口径**：取窗口内最高瞬时帧率，切屏或加载瞬间的极端值会拉高它，属预期。
- **折线图坐标轴**：默认每帧重新锁定范围（为了避免乱码和过窄区间）。手动滚轮或拖拽缩放后会停止自动锁轴，直到切换 FPS/ms 视角或点「🧹 清空重采」才复位。
- **显卡频率门控**：依赖驱动上报（AMD ADL / NVIDIA NVML）。驱动未上报时该行标注「不可判定」，不会给出假结论。
- **交换证据链口径**：pagefile 占用率、Pages Input/Output、磁盘延迟与队列全部来自 PDH 系统级计数器（0.4 秒一轮）。采样粒度粗于几十毫秒的尖峰，所以给出的是「会话级重合率 + 单帧 ±0.6s 窗口证据」，并标注置信度与「不可判定」。需要事件级精度请用 Microsoft PIX，需要逐帧对齐请用 perfmon 对齐功能。
- **模拟数据**：会在图表上打水印并在侧栏标注数据源，避免被误当真实采集。
- **单文件 EXE 被强杀**：会残留 `%TEMP%\_MEI*`（约 120 MB/次），这是 PyInstaller 单文件模式的固有行为。正常关闭不残留，强杀后可手动删除。
- **ETW 会话残留**：PresentMon 与 ETW 会话已用 Job Object 兜底，StutterTriage 退出后它们会跟着结束。但 `logman stop` 需要管理员权限，非管理员强杀后 ETW 会话可能残留 Running，下次启动会自动清理（`PresentMonBridge` 启动前调用 `cleanup_stale()`）。
- **PyQt6 许可**：GPL-3.0 / 商业双许可。项目以 MIT 发布，若用于闭源商业软件，请改用 PySide6（LGPL-3.0）或购买 PyQt 商业许可。

## 踩坑记录

- **拆分后配置失效**：包拆分后 `__file__` 变成包目录，配置被写进 `stuttertriage/`，导致用户旧配置失效。现在统一由 `stuttertriage/paths.py` 提供路径。
- **配置保存需重启才生效**：`apply_to_globals()` 传了 `globals()`，只更新了本模块副本。改为不带参数、广播到所有已注册模块。
- **PresentMon error 1450 静默 0 帧**：由 `etw.py` 的 ETW 会话管家处理。
- **AMD ADL 随机闪退**：把 Python 缓冲区地址交给驱动导致 use-after-free，改为由驱动 `free()` 接管。
- **退出后 Qt 不退出 / 解释器收尾 fastfail**：顶层窗口没关全，或重复调用 `deleteLater()`。
- **「进程读文件速率」冒充内存交换**：旧口径会误判，v1.1.0 起改用 pagefile、硬缺页、磁盘、DWM 四项互证。

## 测试环境说明

- 测试统一入口是 `scripts/test.ps1`，套件划分见 [DEVELOPMENT.md](DEVELOPMENT.md#3-测试)。
- `test_lifecycle.py` 里「无残留 ETW 会话」一项需要**管理员权限**（`logman stop` 受限）。
  非管理员运行时该项打印 `[SKIP]` 并保留其余断言；集成结果单独报告，不算离线回归失败。
- `tests/` 里的脚本会自己设 `QT_QPA_PLATFORM=offscreen`，不弹窗、可在无桌面环境跑。
- 计数类断言（交换证据、CSV 列）依赖真实 PDH 计数器；计数器服务被禁用时打印 `[SKIP]`，
  不会伪造零值。

## 文档口径约定

- **版本号只有一处真源**：`stuttertriage/__init__.py` 的 `__version__`。
  标题栏、README 徽章、`build_tools/version_info.txt`、`CHANGELOG.md` 都由发布脚本
  `build_tools/check_version.py` 校验一致性（`build_exe.ps1 -Release` 会自动调用）。
- **构建耗时/体积**只作为历史测量记录（已标注 v1.0.0 实测），每次发布写当次实测值。
- **测试数量**不写死在文档里，统一说「见 `scripts/test.ps1 -List`」，避免再次出现
  「首页 18 个、贡献段 14 个」这类漂移。

## 结构待办（不影响功能）

- `consts.py` 目前为了主题热切换在导入期 `from .theme import bind_globals`，而 `theme.py`
  依赖 PyQt6/pyqtgraph —— 结果算法测试也要先装 Qt 才能导入。后续可把配色表拆成无 Qt 的
  `theme_tokens.py`（纯字符串与纯 Python 混色），`consts.py` 只依赖它，让 `stats` / `swapdiag`
  这类纯函数模块在无 Qt 环境也能单测。
- 交换证据链的「缺计数器」与「真实 0」目前靠 `_available` 推断，后续应改为逐字段可用性
  标记（PDH 注册成功与否），PerfMon 转换也走同一套标记。

## 实测记录（打包 v1.0.0 时）

- 从项目目录启动、复制到其他目录启动，均正常。
- 窗口标题、界面、实时数据、图表均正常渲染。
- 配置沿用用户设置，没有被改写。
- 关闭后无残留 StutterTriage / PresentMon 进程。
- 普通双击启动时，程序会在 700 ms 后弹一次「以管理员身份运行可获得完整采集能力」的提醒。右键 exe → 以管理员身份运行就不会有这个提醒。
