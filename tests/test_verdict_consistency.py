# -*- coding: utf-8 -*-
"""
「文案一致性」回归测试（v1.1.1，来自用户截图反馈）。

截图里的三处自相矛盾：
  1. 标题栏写着 **StutterTriage 1.0.0**，但跑的是 1.1.0（含一键问 AI 的构建）
  2. 平均 **95 帧 · C 级**，顶部横幅却写「**帧率偏低**」
  3. 帧率总览页头写「**全程丝滑** · 95 帧 · C 级 · **顿挫 72 次**」
  4. 病历卡兜底文案让用户「把画质调低一档」，但 12 维归因一个都没命中

运行：
    python tests/test_verdict_consistency.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stuttertriage import __version__                      # noqa: E402
from stuttertriage import stats                            # noqa: E402
from stuttertriage.analysis import DiffRow                 # noqa: E402
from stuttertriage.ui import plain                         # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  [OK  ] %s %s" % (name, detail))
    else:
        print("  [FAIL] %s %s" % (name, detail))
        FAILS.append(name)


def test_motion_verdict():
    print("== 1. motion_verdict：高帧率 + 多顿挫 ≠ 流畅 ==")
    cases = [
        ((95.0, 7.2, 7.0), "阵发性顿挫", "截图场景：95 帧但 7.2% 顿挫、1% Low 7"),
        ((95.0, 0.0, 60.0), "流畅游玩", "平稳 95 帧"),
        ((120.0, 0.0, 90.0), "电竞级丝滑", "平稳 120 帧"),
        ((45.0, 10.5, 20.0), "阵发性顿挫", "顿挫占比 ≥10%"),
        ((95.0, 4.0, 90.0), "偶发顿挫", "3% ≤ 占比 < 10%"),
        ((95.0, 0.5, 12.0), "阵发性顿挫", "占比不高但 1% Low < 平均帧 25%"),
        ((0.0, 0.0, None), "等待数据", "无数据"),
        ((-5.0, 1.0, None), "等待数据", "脏数据"),
    ]
    for args, want, why in cases:
        got = stats.motion_verdict(*args)
        check("%s → %s" % (why, want), got == want, got)


def test_page_verdict():
    print("== 2. 帧率总览页头部不得自相矛盾 ==")
    try:
        from PyQt6 import QtWidgets
        from stuttertriage.ui.pages import FpsOverviewPage
    except Exception as ex:
        print("  [SKIP] PyQt6 不可用：%s" % ex)
        return
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    page = FpsOverviewPage()

    page.refresh(None, {"avg_fps": 95.0, "grade": "C", "dropped": 72, "dropped_pct": 7.2})
    txt = page.lbl_verdict.text()
    check("95帧+72次顿挫 ⇒ 不再写「全程丝滑」", "全程丝滑" not in txt, txt)
    check("写成阵发性顿挫", "阵发性顿挫" in txt and "72" in txt, txt)

    page.refresh(None, {"avg_fps": 95.0, "grade": "A", "dropped": 1, "dropped_pct": 0.2})
    txt2 = page.lbl_verdict.text()
    check("偶发 1 次顿挫 ⇒ 基本流畅", "基本流畅" in txt2 and "全程丝滑" not in txt2, txt2)

    page.refresh(None, {"avg_fps": 95.0, "grade": "S", "dropped": 0, "dropped_pct": 0.0})
    txt3 = page.lbl_verdict.text()
    check("零顿挫 ⇒ 才算全程丝滑", "全程丝滑" in txt3, txt3)

    page.refresh(None, {"avg_fps": 95.0, "grade": "C", "dropped": 3,
                        "dropped_pct": 1.0, "stutter_now": True})
    txt4 = page.lbl_verdict.text()
    check("真顿挫优先报红", "真顿挫" in txt4, txt4)


def test_window_title_and_banner():
    print("== 3. 标题版本号 + 顶部横幅措辞 ==")
    try:
        from PyQt6 import QtWidgets
        from stuttertriage.ui.main_window import StutterTriageWindow
    except Exception as ex:
        print("  [SKIP] 无法构建主窗口：%s" % ex)
        return
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    title = win.windowTitle()
    check("标题含当前版本号", __version__ in title, title)
    check("标题不再写死 1.0.0", "1.0.0" not in title, title)

    # 直接验证横幅分支：把统计缓存设成截图里的那组数字
    win._stat_cache = {"avg_fps": 95.0, "grade": "C", "grade_label": "建议降画质",
                       "dropped": 72, "dropped_pct": 7.2, "low1_fps": 7.0}
    win.target_app = "自动锁定主游戏"
    win._auto_dom = "TslGame.exe"
    win.store.reset()
    from stuttertriage.models import FrameRecord
    for i in range(6):
        win.store.frames.append(FrameRecord(i + 1, i * 0.0167, 1000.0 + i * 0.0167,
                                            "TslGame.exe", 1, 16.7, 10.0, 5.0))
    win._env_tick = 1
    win._collect_started = True       # 走「有帧」分支，才会刷横幅
    win.is_paused = False
    win._pending_status = (True, "真实采集")
    try:
        win._refresh_ui_inner()
    except Exception as ex:
        check("_refresh_ui_inner 可执行", False, str(ex))
        win.close()
        return
    banner = win.banner_text.text()
    check("95 帧不许说「帧率偏低」", "帧率偏低" not in banner, banner)
    check("改为指出抖动/顿挫", ("顿挫" in banner) or ("抖动" in banner), banner)
    win.close()


def test_fallback_copy():
    print("== 4. 12 维全未命中时的兜底文案 ==")
    rows = [DiffRow("某个未知维度", "1", "1", "0", "🟠 有点高", "WARN")]
    info = plain.explain(rows)
    check("点明未命中已知诱因", "未命中已知诱因" in info["cause"], info["cause"])
    check("引导一键问 AI", "一键问 AI" in info["advice"], info["advice"])
    check("不再只说降画质", "调低一档" not in info["advice"], info["advice"])


def main():
    test_motion_verdict()
    test_page_verdict()
    test_window_title_and_banner()
    test_fallback_copy()
    print()
    if FAILS:
        print("FAILED: %d -> %s" % (len(FAILS), FAILS))
        return 1
    print("=" * 60)
    print("ALL VERDICT-CONSISTENCY CHECKS PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
