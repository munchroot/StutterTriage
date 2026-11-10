# -*- coding: utf-8 -*-
"""
内存-磁盘交换（swap）证据链 —— 「这次尖峰到底是不是内存交换造成的？」

【为什么单独一个模块】
ST 改进建议书的核心批评是：PresentMon / 旧版 CSV 里的 `PageIn_MBps` 只统计
「进程读文件速率」，**不包含 Windows 页面文件（pagefile.sys）的换入换出**，
也没有硬缺页、磁盘延迟与磁盘队列。于是「窗口化时 >100ms 的尖峰」只能靠猜：

    · 是物理内存不足触发 pagefile 交换？（需要 pagefile 占用 + 硬缺页 + 磁盘排队）
    · 还是桌面合成 DWM / 壁纸进程 / CPU 调度抢占？（需要 dwm CPU + 单核拥堵）

本模块把硬件快照里这些**实测**指标按「尖峰帧 ±window 秒」聚合，给出一个
**可证伪**的结论：要么指出交换/磁盘/DWM 三条链路上哪一条与尖峰同向，
要么明确回答「这三条链路全部平稳 ⇒ 排除内存交换」，而不是含糊其辞。

【设计红线】
  · 只用实测值：任一链路数据缺失就标「不可判定」，绝不拿 0 去支持正向结论。
  · 单一指标出现不算命案：交换必须「pagefile/硬缺页」与「磁盘排队」同向，
    才升级成 DANGER（只有磁盘真的排队，才会把 CPU 线程阻塞成可见卡顿）。
  · 纯函数优先：`sample` / `classify` 都不碰 UI，便于单测与离线 CSV 复用。
"""

import bisect

# 【工程约定·与全项目一致】这些阈值全部登记在 consts.LIVE_CONST_NAMES 里，
# 一律靠 bind_globals 注入**可变副本**，禁止 `from .consts import SWAP_...`
# —— 直接 import 会在导入期把值冻结，stuttertriage_config.json 热更新到不了本模块
# （tests/test_static_integrity.py 会直接判失败）。
from .consts import bind_globals as _bind_const_globals
_bind_const_globals(globals())

# 尖峰判定的默认绝对下限（ms）。会话级统计与诊断卡都用同一口径。
SPIKE_ABS_MS = 33.0

# 单帧归因的取样窗口（秒，向前/向后各取这么多）。硬件快照 0.4s 一轮，
# ±0.6s 大约能覆盖 3 轮采样 —— 既能盖住 100ms 尖峰，又不会把无关时段拉进来。
SAMPLE_WINDOW_S = 0.6

# 三条链路各自取哪些快照字段（max 聚合：宁愿抓到瞬时峰值，也不要被均值抹平）
_FIELDS = ("pf_usage_pct", "pf_used_mb", "page_in", "page_out", "pages_per_s",
           "disk_read_ms", "disk_write_ms", "disk_queue", "disk_read_mbps",
           "disk_write_mbps", "dwm_cpu", "cpu0_usage", "cpu_total",
           "game_ws_mb", "game_pf_s", "mem_avail_mb")


def _num(v, default=0.0):
    """安全转 float（None / 非数字 / 缺失字段一律回落）"""
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _f(obj, name, default=0.0):
    """
    安全取浮点。

    支持两种证据载体：
      · HardwareSnapshot（实时采集，字段是属性）
      · dict（perfmon CSV 对齐时的证据包，例如 perfmon.evidence_from()）
    字段缺失 / None / 非数字一律回落 default —— 绝不拿 None 参与比较炸掉归因。
    """
    try:
        if isinstance(obj, dict):
            v = obj.get(name, default)
        else:
            v = getattr(obj, name, default)
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


# ==============================================================================
# 1) 取样：把「尖峰帧 ±window 秒」内的快照压成一条证据
# ==============================================================================
def sample(snaps, window_center=None, fields=None):
    """
    把若干快照聚合成一条「交换证据」。

    snaps: HardwareSnapshot 列表（可以是该时间段内的全部快照）
    返回 dict：各字段的 max 值 + `n`（参与聚合的快照数）+ `seq`（峰值快照序号）
    """
    fields = fields or _FIELDS
    out = {"n": 0, "seq": -1}
    for name in fields:
        out[name] = 0.0
    if not snaps:
        return out
    out["n"] = len(snaps)
    for name in fields:
        best_v = 0.0
        best_seq = -1
        for s in snaps:
            v = _f(s, name, 0.0)
            if abs(v) > abs(best_v):
                best_v = v
                best_seq = getattr(s, "seq", -1)
        out[name] = best_v
        if best_seq >= 0:
            out["seq"] = best_seq
    return out


def snapshots_near(snap_ts, snapshots, ts, window=SAMPLE_WINDOW_S):
    """
    取时间上落在 [ts-window, ts+window] 的快照。

    snap_ts: 与 snapshots 平行的时间戳列表（升序）
    """
    if not snap_ts or not snapshots:
        return []
    lo = bisect.bisect_left(snap_ts, ts - window)
    hi = bisect.bisect_right(snap_ts, ts + window)
    if hi <= lo:
        # 时间窗内没有快照（探针刚起步 / 采样中断）→ 退化到最近一条
        i = min(max(bisect.bisect_left(snap_ts, ts) - 1, 0), len(snapshots) - 1)
        return [snapshots[i]]
    return list(snapshots[lo:hi])


# ==============================================================================
# 2) 判定：交换 / 磁盘 / 桌面合成 / CPU 调度 —— 谁是这一帧的嫌疑
# ==============================================================================
def signals(ev):
    """
    把一条证据拆成三条链路的布尔量（纯函数，便于单测与逐条解释）。

    返回 dict：
      swap_mem  页面文件真的在被使用（占用率或硬缺页达标）
      swap_disk 磁盘真的在排队（延迟或队列达标）
      dwm       桌面合成器占用显著
      cpu       CPU 单核拥堵 / 整机满载
      available 交换链路数据是否可用（全 0 视为计数器不可用）
    """
    pf_pct = _f(ev, "pf_usage_pct")
    page_in = _f(ev, "page_in")
    page_out = _f(ev, "page_out")
    pages_per_s = _f(ev, "pages_per_s")
    d_r = _f(ev, "disk_read_ms")
    d_w = _f(ev, "disk_write_ms")
    queue = _f(ev, "disk_queue")
    dwm = _f(ev, "dwm_cpu")
    cpu0 = _f(ev, "cpu0_usage")
    cpu_total = _f(ev, "cpu_total")

    swap_mem = (pf_pct >= SWAP_PAGEFILE_WARN_PCT
                or page_in >= SWAP_PAGES_IN_MIN_MB
                or page_out >= SWAP_PAGES_OUT_MIN_MB)
    swap_disk = (d_r >= SWAP_DISK_LAT_WARN_MS
                 or d_w >= SWAP_DISK_LAT_WARN_MS
                 or queue >= SWAP_DISK_QUEUE_WARN)
    # 「可用」判定：perfmon 对齐来的证据包会显式给 _available（因为计数器齐全，
    # 全 0 也可能是真的全 0）；实时快照只能靠「至少一项非零」来推断。
    explicit = ev.get("_available") if isinstance(ev, dict) else None
    if explicit is None:
        available = bool(pf_pct > 0.0 or pages_per_s > 0.0 or page_in > 0.0
                         or page_out > 0.0 or d_r > 0.0 or d_w > 0.0 or queue > 0.0)
    else:
        available = bool(explicit)
    return {
        "swap_mem": swap_mem,
        "swap_disk": swap_disk,
        "dwm": dwm >= DWM_CPU_WARN_PCT,
        "cpu": (cpu0 >= CPU0_JAM_THRESHOLD or cpu_total >= 90.0),
        "available": available,
        "pf_pct": pf_pct, "page_in": page_in, "page_out": page_out,
        "pages_per_s": pages_per_s, "disk_read_ms": d_r, "disk_write_ms": d_w,
        "disk_queue": queue, "dwm_cpu": dwm, "cpu0": cpu0, "cpu_total": cpu_total,
    }


def classify(ev, frametime=None, base_frametime=None):
    """
    单帧归因结论。

    返回 dict：
      kind     "swap" / "disk" / "dwm" / "cpu" / "none" / "unknown"
      severity "DANGER" / "WARN" / "NORMAL"
      text     一句话人话结论（含关键数字，便于贴到病历卡/表格里）
      advice   一招建议
      ev       原始证据
    """
    sg = signals(ev)
    ft = _num(frametime)
    base_ft = _num(base_frametime)
    spike = (base_ft > 0 and ft >= base_ft * 1.8) or (ft >= SPIKE_ABS_MS)

    if not sg["available"]:
        return {"kind": "unknown", "severity": "NORMAL", "ev": ev,
                "text": "⚪ 交换证据不可用：本机未取到 pagefile / 磁盘延迟计数器"
                        "（或该帧没有绑定硬件快照）—— 内存交换不可判定",
                "advice": ""}

    # 【文案】没有帧上下文时（例如内存页直接调用）不能报「阻塞成 0 ms 的尖峰」
    spike_txt = ("，磁盘排队直接把渲染线程阻塞成 %.0f ms 的尖峰" % ft) if ft > 0 else ""
    if sg["swap_mem"] and sg["swap_disk"]:
        sev = "DANGER" if (sg["pf_pct"] >= SWAP_PAGEFILE_DANGER_PCT
                           or sg["disk_read_ms"] >= SWAP_DISK_LAT_DANGER_MS
                           or sg["disk_write_ms"] >= SWAP_DISK_LAT_DANGER_MS) else "WARN"
        return {"kind": "swap", "severity": sev, "ev": ev,
                "text": ("🔴 内存-磁盘交换型卡顿：页面文件占用 %.0f%%、"
                         "换入 %.1f / 换出 %.1f MB/s，同时磁盘延迟 读%.0f/写%.0f ms、"
                         "队列 %.1f —— 物理内存不足，Windows 正把内存与硬盘来回倒腾"
                         % (sg["pf_pct"], sg["page_in"], sg["page_out"],
                            sg["disk_read_ms"], sg["disk_write_ms"], sg["disk_queue"])
                         + spike_txt),
                "advice": "关掉高占内存后台（浏览器/录屏/壁纸），或加装物理内存；"
                          "页面文件所在磁盘必须是 SSD，别放在机械盘"}
    if sg["swap_mem"]:
        return {"kind": "swap", "severity": "WARN", "ev": ev,
                "text": ("🟠 已在用虚拟内存：页面文件占用 %.0f%%、换入 %.1f / 换出 %.1f MB/s"
                         "（磁盘尚未排队，暂未阻塞渲染线程）"
                         % (sg["pf_pct"], sg["page_in"], sg["page_out"])),
                "advice": "继续恶化就会变成卡顿：优先释放物理内存"}
    if sg["swap_disk"]:
        sev = "DANGER" if (sg["disk_read_ms"] >= SWAP_DISK_LAT_DANGER_MS
                           or sg["disk_write_ms"] >= SWAP_DISK_LAT_DANGER_MS) else "WARN"
        return {"kind": "disk", "severity": sev, "ev": ev,
                "text": ("🟠 物理磁盘排队（读 %.0f / 写 %.0f ms，队列 %.1f）但页面文件占用仅 %.0f%%"
                         " —— 是磁盘 IO 拥堵，不是内存交换"
                         % (sg["disk_read_ms"], sg["disk_write_ms"], sg["disk_queue"], sg["pf_pct"])),
                "advice": "检查是否有下载/解压/杀毒扫描占用同一块盘，或把游戏移到 NVMe"}
    if sg["dwm"]:
        return {"kind": "dwm", "severity": "WARN", "ev": ev,
                "text": ("🟠 桌面合成器 dwm.exe 占用 %.0f%% CPU（交换指标全平稳：页面文件 %.0f%%、"
                         "磁盘延迟 %.0f/%.0f ms）—— 尖峰更像 DWM 合成 / 壁纸进程调度抢占，"
                         "不是内存交换" % (sg["dwm_cpu"], sg["pf_pct"],
                                           sg["disk_read_ms"], sg["disk_write_ms"])),
                "advice": "改用独占全屏，或在设置里关闭动态壁纸/透明效果"}
    if sg["cpu"]:
        return {"kind": "cpu", "severity": "WARN", "ev": ev,
                "text": ("🟠 CPU 侧拥堵（CPU0 %.0f%% / 整机 %.0f%%），"
                         "而交换指标全平稳 —— 尖峰来自 CPU 调度抢占，不是内存交换"
                         % (sg["cpu0"], sg["cpu_total"])),
                "advice": "用 Process Lasso 给游戏主线程绑大核，并取消勾选 CPU0"}
    return {"kind": "none", "severity": "NORMAL", "ev": ev,
            "text": ("🟢 已排除内存交换：页面文件占用 %.0f%%、换入 %.1f / 换出 %.1f MB/s、"
                     "磁盘延迟 读%.0f/写%.0f ms、队列 %.1f 全部平稳%s"
                     % (sg["pf_pct"], sg["page_in"], sg["page_out"],
                        sg["disk_read_ms"], sg["disk_write_ms"], sg["disk_queue"],
                        ("（本帧 %.0f ms 不是尖峰帧，仅作证据展示）" % ft)
                         if (ft > 0 and not spike) else "")),
            "advice": ""}


# ==============================================================================
# 3) 会话级：多少次尖峰与哪条链路同向（可证伪的重合率）
# ==============================================================================
KIND_LABEL = {
    "swap": "内存交换(磁盘)",
    "disk": "磁盘排队",
    "dwm": "桌面合成 DWM",
    "cpu": "CPU 调度抢占",
}


def session_stats(frames, snap_ts, snapshots, base_frametime, window=SAMPLE_WINDOW_S):
    """
    会话级证据统计。

    frames: 帧列表（升序）
    snap_ts/snapshots: 平行的时间戳与快照列表
    返回 dict：
      total      尖峰帧总数
      kinds      {kind: 命中数}
      rates      {kind: 命中率}
      verdicts   {kind: 置信度文案}
      usable     参与统计的帧里有几条拿到了交换证据
    纯函数，不读 UI，不写全局。
    """
    out = {"total": 0, "kinds": {}, "rates": {}, "verdicts": {}, "usable": 0}
    if not frames:
        return out
    base_ft = _num(base_frametime)
    thr = max(SPIKE_ABS_MS, base_ft * 1.8) if base_ft > 0 else SPIKE_ABS_MS
    spikes = [f for f in frames if _f(f, "frametime") >= thr]
    if not spikes:
        return out
    out["total"] = len(spikes)
    kinds = {}
    usable = 0
    for fr in spikes:
        ev = sample(snapshots_near(snap_ts, snapshots, _f(fr, "wall_ts"), window))
        if not ev["n"]:
            continue
        res = classify(ev, _f(fr, "frametime"), base_ft)
        if res["kind"] == "unknown":
            continue
        usable += 1
        if res["kind"] != "none":
            kinds[res["kind"]] = kinds.get(res["kind"], 0) + 1
    out["usable"] = usable
    total = float(len(spikes))
    out["kinds"] = kinds
    for kind, hit in kinds.items():
        rate = hit / total
        out["rates"][kind] = rate
        if rate >= SWAP_COINCIDE_MIN_RATE:
            conf = "高"
        elif rate >= 0.35:
            conf = "中"
        elif rate >= 0.15:
            conf = "低"
        else:
            conf = "极低（可能是巧合）"
        out["verdicts"][kind] = conf
    return out


def session_text(stats, max_items=3):
    """把会话级统计翻成一句话（诊断卡第 12 行 / 病历卡用）"""
    if not stats or not stats.get("total"):
        return "📊 交换证据：本会话还没有尖峰帧，暂无统计"
    total = stats["total"]
    kinds = stats.get("kinds") or {}
    if not kinds:
        return ("📊 交换证据：%d 次尖峰里 0 次与内存交换/磁盘排队/DWM 同向"
                "（可用样本 %d）—— 交换链路已排除" % (total, stats.get("usable", 0)))
    parts = []
    for kind, hit in sorted(kinds.items(), key=lambda kv: -kv[1])[:max_items]:
        parts.append("%s %d/%d (%.0f%%·置信度%s)"
                     % (KIND_LABEL.get(kind, kind), hit, total,
                        hit / float(total) * 100.0, stats["verdicts"].get(kind, "低")))
    return "📊 交换证据：%d 次尖峰中 %s" % (total, "；".join(parts))
