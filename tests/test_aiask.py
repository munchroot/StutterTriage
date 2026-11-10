# -*- coding: utf-8 -*-
"""
「一键问 AI」功能测试（v1.1.0 小改动）。

覆盖：
  1. 桌面路径解析 / 报告文件名
  2. 提示词内容（角色、数据事实、输出格式要求、缺失数据显示「—」而不是编数字）
  3. 一句话版
  4. 界面：按钮存在、复制到剪贴板、内容可读
  5. 端到端：塞假数据 → 收集上下文 → 导出桌面 CSV → 生成提示词（不弹窗、不阻塞）

运行：
    python tests/test_aiask.py
"""

import csv
import io
import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stuttertriage import aiask                      # noqa: E402
from stuttertriage.models import FrameRecord, HardwareSnapshot   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  [OK  ] %s %s" % (name, detail))
    else:
        print("  [FAIL] %s %s" % (name, detail))
        FAILS.append(name)


SAMPLE = {
    "app": "TslGame.exe",
    "data_source": "real",
    "frames": 1832,
    "duration_s": 30.4,
    "avg_fps": 88.5,
    "max_fps": 143.2,
    "low1_fps": 52.1,
    "low01_fps": 21.0,
    "dropped": 27,
    "dropped_pct": 3.4,
    "gpu_busy_pct": 92.0,
    "spikes_33": 41,
    "spikes_100": 6,
    "hw": {"cpu_total": 63.0, "cpu0_usage": 88.0, "mem_avail_mb": 900.0,
           "pagefile_pct": 78.0, "pagein_mb": 120.0, "pageout_mb": 42.0,
           "disk_read_ms": 31.0, "disk_write_ms": 9.0, "disk_queue": 4.0,
           "dwm_cpu": 17.0, "vram_pct": 95.0, "gpu_clock": 2610, "gpu_temp": 71},
    "swap": {"total": 41, "kinds": {"swap": 29, "dwm": 5}, "rates": {"swap": 0.7},
             "verdicts": {"swap": "高"}},
    "issues": [("内存-磁盘交换 (pagefile 证据链)", "🔴 物理内存不足，Windows 正把内存与硬盘来回倒腾"),
               ("GPU 忙时比 (显卡饥饿判定)", "🟠 显卡满载瓶颈")],
    "events": [("10:01:02", "内存交换", "页面文件占用 78% 且磁盘延迟 读31ms")],
}


# ==============================================================================
# 1. 纯函数
# ==============================================================================
def test_paths():
    print("== 1. 桌面路径 / 文件名 ==")
    d = aiask.desktop_dir()
    check("桌面目录存在", os.path.isdir(d), d)
    name = aiask.report_filename()
    check("文件名带 StutterTriage 前缀", name.startswith("StutterTriage体检报告_"), name)
    check("文件名是 .csv", name.endswith(".csv"), name)
    check("文件名含时间戳", len(name) > len("StutterTriage体检报告_.csv") + 8, name)


def test_prompt():
    print("== 2. 提示词内容 ==")
    p = aiask.build_prompt(SAMPLE, r"C:\Users\x\Desktop\StutterTriage体检报告_20261110_230000.csv")
    check("含专家角色", "Windows 游戏性能与系统调优专家" in p)
    check("要求根因排序", "根因排序" in p)
    check("要求可执行步骤", "修复步骤" in p and "在哪改" in p)
    check("要求指出不可判定项", "不可判定" in p)
    check("要求点明矛盾", "矛盾" in p)
    check("含帧表现数字", "88.5" in p and "52.1" in p, "")
    check("含 pagefile 数字", "页面文件占用 78%" in p)
    check("含磁盘延迟数字", "31.0" in p)
    check("含交换证据结论", "内存交换(磁盘) 29 次" in p)
    check("含归因命中项原文", "pagefile 证据链" in p)
    check("含 CSV 路径", "StutterTriage体检报告_20261110_230000.csv" in p)
    check("含窗口化场景说明", "窗口化" in p)

    # 缺数据：必须显示「—」，不能编数字
    thin = {"app": "", "frames": 5, "hw": {}, "swap": {}, "issues": [], "events": []}
    p2 = aiask.build_prompt(thin, "")
    check("缺数据不炸", "—" in p2, p2.splitlines()[2][:40])
    check("缺数据不出现假 FPS", "88.5" not in p2)

    s = aiask.short_sentences(SAMPLE, r"D:\a.csv")
    check("一句话版够短", len(s) < 420, len(s))
    check("一句话版含专家要求", "性能专家" in s)
    check("一句话版含 CSV 路径", "D:\\a.csv" in s)

    # 无尖峰时说清「可排除」
    calm = dict(SAMPLE)
    calm["swap"] = {"total": 0, "kinds": {}}
    p3 = aiask.build_prompt(calm, "")
    check("无尖峰时说明暂无统计", "还没有尖峰帧" in p3)


# ==============================================================================
# 3. 界面
# ==============================================================================
def test_dialog():
    print("== 3. 弹窗与复制 ==")
    try:
        from PyQt6 import QtWidgets
        from stuttertriage.ui.aiask_dialog import AskAiDialog
    except Exception as ex:
        print("  [SKIP] PyQt6 不可用：%s" % ex)
        return
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    dlg = AskAiDialog()
    p = aiask.build_prompt(SAMPLE, r"C:\t\a.csv")
    s = aiask.short_sentences(SAMPLE, r"C:\t\a.csv")
    dlg.set_content(p, s, r"C:\t\a.csv")
    check("提示词已填入", dlg.edit_prompt.toPlainText() == p)
    check("一句话版已填入", dlg.edit_short.toPlainText() == s)
    check("两个页签", dlg.tabs.count() == 2, dlg.tabs.count())
    check("显示 CSV 路径", "a.csv" in dlg.lbl_csv.text(), dlg.lbl_csv.text()[:30])
    check("打开目录按钮可用", dlg.btn_open_dir.isEnabled())

    dlg.copy_prompt()
    check("复制提示词进剪贴板", app.clipboard().text() == p, app.clipboard().text()[:20])
    check("复制后有状态提示", "已复制" in dlg.lbl_status.text(), dlg.lbl_status.text()[:20])
    dlg.copy_short()
    check("复制一句话进剪贴板", app.clipboard().text() == s)

    dlg.set_content("", "", "")
    check("空内容不炸", dlg.edit_prompt.toPlainText() == "")
    check("无 CSV 时禁用打开目录", not dlg.btn_open_dir.isEnabled())
    dlg.close()


# ==============================================================================
# 4. 端到端（不弹窗）
# ==============================================================================
def test_end_to_end():
    print("== 4. 端到端：数据 → CSV → 提示词 ==")
    try:
        from PyQt6 import QtWidgets
        from stuttertriage.ui.main_window import StutterTriageWindow
    except Exception as ex:
        print("  [SKIP] 无法构建主窗口：%s" % ex)
        return
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    check("侧栏有一键问 AI 按钮", hasattr(win, "btn_ask_ai"))
    check("齿轮菜单有一键问 AI 项", hasattr(win, "act_ask_ai"))

    # 空数据时 _collect_ai_data 必须返回 None（由 _on_ask_ai 弹提示，不生成假报告）
    win.store.reset()
    check("无帧时返回 None", win._collect_ai_data() is None)

    # 塞 60 帧 + 1 条交换快照
    now = time.time()
    s = HardwareSnapshot(1)
    s.ts = now
    s.pf_usage_pct = 77.0
    s.page_in = 90.0
    s.disk_read_ms = 28.0
    s.disk_queue = 3.0
    s.dwm_cpu = 9.0
    s.cpu_total = 55.0
    s.cpu0_usage = 70.0
    s.mem_avail_mb = 1500.0
    s.cpu_freq = [3000.0] * 4
    s.cpu_usage = [55.0] * 4
    win.store.add_snapshot(s)
    for i in range(60):
        ft = 120.0 if i == 30 else 16.7
        win.store.add_frame(FrameRecord(i + 1, i * 0.0167, now + i * 0.0167,
                                        "TslGame.exe", 1234, ft, 10.0, 5.0))
    win._data_source = "real"

    data = win._collect_ai_data()
    check("能收集到上下文", bool(data))
    check("帧数正确", data["frames"] == 60, data["frames"])
    check("尖峰计数 ≥1", data["spikes_33"] >= 1, data["spikes_33"])
    check("带硬件数字", float(data["hw"].get("pagefile_pct") or 0) > 0, data["hw"].get("pagefile_pct"))
    check("带交换统计", "total" in data["swap"])

    d = tempfile.mkdtemp(prefix="aiask_")
    try:
        path = os.path.join(d, aiask.report_filename())
        n = win._write_record_csv(path)
        check("CSV 已写出", os.path.isfile(path) and n == 60, n)
        with io.open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        check("CSV 含 WallClock", "WallClock" in rows[0])
        check("CSV 含 pagefile 列", "PageFile_Pct" in rows[0])
        prompt = aiask.build_prompt(data, path)
        check("提示词带出 CSV 路径", path in prompt)
        check("提示词含页面文件数字", "页面文件占用" in prompt)

        # ---- 模拟真实点击：桌面目录指到临时目录，窗口 exec 换成不阻塞的假实现 ----
        import stuttertriage.ui.main_window as mw
        captured = {}

        class _FakeDialog(object):
            def __init__(self, parent=None):
                captured["created"] = True

            def set_content(self, p, s, c):
                captured["prompt"] = p
                captured["short"] = s
                captured["csv"] = c

            def exec(self):
                captured["exec"] = True
                return 0

        old_dialog = mw.AskAiDialog
        old_desktop = aiask.desktop_dir
        fake_desktop = os.path.join(d, "Desktop")
        os.makedirs(fake_desktop, exist_ok=True)
        mw.AskAiDialog = _FakeDialog
        aiask.desktop_dir = lambda: fake_desktop
        try:
            win._on_ask_ai()
        finally:
            mw.AskAiDialog = old_dialog
            aiask.desktop_dir = old_desktop
        check("点击后弹出窗口", captured.get("exec") is True)
        files = [f for f in os.listdir(fake_desktop) if f.endswith(".csv")]
        check("CSV 落到桌面目录", len(files) == 1, files)
        check("提示词已填进窗口", bool(captured.get("prompt")))
        check("窗口拿到 CSV 路径",
              captured.get("csv", "").endswith(files[0]) if files else False,
              captured.get("csv"))
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    win.close()


def main():
    test_paths()
    test_prompt()
    test_dialog()
    test_end_to_end()
    print()
    if FAILS:
        print("FAILED: %d -> %s" % (len(FAILS), FAILS))
        return 1
    print("=" * 60)
    print("ALL ASK-AI CHECKS PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
