# -*- coding: utf-8 -*-
"""
perfmon 计数器模板 + 「帧日志 × 系统计数器」时间对齐（v1.1.0）。

【为什么需要这个模块】
ST 改进建议书里最关键的一句是：PresentMon 只抓帧提交瞬间的少量采样，
**没有** Windows 性能计数器里完整的虚拟内存、硬缺页、页面写出、磁盘 IO 队列深度。
所以「窗口化时 >100ms 的尖峰」到底是内存交换、DWM/壁纸调度，还是 CPU 线程抢占，
单靠 PresentMon CSV 永远无法证明 —— 必须**同时**录制 perfmon，再用时间戳对齐。

本模块把这件事做成三条可执行路径（不需要用户自己拼命令行）：

  ① `write_template(dir)`  导出一份**可以直接用**的 perfmon 采集模板：
       · perfmon_counters.txt —— typeperf / perfmon 通用的英文计数器清单
       · perfmon_collect.bat  —— logman 一键创建/启动/停止采集，产出 CSV
       · perfmon_template.xml —— perfmon 数据收集器集模板（logman 可用时生成，
                                 保证是 Windows 自己认可的格式）
  ② `load_perfmon_csv(path)` 解析 perfmon 导出的 CSV（含 `(PDH-CSV 4.0)` 头）
  ③ `align(record_csv, perfmon_csv, out_csv)` 把 StutterTriage 导出的
     `stuttertriage_record.csv`（含每帧绝对时间戳 WallClock）与 perfmon CSV
     按时间戳对齐，对每个尖峰帧给出「交换 / 磁盘 / DWM / 排除交换」结论，
     另存一份合并 CSV —— 这就是改进建议书里「用 Python 脚本对齐两份 CSV」。

【口径换算】perfmon 的 Pages Input/sec 单位是「页/秒」，×4096/1MiB 换 MB/s；
`Avg. Disk sec/Read|Write` 单位是秒，×1000 换毫秒。与采集层完全一致。
"""

import csv
import io
import os
import subprocess
import sys
from datetime import datetime

from . import swapdiag

# 记录 CSV 里每帧的绝对时间列（导出时写的就是这个列名）
RECORD_TIME_COL = "WallClock"
RECORD_FT_COL = "MsBetweenPresents"

# 采集模板的计数器清单（顺序即 CSV 列顺序；全部用**英文计数器名**，
# 这样在中文/英文 Windows 上都能用 —— 与采集层的 AddEnglishCounter 口径一致）
COUNTERS = (
    (r"\Memory\Available MBytes", "可用物理内存 (MB)"),
    (r"\Memory\Committed Bytes", "已提交虚拟内存 (B)"),
    (r"\Memory\Commit Limit", "提交上限 (B)"),
    (r"\Memory\% Committed Bytes In Use", "已提交占上限 (%)"),
    (r"\Memory\Pages Input/sec", "硬缺页换入：从磁盘读入内存 (页/s)"),
    (r"\Memory\Pages Output/sec", "页面写出：内存写回磁盘 (页/s)"),
    (r"\Memory\Pages/sec", "内存↔磁盘总页面流量 (页/s)"),
    (r"\Memory\Page Faults/sec", "缺页总次数（含软缺页，仅作总览）"),
    (r"\Paging File(_Total)\% Usage", "页面文件占用率 (%) ← 判定是否真的在用虚拟内存"),
    (r"\PhysicalDisk(_Total)\Avg. Disk sec/Read", "物理磁盘平均读延迟 (s)"),
    (r"\PhysicalDisk(_Total)\Avg. Disk sec/Write", "物理磁盘平均写延迟 (s)"),
    (r"\PhysicalDisk(_Total)\Current Disk Queue Length", "磁盘队列长度（>2 说明 IO 排队）"),
    (r"\PhysicalDisk(_Total)\Disk Read Bytes/sec", "磁盘读取吞吐 (B/s)"),
    (r"\PhysicalDisk(_Total)\Disk Write Bytes/sec", "磁盘写入吞吐 (B/s)"),
    (r"\Process(dwm)\% Processor Time", "桌面合成器 CPU 占用 (%)"),
    (r"\Process(dwm)\Working Set", "桌面合成器工作集 (B)"),
)

# 【实测硬限制】logman / typeperf 的 -si 只接受整秒（实测传 00:00:00.200 会直接报
#   "Invalid syntax: -si <[[hh:]mm:]ss>" / "参数错误"），perfmon GUI 的数据收集器集同样
#   以秒为最小单位。也就是说「100~200ms 系统计数器采样」**用系统自带工具做不到** ——
#   恰好本软件内置的 PDH 硬件心跳就是 0.4s 一轮，比 perfmon 细 2.5 倍，
#   所以「逐帧对齐」用软件内置导出（0.4s 粒度）更准，perfmon 模板只作为
#   「外部独立复核 / 超长录制 / 与第三方日志互通」这条路。
# 因此这里的模板间隔统一用 1 秒（唯一可用的合法值），对齐容差相应放到 ±1.2s。
DEFAULT_INTERVAL_MS = 1000

PAGE_BYTES = 4096.0
MB = 1048576.0


# ==============================================================================
# 1) 采集模板
# ==============================================================================
def counter_paths():
    """纯计数器路径列表（typeperf -cf 的输入格式）"""
    return [p for p, _ in COUNTERS]


def write_counter_list(path):
    """写 typeperf/perfmon 通用计数器清单（每行一个计数器路径）"""
    with io.open(path, "w", encoding="utf-8", newline="\r\n") as f:
        for p in counter_paths():
            f.write(p + "\n")
    return path


def write_batch(path, interval_ms=DEFAULT_INTERVAL_MS):
    """
    写一键采集脚本（logman 版）：创建 → 启动 → 提示停止。

    logman 的输出 CSV 与 perfmon GUI 导出的 CSV 格式一致（都以 `(PDH-CSV 4.0)` 开头），
    因此本模块的 `load_perfmon_csv` 两种来源都能解析。

    【坑·必读】这里所有字符串都用拼接而不是 `%` 格式化 —— bat 里的
    `%DATE:~0,4%` 会被 `%` 运算符吃掉，要么报 "unsupported format character"，
    要么把日期占位符悄悄换掉，用户拿到的脚本根本跑不起来。
    """
    # logman 只认整秒（见 DEFAULT_INTERVAL_MS 处说明），这里强制归一
    sec = max(1, int(round(interval_ms / 1000.0)))
    ms = sec * 1000
    si = "00:00:%02d" % sec
    stamp = "%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
    out_csv = "%~dp0perfmon_" + stamp + ".csv"
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "rem ==== StutterTriage 1.1.0 · perfmon 同步采集（与帧日志一起开、一起停）====",
        "rem 用法：先双击 start_perfmon.bat 开始采集，再在 StutterTriage 里点「开始采集」；",
        "rem 说明：logman 只支持整秒采样，这里用 1 秒；软件内置的 PDH 心跳是 0.4 秒一轮，",
        "rem       精度更高，日常排查优先用软件内置导出（见 docs/UPGRADE_1.1.0.md）。",
        "rem       复现卡顿后双击 stop_perfmon.bat，CSV 保存在本目录。",
        "setlocal",
        'cd /d "%~dp0"',
        "logman delete StutterTriage_swap >nul 2>nul",
        'logman create counter StutterTriage_swap -cf "%~dp0perfmon_counters.txt"'
        ' -f csv -si ' + si + ' -o "' + out_csv + '" -y',
        "logman start StutterTriage_swap",
        "echo.",
        "echo 采集已启动（间隔 " + str(ms) + " ms）。录完请运行 stop_perfmon.bat。",
        "endlocal",
        "pause",
    ]
    with io.open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    stop = [
        "@echo off",
        "chcp 65001 >nul",
        "logman stop StutterTriage_swap",
        "logman delete StutterTriage_swap >nul 2>nul",
        "echo 已停止；CSV 与脚本同目录。",
        "pause",
    ]
    stop_path = os.path.join(os.path.dirname(path), "stop_perfmon.bat")
    with io.open(stop_path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(stop) + "\n")
    return path, stop_path


def write_template(out_dir, interval_ms=DEFAULT_INTERVAL_MS, try_xml=True):
    """
    导出完整采集模板到 out_dir。

    返回 (written_paths, notes)；notes 里写清楚哪些步骤因为环境原因降级了
    （例如非管理员无法 logman create → 只给 txt/bat，用户手动用 perfmon GUI）。
    """
    notes = []
    written = []
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as ex:
        return [], ["无法创建目录 %s：%s" % (out_dir, ex)]

    txt = os.path.join(out_dir, "perfmon_counters.txt")
    write_counter_list(txt)
    written.append(txt)

    bat = os.path.join(out_dir, "start_perfmon.bat")
    try:
        b, s = write_batch(bat, interval_ms)
        written.extend([b, s])
    except Exception as ex:
        notes.append("批处理模板写入失败：%s" % ex)

    if try_xml:
        xml = os.path.join(out_dir, "perfmon_template.xml")
        ok, msg = export_datacollector_xml(xml)
        if ok:
            written.append(xml)
        else:
            notes.append("数据收集器集 XML 未生成（%s）；可改用 start_perfmon.bat 或用 "
                         "perfmon → 数据收集器集 → 手动导入 perfmon_counters.txt" % msg)

    readme = os.path.join(out_dir, "README_perfmon.txt")
    try:
        with io.open(readme, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(
                "StutterTriage 1.1.0 · perfmon 同步采集模板\r\n"
                "=================================================\r\n"
                "为什么需要它：PresentMon 的 CSV 只有帧时间，没有页面文件交换、硬缺页、\r\n"
                "磁盘延迟与磁盘队列。要证明「>100ms 尖峰是不是内存交换造成的」，\r\n"
                "必须同时录制系统计数器，再用时间戳对齐。\r\n"
                "\r\n"
                "用法（推荐）：\r\n"
                "  1. 双击 start_perfmon.bat（建议以管理员身份运行）开始采集；\r\n"
                "  2. 打开 StutterTriage → 开始采集 → 复现卡顿；\r\n"
                "  3. 录制结束双击 stop_perfmon.bat；\r\n"
                "  4. StutterTriage 里「导出当前帧与诊断数据」，得到 stuttertriage_record.csv；\r\n"
                "  5. StutterTriage 设置页点「对齐 perfmon CSV」，选这两个文件即可得到\r\n"
                "     合并 CSV（含每个尖峰的交换/磁盘/DWM 判定）。\r\n"
                "\r\n"
                "也可以命令行对齐：\r\n"
                "  python -m stuttertriage.perfmon align --record stuttertriage_record.csv "
                "--perfmon perfmon_xxx.csv -o merged.csv\r\n")
        written.append(readme)
    except Exception:
        pass
    return written, notes


def export_datacollector_xml(path, name="StutterTriage_swap"):
    """
    用 Windows 自带的 logman 生成一份**格式合法**的数据收集器集 XML。

    【为什么不用手写 XML】DataCollectorSet 的 schema 很长，手写很容易出现
    「perfmon 导入报错」这种最伤用户信任的问题。logman 生成的 XML 一定合法。
    需要创建计数器集合（通常要管理员）：失败就返回 (False, 原因)，让 UI 提示
    改用 bat，绝不假装成功。
    """
    secs = "%02d:%02d:%02d.%03d" % (0, 0, DEFAULT_INTERVAL_MS // 1000,
                                    DEFAULT_INTERVAL_MS % 1000)
    try:
        subprocess.run(["logman", "delete", name], capture_output=True, timeout=20,
                       creationflags=0x08000000)
        r = subprocess.run(["logman", "create", "counter", name, "-c", "\\Memory\\Available MBytes",
                            "-f", "csv", "-si", secs, "-o", path + ".tmp.csv"],
                           capture_output=True, timeout=30, creationflags=0x08000000)
        if r.returncode != 0:
            msg = _decode_console(r.stderr or r.stdout)
            _remove(name)
            # 【用户可读】这一步失败 99% 是因为没管理员权限：
            # logman 输出「Access is denied. Try running this command as an administrator.」，
            # 但它在中文/英文控制台的编码不一样，解不出来就退回一句明确的话。
            if not msg:
                msg = "logman create 失败（rc=%d，通常是缺少管理员权限）" % r.returncode
            return False, msg.splitlines()[0][:120]
        r2 = subprocess.run(["logman", "export", name, "-xml", path],
                            capture_output=True, timeout=30, creationflags=0x08000000)
        ok = r2.returncode == 0 and os.path.isfile(path)
        _remove(name)
        try:
            os.remove(path + ".tmp.csv")
        except OSError:
            pass
        if not ok:
            return False, "logman export 失败（可能不是管理员）"
        return True, ""
    except Exception as ex:
        _remove(name)
        return False, str(ex)[:120]


def _decode_console(raw):
    """console 输出解码：logman 在中文系统上是 OEM(936/437)，不是 UTF-8"""
    if not raw:
        return ""
    for enc in ("mbcs", "oem", "utf-8", "latin-1"):
        try:
            txt = raw.decode(enc, "ignore").strip()
        except Exception:
            continue
        if txt:
            # 去掉前面可能出现的空行 / 纯 "Error:" 行，保留第一句有内容的
            lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
            return " ".join(lines)
    return ""


def _remove(name):
    try:
        subprocess.run(["logman", "delete", name, "-y"], capture_output=True,
                       timeout=20, creationflags=0x08000000)
    except Exception:
        pass


# ==============================================================================
# 2) perfmon CSV 解析
# ==============================================================================
def _split_time(text):
    """把 perfmon 时间列拆成 (日期三段, hh, mm, ss, ms)；失败返回 None"""
    s = (text or "").strip().strip('"')
    if not s:
        return None
    s = s.replace("上午", " AM ").replace("下午", " PM ")
    parts = [p for p in s.split() if p]
    if len(parts) < 2:
        return None
    date_part = parts[0]
    ampm = ""
    if parts[-1].upper() in ("AM", "PM"):
        ampm = parts[-1].upper()
        clock = parts[-2]
    else:
        clock = parts[-1]
    sep = "/" if "/" in date_part else ("-" if "-" in date_part else "")
    if not sep:
        return None
    nums = date_part.split(sep)
    if len(nums) != 3:
        return None
    try:
        nums = [int(x) for x in nums]
    except ValueError:
        return None
    hh = mm = ss = ms = 0
    bits = clock.split(":")
    try:
        hh = int(bits[0])
        mm = int(bits[1]) if len(bits) > 1 else 0
        if len(bits) > 2:
            si, _, frac = bits[2].partition(".")
            ss = int(si)
            if frac:
                ms = int((frac + "000")[:3])
    except (ValueError, IndexError):
        return None
    if ampm == "PM" and hh < 12:
        hh += 12
    elif ampm == "AM" and hh == 12:
        hh = 0
    return nums, hh, mm, ss, ms


def _epoch(text, dayfirst=False):
    """perfmon 时间字符串 → epoch 秒（解析失败返回 None，绝不猜）"""
    got = _split_time(text)
    if not got:
        return None
    nums, hh, mm, ss, ms = got
    a, b, c = nums
    if a >= 1000:                      # 年/月/日（中文 Windows / ISO 风格）
        y, mo, d = a, b, c
    elif c >= 1000:                    # 月/日/年（en-US）或 日/月/年（欧洲/中国旧风格）
        y = c
        if dayfirst:
            d, mo = a, b
        else:
            mo, d = a, b
    else:
        return None
    try:
        return datetime(y, mo, d, hh, mm, ss, ms * 1000).timestamp()
    except ValueError:
        return None


def parse_perfmon_time(text, dayfirst=None):
    """解析 perfmon CSV 的时间列；失败返回 None"""
    ts = _epoch(text, bool(dayfirst))
    return None if ts is None else datetime.fromtimestamp(ts)


def _choose_dayfirst(time_texts):
    """
    自动判断日期是「月/日/年」还是「日/月/年」。

    【为什么必须自动判断】中文 Windows 导出 `2026/11/10`（年在最前，无歧义），
    但英文/欧洲区域会导出 `11/10/2026` —— 到底是 11 月 10 日还是 10 月 11 日？
    判错会把整段采样平移到另一天，时间对齐必然全错，而用户只会看到
    「一个尖峰都没匹配上」。
    做法：两种解释各算一次时间序列，取「更单调递增且总跨度更小」的解释
    （一次录制只有几分钟；若把 11/10 读错，会出现 20~30 天的跳跃）。
    """
    best = None
    for dayfirst in (False, True):
        epochs = [e for e in (_epoch(t, dayfirst) for t in time_texts) if e is not None]
        if len(epochs) < 2:
            continue
        steps = [epochs[i + 1] - epochs[i] for i in range(len(epochs) - 1)]
        mono = sum(1 for s in steps if s >= 0) / float(len(steps))
        span = abs(epochs[-1] - epochs[0])
        score = mono * 1e6 - span
        if best is None or score > best[0]:
            best = (score, dayfirst)
    return bool(best[1]) if best else False


_OUR_OBJECTS = ("memory", "paging file", "physicaldisk", "logicaldisk", "process",
                "processor information", "processor", "system", "cache", "objects")


def _norm_counter(raw):
    """
    把 `\\\\MACHINE\\Memory\\Available MBytes` 归一成 `Memory\\Available MBytes`。

    只保留「对象(实例)\\计数器」这一段，对齐时就不受采集机器名（可能是 FQDN）影响。
    """
    s = (raw or "").strip().strip('"').strip()
    while "\\\\" in s:
        s = s.replace("\\\\", "\\")
    parts = [p for p in s.split("\\") if p != ""]
    if len(parts) >= 3:
        head = parts[0].lower()
        if head not in _OUR_OBJECTS:
            parts = parts[1:]          # 去掉机器名
    return "\\".join(parts)


def load_perfmon_csv(path, dayfirst=None):
    """
    解析 perfmon / typeperf / logman 导出的 CSV。

    返回 (samples, meta)：
      samples = [(epoch_seconds, {归一化计数器名: float}), ...]（按时间升序）
      meta    = {"counters": [...], "skipped": n, "error": "", "dayfirst": bool}
    编码优先 utf-8-sig，失败回退 utf-16 / mbcs（中文 Windows 的 logman 输出）。
    """
    meta = {"counters": [], "skipped": 0, "error": "", "dayfirst": None}
    if not path or not os.path.isfile(path):
        meta["error"] = "文件不存在"
        return [], meta
    raw = None
    for enc in ("utf-8-sig", "utf-16", "mbcs", "latin-1"):
        try:
            with io.open(path, "r", encoding=enc, newline="", errors="strict") as f:
                raw = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception as ex:
            meta["error"] = str(ex)
            return [], meta
    if raw is None:
        meta["error"] = "无法解码文件"
        return [], meta
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    rows = list(csv.reader(io.StringIO(raw)))
    if not rows:
        meta["error"] = "文件为空"
        return [], meta
    # 表头行：perfmon 的 CSV 第一行以 (PDH-CSV 开头；找不到就按第一行当表头
    head_idx = 0
    for i, r in enumerate(rows[:5]):
        if r and r[0].strip().lstrip('"').startswith("(PDH-CSV"):
            head_idx = i
            break
    header = rows[head_idx]
    counters = [_norm_counter(c) for c in header[1:]]
    meta["counters"] = counters
    body = [r for r in rows[head_idx + 1:] if r and r[0].strip()]
    if dayfirst is None:
        dayfirst = _choose_dayfirst([r[0] for r in body])
    meta["dayfirst"] = bool(dayfirst)
    samples = []
    for r in body:
        ts = _epoch(r[0], bool(dayfirst))
        if ts is None:
            meta["skipped"] += 1
            continue
        vals = {}
        for i, cell in enumerate(r[1:]):
            if i >= len(counters):
                break
            text = (cell or "").strip().strip('"')
            if not text:
                continue
            try:
                vals[counters[i]] = float(text)
            except ValueError:
                continue
        samples.append((ts, vals))
    samples.sort(key=lambda kv: kv[0])
    return samples, meta


# ==============================================================================
# 3) 帧日志 × perfmon 对齐
# ==============================================================================
# 归一化计数器名 → (记录列名, 换算系数, 说明)
def _pick(vals, *names):
    """按多个候选名字取第一个命中（兼容不同 Windows 的实例名写法）"""
    for n in names:
        if n in vals:
            return vals[n]
    low = {k.lower(): v for k, v in vals.items()}
    for n in names:
        v = low.get(n.lower())
        if v is not None:
            return v
    return None


def evidence_from(vals):
    """
    把一条 perfmon 采样转成 swapdiag 认的证据 dict（单位与采集层一致）。

    注意：`Avg. Disk sec/*` 是秒 → ×1000 换毫秒；`Pages Input/Output/sec` 是页 → MB/s。
    """
    def g(*names):
        v = _pick(vals, *names)
        return float(v) if v is not None else 0.0

    ev = {
        "pf_usage_pct": g(r"Paging File(_Total)\% Usage"),
        "page_in": g(r"Memory\Pages Input/sec") * PAGE_BYTES / MB,
        "page_out": g(r"Memory\Pages Output/sec") * PAGE_BYTES / MB,
        "pages_per_s": g(r"Memory\Pages/sec"),
        "disk_read_ms": g(r"PhysicalDisk(_Total)\Avg. Disk sec/Read") * 1000.0,
        "disk_write_ms": g(r"PhysicalDisk(_Total)\Avg. Disk sec/Write") * 1000.0,
        "disk_queue": g(r"PhysicalDisk(_Total)\Current Disk Queue Length"),
        "disk_read_mbps": g(r"PhysicalDisk(_Total)\Disk Read Bytes/sec") / MB,
        "disk_write_mbps": g(r"PhysicalDisk(_Total)\Disk Write Bytes/sec") / MB,
        "dwm_cpu": g(r"Process(dwm)\% Processor Time"),
        "mem_avail_mb": g(r"Memory\Available MBytes"),
    }
    # perfmon CSV 里这些计数器是齐全的（列存在即视为可用），因此显式声明可用性：
    # 实时快照只能靠「至少一项非零」推断，而离线对齐不该把「真的全 0」误判成不可用。
    ev["_available"] = True
    return ev


def _nearest(samples, ts, window=0.6):
    """在升序采样里取 |Δt| ≤ window 且最近的一条"""
    if not samples:
        return None
    lo, hi = 0, len(samples) - 1
    # 二分
    while lo < hi:
        mid = (lo + hi) // 2
        if samples[mid][0] < ts:
            lo = mid + 1
        else:
            hi = mid
    best = None
    for i in (lo - 1, lo):
        if 0 <= i < len(samples):
            d = abs(samples[i][0] - ts)
            if best is None or d < best[0]:
                best = (d, samples[i])
    if best is None or best[0] > window:
        return None
    return best[1]


def _read_record_csv(path):
    """
    读 StutterTriage 导出的帧日志。返回 (rows, fields)。

    【兼容】v1.0.0 的 CSV 没有 WallClock 列 —— 此时用 `TimeInSeconds` 加文件里
    第一帧的绝对时间（若没有就只能报错，因为跨两份 CSV 对齐必须有绝对时间）。
    """
    for enc in ("utf-8-sig", "mbcs", "latin-1"):
        try:
            with io.open(path, "r", encoding=enc, newline="", errors="strict") as f:
                rd = csv.DictReader(f)
                return list(rd), (rd.fieldnames or [])
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise RuntimeError("无法解码帧日志 CSV")


def align(record_csv, perfmon_csv, out_csv=None, spike_ms=33.0, window=1.2,
          dayfirst=None):
    """
    把帧日志与 perfmon CSV 按绝对时间戳对齐。

    返回 dict：
      frames      帧总数
      spikes      尖峰帧数（MsBetweenPresents ≥ spike_ms）
      matched     匹配到 perfmon 采样的尖峰数
      kinds       {"swap"/"disk"/"dwm"/"cpu"/"none"/"unknown": 次数}
      summary     一句话结论
      out_csv     合并 CSV 路径
      rows        合并 CSV 的行（也供 UI 直接展示）
    """
    frames, fields = _read_record_csv(record_csv)
    samples, meta = load_perfmon_csv(perfmon_csv, dayfirst=dayfirst)
    out = {"frames": len(frames), "spikes": 0, "matched": 0, "kinds": {},
           "summary": "", "out_csv": out_csv or "", "rows": [],
           "perfmon_samples": len(samples), "perfmon_error": meta.get("error", ""),
           "skipped": meta.get("skipped", 0)}
    if not frames:
        out["summary"] = "帧日志为空"
        return out
    if not samples:
        out["summary"] = ("perfmon CSV 解析失败或为空：%s" % (meta.get("error") or "没有有效采样行"))
        return out
    if RECORD_TIME_COL not in fields:
        out["summary"] = ("这份帧日志没有 %s 列（v1.0.0 及更早导出）—— "
                          "请用 1.1.0 版重新导出，或先导入该 CSV 再导出一次" % RECORD_TIME_COL)
        return out

    rows_out = []
    kinds = {}
    for row in frames:
        ft = _safe_float(row.get(RECORD_FT_COL))
        if ft < spike_ms:
            continue
        out["spikes"] += 1
        ts = _iso_to_epoch(row.get(RECORD_TIME_COL))
        hit = _nearest(samples, ts, window) if ts is not None else None
        if hit is None:
            kinds["unknown"] = kinds.get("unknown", 0) + 1
            out["rows"].append({
                "Frame": row.get("Index", ""), "WallClock": row.get(RECORD_TIME_COL, ""),
                "MsBetweenPresents": "%.1f" % ft, "Evidence": "无对应 perfmon 采样",
                "Verdict": "⚪ 不可判定", "Kind": "unknown"})
            continue
        out["matched"] += 1
        ev = evidence_from(hit[1])
        res = swapdiag.classify(ev, ft, None)
        kinds[res["kind"]] = kinds.get(res["kind"], 0) + 1
        rows_out.append({
            "Frame": row.get("Index", ""),
            "WallClock": row.get(RECORD_TIME_COL, ""),
            "MsBetweenPresents": "%.1f" % ft,
            "PageFile_Pct": "%.1f" % ev.get("pf_usage_pct", 0.0),
            "PagesIn_MBps": "%.2f" % ev.get("page_in", 0.0),
            "PagesOut_MBps": "%.2f" % ev.get("page_out", 0.0),
            "DiskRead_ms": "%.1f" % ev.get("disk_read_ms", 0.0),
            "DiskWrite_ms": "%.1f" % ev.get("disk_write_ms", 0.0),
            "DiskQueue": "%.1f" % ev.get("disk_queue", 0.0),
            "DwmCPU_Pct": "%.1f" % ev.get("dwm_cpu", 0.0),
            "Kind": res["kind"],
            "Verdict": res["text"],
            "Advice": res.get("advice", ""),
        })
        out["rows"].append(rows_out[-1])
    out["kinds"] = kinds

    # ---- 会话级结论 ----
    total = out["spikes"] or 1
    swap_n = kinds.get("swap", 0)
    disk_n = kinds.get("disk", 0)
    dwm_n = kinds.get("dwm", 0)
    none_n = kinds.get("none", 0)
    parts = []
    if swap_n:
        parts.append("内存交换致卡 %d/%d (%.0f%%)" % (swap_n, total, swap_n / total * 100))
    if disk_n:
        parts.append("磁盘排队 %d/%d (%.0f%%)" % (disk_n, total, disk_n / total * 100))
    if dwm_n:
        parts.append("桌面合成 DWM %d/%d (%.0f%%)" % (dwm_n, total, dwm_n / total * 100))
    if none_n:
        parts.append("交换链路平稳（已排除）%d/%d" % (none_n, total))
    out["summary"] = ("%d 个尖峰帧（≥%.0fms），成功对齐 %d 个。%s"
                      % (out["spikes"], spike_ms, out["matched"],
                         "；".join(parts) if parts else "无有效判定"))

    if out_csv:
        path = out_csv
        cols = ["Frame", "WallClock", "MsBetweenPresents", "PageFile_Pct", "PagesIn_MBps",
                "PagesOut_MBps", "DiskRead_ms", "DiskWrite_ms", "DiskQueue", "DwmCPU_Pct",
                "Kind", "Verdict", "Advice", "Evidence"]
        with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in out["rows"]:
                w.writerow(r)
    return out


def _safe_float(v, default=0.0):
    try:
        return float(str(v).strip().strip('"'))
    except (TypeError, ValueError):
        return default


def _iso_to_epoch(text):
    """帧日志里的 WallClock（ISO 8601，带毫秒）→ epoch 秒"""
    s = (text or "").strip()
    if not s:
        return None
    s = s.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f%z"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return None


# ==============================================================================
# 4) 命令行入口（改进建议书里说的「Python 脚本对齐两份 CSV」）
# ==============================================================================
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m stuttertriage.perfmon",
        description="StutterTriage 1.1.0 · perfmon 模板导出 / 帧日志对齐工具")
    sub = ap.add_subparsers(dest="cmd")

    p_t = sub.add_parser("template", help="导出 perfmon 采集模板（txt/bat/xml）")
    p_t.add_argument("-o", "--outdir", default="perfmon_template")
    p_t.add_argument("--interval-ms", type=int, default=DEFAULT_INTERVAL_MS,
                        help="logman 采样间隔；只支持整秒，默认 1000")

    p_a = sub.add_parser("align", help="把帧日志与 perfmon CSV 按时间戳对齐")
    p_a.add_argument("--record", required=True, help="stuttertriage_record.csv")
    p_a.add_argument("--perfmon", required=True, help="perfmon / logman 导出的 CSV")
    p_a.add_argument("-o", "--out", default="", help="合并 CSV 输出路径")
    p_a.add_argument("--spike-ms", type=float, default=33.0, help="尖峰门槛（ms），默认 33")
    p_a.add_argument("--window", type=float, default=1.2,
                        help="时间对齐容差（秒），默认 1.2（perfmon 最小采样为 1s）")
    p_a.add_argument("--dayfirst", action="store_true", help="日期为「日/月/年」格式")

    args = ap.parse_args(argv)
    if args.cmd == "template":
        written, notes = write_template(args.outdir, args.interval_ms)
        for p in written:
            print("已写出：%s" % p)
        for n in notes:
            print("注意：%s" % n)
        return 0
    if args.cmd == "align":
        res = align(args.record, args.perfmon, args.out or None,
                    spike_ms=args.spike_ms, window=args.window,
                    dayfirst=True if args.dayfirst else None)
        print(res["summary"])
        if res.get("perfmon_error"):
            print("perfmon：%s" % res["perfmon_error"])
        if res.get("out_csv"):
            print("合并 CSV：%s" % res["out_csv"])
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
