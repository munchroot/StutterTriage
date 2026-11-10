# -*- coding: utf-8 -*-
"""
v1.1.0 内存-磁盘交换证据链 / perfmon 对齐 —— 自动化测试。

对应 ST 改进建议书的核心诉求：
  「这份 CSV 只能看到帧与 GPU/CPU 忙碌时间，不能直接定位是不是内存不够导致
    交换磁盘引发卡顿」→ v1.1.0 补上 pagefile 占用、硬缺页、磁盘延迟/队列、
    DWM 占用，并要求「用时间戳把帧日志与 perfmon CSV 对齐」。

运行：
    python tests/test_swap_evidence.py
"""

import csv
import io
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stuttertriage import consts, perfmon, swapdiag       # noqa: E402
from stuttertriage.models import HardwareSnapshot         # noqa: E402
from stuttertriage.probes import PdhSession               # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  [OK  ] %s %s" % (name, detail))
    else:
        print("  [FAIL] %s %s" % (name, detail))
        FAILS.append(name)


class _Sheet(object):
    """极简 HardwareSnapshot 替身（只带需要的字段）"""

    def __init__(self, seq=1, ts=0.0, **kw):
        self.seq = seq
        self.ts = ts
        for k, v in kw.items():
            setattr(self, k, v)


class _Frame(object):
    def __init__(self, ts, ft):
        self.wall_ts = ts
        self.frametime = ft


# ==============================================================================
# 1. 判定逻辑（纯函数）
# ==============================================================================
def test_classify():
    print("== 1. 交换证据判定（纯函数）==")
    # ① pagefile 高 + 硬缺页 + 磁盘排队 → 交换型 DANGER
    ev = swapdiag.sample([_Sheet(pf_usage_pct=85.0, page_in=120.0, page_out=45.0,
                                 disk_read_ms=42.0, disk_write_ms=15.0, disk_queue=7.0,
                                 dwm_cpu=2.0, cpu0_usage=30.0, cpu_total=40.0)])
    r = swapdiag.classify(ev, 130.0, 16.0)
    check("交换型命中", r["kind"] == "swap" and r["severity"] == "DANGER", r["kind"])
    check("结论里有 pagefile 数字", "页面文件占用 85%" in r["text"], r["text"][:40])
    check("给了处置建议", bool(r.get("advice")))

    # ② 全部平稳 → 明确「已排除内存交换」
    ev2 = swapdiag.sample([_Sheet(pf_usage_pct=9.0, page_in=0.2, page_out=0.1,
                                  disk_read_ms=0.4, disk_write_ms=1.2, disk_queue=0.0,
                                  dwm_cpu=0.3, cpu0_usage=20.0, cpu_total=25.0)])
    r2 = swapdiag.classify(ev2, 140.0, 16.0)
    check("平稳 → 排除交换", r2["kind"] == "none" and r2["severity"] == "NORMAL", r2["kind"])
    check("排除结论写清楚", "已排除内存交换" in r2["text"], r2["text"][:30])

    # ③ 只有 DWM 高 → 指向桌面合成，而不是交换
    ev3 = swapdiag.sample([_Sheet(pf_usage_pct=11.0, page_in=0.1, page_out=0.0,
                                  disk_read_ms=0.8, disk_write_ms=1.0, disk_queue=0.0,
                                  dwm_cpu=28.0)])
    r3 = swapdiag.classify(ev3, 150.0, 16.0)
    check("DWM 抢占命中", r3["kind"] == "dwm", r3["kind"])
    check("DWM 结论提到排除交换", "不是内存交换" in r3["text"])

    # ④ 磁盘延迟高但 pagefile 低 → 磁盘排队，不是交换
    ev4 = swapdiag.sample([_Sheet(pf_usage_pct=8.0, page_in=0.0, page_out=0.0,
                                  disk_read_ms=55.0, disk_write_ms=3.0, disk_queue=3.0)])
    r4 = swapdiag.classify(ev4, 120.0, 16.0)
    check("磁盘排队命中", r4["kind"] == "disk", r4["kind"])

    # ⑤ 计数器缺失 → 不可判定（绝不假装平稳）
    ev5 = swapdiag.sample([])
    r5 = swapdiag.classify(ev5, 120.0, 16.0)
    check("无数据 → 不可判定", r5["kind"] == "unknown", r5["kind"])


def test_session_stats():
    print("== 2. 会话级重合率 ==")
    frames = [_Frame(100.0 + i * 0.01, 16.0) for i in range(50)]
    frames.append(_Frame(100.5, 140.0))
    frames.append(_Frame(102.5, 150.0))
    snaps = [
        _Sheet(seq=1, ts=100.4, pf_usage_pct=88.0, page_in=200.0, page_out=60.0,
               disk_read_ms=40.0, disk_write_ms=12.0, disk_queue=6.0, dwm_cpu=1.0,
               cpu0_usage=20.0, cpu_total=30.0),
        _Sheet(seq=2, ts=102.4, pf_usage_pct=10.0, page_in=0.1, page_out=0.0,
               disk_read_ms=0.6, disk_write_ms=1.0, disk_queue=0.0, dwm_cpu=0.4,
               cpu0_usage=20.0, cpu_total=30.0),
    ]
    st = swapdiag.session_stats(frames, [s.ts for s in snaps], snaps, 16.0)
    check("识别出 2 个尖峰", st["total"] == 2, st["total"])
    check("只有 1 次与交换同向", st["kinds"].get("swap") == 1, st["kinds"])
    check("重合率 50%", abs(st["rates"].get("swap", 0) - 0.5) < 1e-9, st["rates"])
    txt = swapdiag.session_text(st)
    check("会话级文案含置信度", "置信度" in txt, txt)

    st0 = swapdiag.session_stats([], [], [], 16.0)
    check("空会话不炸", st0["total"] == 0)


# ==============================================================================
# 3. perfmon 模板与 CSV 解析
# ==============================================================================
_PERF_HEAD = ('"(PDH-CSV 4.0) (China Standard Time)(-480)",'
              '"\\\\PC\\Memory\\Available MBytes",'
              '"\\\\PC\\Paging File(_Total)\\% Usage",'
              '"\\\\PC\\Memory\\Pages Input/sec",'
              '"\\\\PC\\Memory\\Pages Output/sec",'
              '"\\\\PC\\PhysicalDisk(_Total)\\Avg. Disk sec/Read",'
              '"\\\\PC\\PhysicalDisk(_Total)\\Avg. Disk sec/Write",'
              '"\\\\PC\\PhysicalDisk(_Total)\\Current Disk Queue Length",'
              '"\\\\PC\\Process(dwm)\\% Processor Time"\n')


def _write_perfmon_csv(path, rows):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(_PERF_HEAD)
        for r in rows:
            f.write(",".join('"%s"' % c for c in r) + "\n")


def test_perfmon_parse_and_align():
    print("== 3. perfmon CSV 解析 + 帧日志对齐 ==")
    check("计数器清单非空", len(perfmon.COUNTERS) >= 10, len(perfmon.COUNTERS))
    paths = perfmon.counter_paths()
    check("清单含 pagefile / 磁盘延迟 / DWM",
          any("Paging File" in p for p in paths)
          and any("Avg. Disk sec/Read" in p for p in paths)
          and any("Process(dwm)" in p for p in paths))

    d = tempfile.mkdtemp(prefix="fl_swap_")
    try:
        # 模板导出（跳过 XML：那一步依赖管理员 logman）
        written, notes = perfmon.write_template(os.path.join(d, "tpl"), 200, try_xml=False)
        names = [os.path.basename(p) for p in written]
        check("写出计数器清单", "perfmon_counters.txt" in names, names)
        check("写出一键采集脚本", "start_perfmon.bat" in names and "stop_perfmon.bat" in names, names)
        check("写出说明文件", "README_perfmon.txt" in names)

        pf = os.path.join(d, "perf.csv")
        # 中文 Windows 风格（年月日）+ 一条突发交换采样
        _write_perfmon_csv(pf, [
            ("2026/11/10 22:00:00.10", "5000", "70.5", "900", "300", "0.031", "0.004", "5", "4.0"),
            ("2026/11/10 22:00:00.30", "5000", "71.0", "950", "320", "0.033", "0.004", "5", "4.0"),
        ])
        samples, meta = perfmon.load_perfmon_csv(pf)
        check("解析出 2 条采样", len(samples) == 2, len(samples))
        check("计数器归一化去掉机器名",
              "Paging File(_Total)\\% Usage" in meta["counters"], meta["counters"][:2])
        ev = perfmon.evidence_from(samples[0][1])
        check("pagefile 换算出 %", abs(ev["pf_usage_pct"] - 70.5) < 1e-6, ev["pf_usage_pct"])
        check("Pages Input 页→MB/s",
              abs(ev["page_in"] - 900 * 4096.0 / 1048576.0) < 1e-6, ev["page_in"])
        check("磁盘延迟 秒→毫秒", abs(ev["disk_read_ms"] - 31.0) < 1e-6, ev["disk_read_ms"])

        # 英文区域风格（月/日/年）也要能解析
        pf2 = os.path.join(d, "perf_us.csv")
        _write_perfmon_csv(pf2, [
            ("11/10/2026 22:00:00.10", "5000", "70.5", "900", "300", "0.031", "0.004", "5", "4.0"),
            ("11/10/2026 22:00:00.30", "5000", "71.0", "950", "320", "0.033", "0.004", "5", "4.0"),
        ])
        s2, m2 = perfmon.load_perfmon_csv(pf2)
        check("美式日期也能解析", len(s2) == 2, len(s2))
        check("自动判定日期顺序（跨度最小）",
              abs(s2[-1][0] - s2[0][0]) < 1.0, m2.get("dayfirst"))

        # 帧日志：1 帧 120ms 尖峰 + 4 帧平稳
        rec = os.path.join(d, "rec.csv")
        base = datetime(2026, 11, 10, 22, 0, 0, 100000).timestamp()
        with io.open(rec, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Index", "TimeInSeconds", "Application", "MsBetweenPresents",
                        "FPS", "WallClock"])
            for i in range(5):
                ts = base + i * 0.1
                ft = 120.0 if i == 0 else 16.6
                w.writerow([i + 1, "%.3f" % (i * 0.1), "TslGame.exe", "%.3f" % ft, "60",
                            datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]])

        out = os.path.join(d, "merged.csv")
        res = perfmon.align(rec, pf, out)
        check("对齐：找到 1 个尖峰", res["spikes"] == 1, res["spikes"])
        check("对齐：成功匹配 1 个", res["matched"] == 1, res["matched"])
        check("对齐：判定为交换", res["kinds"].get("swap") == 1, res["kinds"])
        check("对齐：写出合并 CSV", os.path.isfile(out))
        with io.open(out, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        check("合并 CSV 含 pagefile 列", "PageFile_Pct" in rows[0], list(rows[0])[:3])
        check("合并 CSV 结论可读", "内存" in rows[0]["Verdict"], rows[0]["Verdict"][:24])

        # 没有 WallClock 的旧日志：必须明确拒绝，而不是错位对齐
        rec_old = os.path.join(d, "rec_old.csv")
        with io.open(rec_old, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Index", "TimeInSeconds", "Application", "MsBetweenPresents", "FPS"])
            w.writerow([1, "0.0", "A.exe", "120.0", "60"])
        res2 = perfmon.align(rec_old, pf, None)
        check("旧日志被明确拒绝", res2["matched"] == 0 and "WallClock" in res2["summary"],
              res2["summary"][:40])
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ==============================================================================
# 4. 采集层 / 导出层 / 阈值热更新
# ==============================================================================
def test_fields_and_csv_columns():
    print("== 4. 快照字段 / 导出列 / 阈值热更新 ==")
    s = HardwareSnapshot(1)
    for name in ("pf_usage_pct", "pf_used_mb", "pf_total_mb", "committed_pct",
                 "pages_per_s", "page_faults_s", "disk_read_ms", "disk_write_ms",
                 "disk_queue", "disk_read_mbps", "disk_write_mbps", "dwm_cpu",
                 "dwm_ws_mb", "game_proc", "game_ws_mb", "game_pf_s"):
        check("快照字段 %s" % name, hasattr(s, name), getattr(s, name, "缺失"))

    from stuttertriage.ui.base_window import StutterTriageWindowBase
    cols = StutterTriageWindowBase.RECORD_COLS
    check("导出前 13 列与 1.0.0 一致",
          cols[:13] == ("Index", "TimeInSeconds", "Application", "MsBetweenPresents", "FPS",
                        "MsGPUBusy", "MsCPUBusy", "GPUBusyRatio", "CPU0_Usage",
                        "VRAM_NonLocal_MB", "PageIn_MBps", "CacheNewFiles", "DataSource"),
          cols[:3])
    for name in ("WallClock", "PageFile_Pct", "DiskReadLatency_ms", "DiskQueue",
                 "DwmCPU_Pct", "GameWS_MB", "SwapVerdict"):
        check("导出新列 %s" % name, name in cols)

    # 阈值热更新必须广播到 swapdiag（不能 import 冻结）
    old = swapdiag.SWAP_PAGEFILE_WARN_PCT
    consts.apply_values({"SWAP_PAGEFILE_WARN_PCT": 55.0})
    check("阈值热更新到达 swapdiag", abs(swapdiag.SWAP_PAGEFILE_WARN_PCT - 55.0) < 1e-9,
          swapdiag.SWAP_PAGEFILE_WARN_PCT)
    consts.apply_values({"SWAP_PAGEFILE_WARN_PCT": old})
    check("阈值可还原", abs(swapdiag.SWAP_PAGEFILE_WARN_PCT - old) < 1e-9)


def test_pdh_counters_available():
    """本机 PDH 是否真的能打开交换/磁盘计数器（只读，失败不算致命）。"""
    print("== 5. PDH 计数器可用性（本机）==")
    specs = [("pf", r"\Paging File(_Total)\% Usage"),
             ("que", r"\PhysicalDisk(_Total)\Current Disk Queue Length"),
             ("drd", r"\PhysicalDisk(_Total)\Avg. Disk sec/Read"),
             ("dwm", r"\Process(dwm)\% Processor Time")]
    sess = PdhSession()
    try:
        if not sess.open(specs):
            print("  [SKIP] 本机 PDH 打不开（无性能计数器服务？）")
            return
        sess.collect()
        got = 0
        for key, _path in specs:
            arr = sess.array(key) or {}
            if arr:
                got += 1
        check("PDH 至少拿到 3 个交换/磁盘计数器", got >= 3, "%d/4" % got)
    finally:
        sess.close()


def main():
    test_classify()
    test_session_stats()
    test_perfmon_parse_and_align()
    test_fields_and_csv_columns()
    test_pdh_counters_available()
    print()
    if FAILS:
        print("FAILED: %d -> %s" % (len(FAILS), FAILS))
        return 1
    print("=" * 60)
    print("ALL SWAP-EVIDENCE CHECKS PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
