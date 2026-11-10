# StutterTriage v1.1.0 升级说明 —— 内存-磁盘交换证据链 & perfmon 时间对齐

> 这份文档逐条回答 `ST的改进.txt` 提出的问题：**旧版 CSV 只能看到帧与 GPU/CPU 忙碌时间，
> 无法直接定位「是不是内存不够导致交换磁盘引发卡顿」**。v1.1.0 不是加一句免责声明，
> 而是把「证明/证伪」这条链路真正做出来。

---

## 1. 改进书原文的三个核心结论

| # | 改进书的判断 | v1.1.0 的回应 |
|---|---|---|
| 1 | `PageIn_MBps` **不是**页面文件交换，它统计的是游戏资源文件读入 | 新增系统级 `Pages Input/sec`、`Pages Output/sec`、`Paging File(_Total)\% Usage`，与旧的进程级 `PageIn_MBps` **分列并存**，口径在界面与 CSV 里都写清，不再混用 |
| 2 | 旧日志没有硬缺页、页面写出、磁盘延迟、磁盘队列、可用内存曲线 | 采集层新增 12 个 PDH 计数器（见 §2），全部进快照、进 CSV、进诊断 |
| 3 | 「>100ms 尖峰」无法区分是内存交换，还是 DWM/壁纸/CPU 线程抢占 | 新增第 12 维归因「内存-磁盘交换（pagefile 证据链）」+ 会话级重合率；交换链路全平稳时**明确写出「已排除内存交换」**，并把矛头指向磁盘排队、DWM 或 CPU |
| 4 | 建议 perfmon 采样间隔 **100–200ms**（尽量贴近帧采样） | **实测做不到**：`logman`/`typeperf` 的 `-si` 只接受整秒（传 `00:00:00.200` 直接报 `Invalid syntax` / `参数错误`），perfmon 数据收集器集同样以秒为最小单位。但本软件内置的 PDH 硬件心跳本来就是 **0.4s** 一轮（比 perfmon 细 2.5 倍），所以「逐尖峰排查」用软件内置导出更准；perfmon 模板定位为「外部独立复核 / 超长录制」，间隔固定 1 秒，对齐容差相应放到 ±1.2s |

改进书给的三个方案，v1.1.0 的取舍：

- **方案 A（Perfmon，推荐）→ 做成内置能力**：一键导出 perfmon 采集模板（计数器清单 + 一键 bat + 数据收集器集 XML），
  并把「两份 CSV 用时间戳对齐」做成软件内按钮与命令行，用户不必自己写脚本（见 §5）。
- **方案 B（Microsoft PIX）→ 明确不集成**：PIX 需要开发者模式与逐帧事件捕获，日志极大、上手门槛高，
  与「给玩家用的一句话结论」定位冲突。文档里保留指引，需要事件级精度时自取。
- **方案 C（快速排查思路）→ 自动化**：改进书里「看 `PageIn_MBps` 绝大多数时刻=0 → 说明不是资产流式加载」
  「看可见内存是否被压低、页面文件是否上涨、磁盘是否 100%」这些人工判断，
  现在由内存页 + 第 12 维诊断卡 + 事件流自动给出，并给出与尖峰的重合率。

---

## 2. 采集层：新增的 12 个 PDH 计数器

全部使用**英文计数器名**（`PdhAddEnglishCounterW`），中英文 Windows 通用；采样频率与硬件心跳一致（0.4s 一轮）。

| 计数器 | 单位（软件内） | 用途 |
|---|---|---|
| `\Paging File(_Total)\% Usage` | % | 页面文件占用率 —— **只有它升高才说明真的在用虚拟内存** |
| `\Memory\Pages Input/sec` | MB/s（×4096/1MiB） | 硬缺页换入（从磁盘读回内存） |
| `\Memory\Pages Output/sec` | MB/s | 页面写出（内存写回磁盘） |
| `\Memory\Pages/sec` | 页/s | 内存↔磁盘总流量（含软故障，作总览） |
| `\Memory\Page Faults/sec` | 次/s | 缺页总次数（判定「谁在大量申请内存」） |
| `\Memory\% Committed Bytes In Use` | % | 已提交 / 提交上限 |
| `\PhysicalDisk(_Total)\Avg. Disk sec/Read` | ms | 物理磁盘平均读延迟（>25ms 就会造成可感知卡顿） |
| `\PhysicalDisk(_Total)\Avg. Disk sec/Write` | ms | 物理磁盘平均写延迟 |
| `\PhysicalDisk(_Total)\Current Disk Queue Length` | 个 | 磁盘队列长度（≥2 说明 IO 排队） |
| `\PhysicalDisk(_Total)\Disk Read Bytes/sec` | MB/s | 磁盘读取吞吐 |
| `\PhysicalDisk(_Total)\Disk Write Bytes/sec` | MB/s | 磁盘写入吞吐 |
| `\Process(dwm)\% Processor Time` | % | 桌面合成器 CPU 占用（窗口化/无边框模式的抢夺者） |
| `\Process(*)\Working Set` | MB | 进程工作集 → 目标游戏与 `dwm.exe` 的真实内存足迹 |
| `\Process(*)\Page Faults/sec` | MB/s | 进程级缺页速率 |

配套字段（`HardwareSnapshot`）：`pf_usage_pct / pf_used_mb / pf_total_mb / committed_pct / pages_per_s /
page_faults_s / disk_read_ms / disk_write_ms / disk_queue / disk_read_mbps / disk_write_mbps /
dwm_cpu / dwm_ws_mb / game_proc / game_ws_mb / game_pf_s`。

界面事件流新增三类告警（都带冷却，避免刷屏）：

- **内存交换**：pagefile ≥80% 且确有换入/换出 → 磁盘延迟/队列同时升高升级为 DANGER
- **磁盘延迟**：读或写延迟 ≥50ms → DANGER（说明磁盘排队已经把渲染线程阻塞）
- **桌面合成**：`dwm.exe` CPU ≥12% 且 3D 负载 ≥60% → WARN

---

## 3. 诊断层：第 12 维「内存-磁盘交换（pagefile 证据链）」

`SnapshotDiffInspector.inspect()` 从 11 行变成 12 行。第 12 行的取值不是单个数字，
而是**三条链路的互证结论**（`stuttertriage/swapdiag.py`）：

```
交换链路：pagefile% ≥60%  或  Pages Input ≥8 MB/s  或  Pages Output ≥20 MB/s
磁盘链路：磁盘读/写延迟 ≥25ms  或  队列 ≥2
合成链路：dwm.exe CPU ≥12%
CPU 链路：CPU0 ≥92%  或  整机 ≥90%
```

判定顺序与结论：

1. 交换链路 **且** 磁盘链路 → `DANGER` 「内存-磁盘交换型卡顿」（pagefile、换入/换出、延迟、队列四个数字一起给）
2. 只有交换链路 → `WARN`「已在用虚拟内存，磁盘尚未排队」
3. 只有磁盘链路 → 「磁盘 IO 拥堵，**不是**内存交换」
4. 只有合成链路 → 「DWM 合成 / 壁纸调度抢占，**不是**内存交换」
5. 只有 CPU 链路 → 「CPU 调度抢占，**不是**内存交换」
6. 全部平稳 → 「🟢 **已排除内存交换**」（附四个数字，供用户自己核对）
7. 计数器缺失 → 「⚪ 不可判定」（**绝不**把「没有数据」说成「平稳」）

配套：会话级重合率（`FrameStore.swap_stats()` → `swapdiag.session_stats()`），
例如「本次会话 37 次尖峰里 29 次与内存交换同向（78%·置信度高）」；
`banner_verdict` 的病因优先级也把「内存-磁盘交换」排在 `磁盘 I/O` 之前，
因为它有三项实测值互证。

所有阈值都进 `stuttertriage_config.json`（设置页新增「内存交换归因」分组，9 个中文标签），
且全部登记为 `consts.LIVE_CONST_NAMES`，改完立即热更新，不需要重启。

---

## 4. 导出层：CSV 从 13 列扩到 32 列（前 13 列一字不改）

**关键新增：`WallClock`** —— 每帧的绝对时间戳（ISO 8601，毫秒精度）。
`TimeInSeconds` 只是 PresentMon 的相对秒，跨两份 CSV 没有绝对时间就无法对齐；
这块砖不补，「帧日志 × perfmon」永远对不上。

其余新增列：`HwSeq`、`MemAvail_MB`、`CPUTotal_Pct`、`PageOut_MBps`、`PagesPerSec`、
`PageFaults_per_s`、`PageFile_Used_MB`、`PageFile_Pct`、`Committed_Pct`、
`DiskReadLatency_ms`、`DiskWriteLatency_ms`、`DiskQueue`、`DiskRead_MBps`、`DiskWrite_MBps`、
`DwmCPU_Pct`、`GameWS_MB`、`GamePF_MBps`、`SwapVerdict`（仅尖峰帧计算：swap/disk/dwm/cpu/none/unknown）。

列定义集中在 `StutterTriageWindowBase.RECORD_COLS`，写表函数拆成 `_write_record_csv(path)`，
因此「导出」与「perfmon 对齐」共用同一条写表路径（不会出现两份表头各自漂移）。

---

## 5. perfmon 同步录制与时间对齐（改进书方案 A 的落地）

### 5.1 软件内

设置页 →「内存交换证据链（页面文件 / 硬缺页 / 磁盘延迟）」：

- **导出 perfmon 采集模板** → 生成目录 `StutterTriage_perfmon/`：
  - `perfmon_counters.txt`：16 个英文计数器（`typeperf -cf` / perfmon 直接可用）
  - `start_perfmon.bat` / `stop_perfmon.bat`：`logman` 一键起停，**1 秒**采样（`logman -si` 只支持整秒，见 §1 第 4 条），
    CSV 落在脚本同目录，文件名带时间戳
  - `perfmon_template.xml`：数据收集器集模板（用 Windows 自带 `logman export` 生成，保证 schema 合法；
    非管理员时会明确提示改用 bat，**不假装成功**）
  - `README_perfmon.txt`：step-by-step 说明
- **对齐 perfmon CSV…** → 选 perfmon 的 CSV，软件用当前会话（或你指定的 1.1.0 帧日志）
  按 `WallClock` 与 perfmon 时间戳对齐（默认容差 **±1.2s**，因为 perfmon 最小采样是 1 秒），逐尖峰输出判定，
  并在 perfmon CSV 旁边写出 `*_aligned.csv`。

### 5.2 命令行（改进书里说的「Python 脚本对齐」）

```powershell
# 导出采集模板
python -m stuttertriage.perfmon template -o .\perfmon_template

# 对齐帧日志与 perfmon CSV（尖峰门槛 33ms，容差默认 1.2s）
python -m stuttertriage.perfmon align --record stuttertriage_record.csv `
    --perfmon perfmon_20261110_220000.csv -o merged.csv --spike-ms 33
```

合并 CSV 的列：`Frame, WallClock, MsBetweenPresents, PageFile_Pct, PagesIn_MBps, PagesOut_MBps,
DiskRead_ms, DiskWrite_ms, DiskQueue, DwmCPU_Pct, Kind, Verdict, Advice`。

### 5.3 日期顺序的坑（已处理）

perfmon 的 CSV 时间列**跟随系统区域设置**：中文 Windows 是 `2026/11/10 22:00:00.100`（年在最前，无歧义），
英文/欧洲系统是 `11/10/2026 22:00:00.100`（月在前还是日在前取决于区域）。
判错会把整段采样平移到另一天 → 一个尖峰都匹配不上，而用户只会看到「没对齐」。
因此 `load_perfmon_csv()` 会把两种解释各算一遍，取「更单调递增且总跨度更小」的那种
（一次录制只有几分钟，跨月解释会出现 20~30 天的跳跃），并可用 `--dayfirst` 强制指定。

---

## 6. 实操流程（对照改进书「实操建议流程」）

| 改进书步骤 | v1.1.0 操作 |
|---|---|
| ① PresentMon + perfmon 同时录制 | 点「导出 perfmon 采集模板」→ 运行 `start_perfmon.bat` → 回到软件点「▶ 开始采集」 |
| ② 导出两份 csv，以时间戳做对齐 | 软件「导出当前帧与诊断数据」+ perfmon 的 CSV → 设置页「对齐 perfmon CSV…」（或 CLI） |
| ③ 筛选 >33ms / >100ms 的尖峰 | 自动完成：`--spike-ms` 默认 33ms；合并 CSV 里 `Kind` 直接给出归类 |
| ④ 看对应时间点的内存/磁盘计数器 | 自动完成：合并 CSV 每一行都带同一时刻的 pagefile%、换入/换出、磁盘延迟、队列、DWM% |
| ✅ 内存飙升 / ❌ 内存磁盘平稳 | 自动给出结论：「内存-磁盘交换型卡顿」或「已排除内存交换（更可疑：磁盘排队 / DWM / CPU 抢占）」 |

---

## 7. 验证证据

| 验证项 | 结果 |
|---|---|
| 新 PDH 计数器在真机可打开并取到值 | `pf_usage_pct 13.3% / pf_total 16946 MB / committed 99.1% / disk_write 0.27 ms / dwm_cpu 26.7% / dwm_ws 485.6 MB`（实测） |
| `tests/test_swap_evidence.py`（新增 60 项断言） | 全部通过：判定 5 分支 / 会话级重合率 / perfmon CSV 两种日期顺序 / 页→MB、秒→ms 换算 / 对齐产出 / 旧日志拒绝 / 阈值热更新 / 导出列契约 / 本机 PDH 4/4 |
| `tests/test_v1_layout.py`（12 行 + 版面不变量） | 通过 |
| `tests/smoke_v4_runtime.py`（端到端运行） | 通过（专业表格 12 行 5 列） |
| `tests/smoke_ux_runtime.py`（改用真实写表路径 + 新列断言） | 通过 |
| `tests/test_ux.py`（59 项 UX 契约） | 通过 |
| 其余 tests（静态不变量 / 回归 / 主题 / 生命周期 / 关闭清理 / 算法修复） | 全部通过 |
| 端到端导出 | 6 帧会话导出 32 列；130ms 尖峰帧 `SwapVerdict=swap`、平稳帧留空 |

---

## 8. 后续可以继续做的（明确未做）

- **PIX / ETW 事件级缺页捕获**：能做到逐帧证明，但需要开发者模式 + 巨大日志，
  与「玩家一句话结论」的定位冲突，故不做；文档保留指引。
- **`wallpaper64.exe` 等具体桌面进程的逐帧抢占量化**：目前用 `dwm.exe` CPU 作为合成链路的代理指标，
  要精确到「哪个壁纸进程抢了哪一帧」需要 ETW 线程级采样。
- **长时回放**：合并 CSV 目前已可喂给 Excel/Python 画对齐时间轴；软件内的双 Y 轴对齐曲线尚未做。
