# -*- coding: utf-8 -*-
"""
「选择游戏多选 + 桌面帧误采 + 悬浮窗接线」回归测试（v1.1.2）。

对应本轮用户反馈：
  ① 「选择游戏最好可以多选一点，有的游戏它定位不到」
  ② 「在桌面的时候不知道为什么会有如图所示的情况」（其实是 dwm 的帧被当成游戏帧）
  ③ 「游戏开全屏侦测不到」（GTA5 会以 <unknown> 上报 / 枚举不到窗口 —— 要能手动选）
  ④ 「加个浮窗」：浮窗存在、可开关、透明度可调、主窗口关闭时不留孤儿窗口

运行：
    python tests/test_target_select.py
"""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stuttertriage.analysis import FrameStore                 # noqa: E402
from stuttertriage.models import FrameRecord                  # noqa: E402
from stuttertriage.ui.multiselect import MultiSelectGames, _normalize_name  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  [OK  ] %s %s" % (name, detail))
    else:
        print("  [FAIL] %s %s" % (name, detail))
        FAILS.append(name)


def _frame(i, app, ft=16.7, ts=None):
    return FrameRecord(i, i * 0.0167, (ts or 1000.0) + i * 0.0167, app, 1, ft, 10.0, 5.0)


# ==============================================================================
# 1. 名字清洗（手动添加时容易写错）
# ==============================================================================
def test_normalize():
    print("== 1. 手动输入的进程名清洗 ==")
    check("自动补 .exe", _normalize_name("TslGame") == "TslGame.exe", _normalize_name("TslGame"))
    check("已带 .exe 不改写", _normalize_name("GTA5_Enhanced.exe") == "GTA5_Enhanced.exe")
    check("<unknown> 原样保留", _normalize_name(" <unknown> ") == "<unknown>")
    check("空输入 → 空", _normalize_name("   ") == "")
    check("带引号也清洗", _normalize_name('"game.exe"') == "game.exe")


# ==============================================================================
# 2. FrameStore：多选合并 + 桌面帧过滤
# ==============================================================================
def test_store_filters():
    print("== 2. 帧库：多选合并 / 桌面帧过滤 ==")
    st = FrameStore()
    for i in range(5):
        st.add_frame(_frame(i + 1, "TslGame.exe", ts=1000.0 + i * 0.02))
    for i in range(5):
        st.add_frame(_frame(i + 6, "GTA5_Enhanced.exe", ts=1000.01 + i * 0.02))
    for i in range(10):
        st.add_frame(_frame(i + 11, "dwm.exe", ts=1000.005 + i * 0.01))
    for i in range(3):
        st.add_frame(_frame(i + 21, "<unknown>", ts=1000.015 + i * 0.02))

    one = st.visible_frames("TslGame.exe")
    check("单选只取该进程", len(one) == 5 and all(f.app == "TslGame.exe" for f in one), len(one))

    multi = st.visible_frames(["TslGame.exe", "GTA5_Enhanced.exe"])
    check("多选合并两个进程", len(multi) == 10, len(multi))
    ts = [f.wall_ts for f in multi]
    check("多选结果按时间升序", ts == sorted(ts), ts[:3])

    none_sel = st.visible_frames([])
    check("空选择 → 空（不再默默返回全部）", none_sel == [], len(none_sel))

    gf = st.game_frames()
    apps = {f.app for f in gf}
    check("桌面帧被剔除（dwm 不在结果里）", "dwm.exe" not in apps, apps)
    check("<unknown> 必须保留（全屏独占游戏靠它）", "<unknown>" in apps, apps)
    check("游戏帧数量正确", len(gf) == 13, len(gf))


# ==============================================================================
# 3. 多选控件本身
# ==============================================================================
def test_multiselect_widget():
    print("== 3. 多选控件 ==")
    try:
        from PyQt6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    except Exception as ex:
        print("  [SKIP] PyQt6 不可用：%s" % ex)
        return
    w = MultiSelectGames()
    got = []
    w.selectionChanged.connect(lambda sel: got.append(list(sel)))

    check("初始为自动锁定（摘要文案）", "自动锁定" in w.text(), w.text())
    w.set_processes(["TslGame.exe", "GTA5_Enhanced.exe", "<unknown>"])
    w.set_selected(["TslGame.exe"], emit=True)
    check("选中 1 个 → 摘要显示进程名", "TslGame.exe" in w.text(), w.text())
    w.set_selected(["TslGame.exe", "GTA5_Enhanced.exe"], emit=True)
    check("选中 2 个 → 摘要显示多选数量", "多选 2" in w.text(), w.text())
    check("selectionChanged 带出列表", got and got[-1] == ["TslGame.exe", "GTA5_Enhanced.exe"], got[-1:])

    # 刷新进程列表时不能丢已勾选项
    w.set_processes(["TslGame.exe"], keep=True)
    check("刷新列表保留仍存在的勾选", w.selected() == ["TslGame.exe"], w.selected())

    # 列表里没有的名字（手动添加/已消失的进程）也要能保留
    w.set_selected(["SomeExe.exe"], emit=False)
    check("手动/失效名字也能保留", w.selected() == ["SomeExe.exe"], w.selected())

    w.set_selected([], emit=False)
    check("清空 → 回到自动锁定文案", "自动锁定" in w.text(), w.text())


# ==============================================================================
# 4. 主窗口接线（多选生效 / 桌面不误采 / 浮窗 / 设置卡片）
# ==============================================================================
def test_window_integration():
    print("== 4. 主窗口接线 ==")
    try:
        from PyQt6 import QtWidgets
        from stuttertriage.ui.main_window import StutterTriageWindow
    except Exception as ex:
        print("  [SKIP] 无法构建主窗口：%s" % ex)
        return
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = StutterTriageWindow()
    win._collect_started = True
    win.is_paused = False

    check("左栏是多选控件", isinstance(win.combo_procs, MultiSelectGames),
          type(win.combo_procs).__name__)
    check("顶栏有「一键问 AI」", win.btn_ask_ai.parent() is win.banner_frame)
    check("顶栏有「浮窗」开关", hasattr(win, "btn_float"), win.btn_float.text())

    # 多选 → _eff_target 返回列表
    win.combo_procs.set_selected(["TslGame.exe", "GTA5_Enhanced.exe"], emit=True)
    eff = win._eff_target()
    check("多选生效（_eff_target 返回列表）", isinstance(eff, list) and len(eff) == 2, eff)
    win.combo_procs.set_selected([], emit=True)
    win._auto_dom = None
    check("清空后回自动模式（无锁定时返回 None）", win._eff_target() is None, win._eff_target())

    # 只有桌面帧时：必须走「未检测到游戏进程」，且不把 dwm 画成游戏
    win.store.reset()
    now = time.time()
    for i in range(20):
        win.store.add_frame(FrameRecord(i + 1, i * 0.01, now + i * 0.01, "dwm.exe", 4, 10.5, 1.0, 1.0))
    win._env_tick = 1
    win._refresh_ui_inner()
    banner = win.banner_text.text()
    check("桌面帧不冒充游戏（横幅提示未检测到游戏）", "未检测到游戏" in banner, banner)
    check("桌面帧不画进曲线", win.fps_chart._frames is None or len(win.fps_chart._frames) == 0,
          getattr(win.fps_chart, "_frames", None) and len(win.fps_chart._frames))

    # 悬浮窗：创建 / 透明度 / 关闭后不留窗口
    win._apply_float_settings()
    fw = getattr(win, "_float_win", None)
    check("浮窗已创建", fw is not None)
    if fw is not None:
        check("浮窗可见", fw.isVisible())
        check("浮窗透明度来自配置（≤100 且 ≥30）", 30 <= fw.opacity() <= 100, fw.opacity())
        win._update_float_state({"avg_fps": 95.0, "dropped": 72}, "TslGame.exe")
        check("状态推送不抛异常", True)
        win.btn_float.click()
        check("顶栏按钮可关闭浮窗", not fw.isVisible())
        win.btn_float.click()
        check("顶栏按钮可再次打开", fw.isVisible())

    # 设置页：卡片化 + 浮窗控件
    check("设置页已卡片化（≥8 张卡片）", len(getattr(win, "_settings_cards", [])) >= 8,
          len(getattr(win, "_settings_cards", [])))
    check("设置页有浮窗开关", hasattr(win, "check_float"))
    check("设置页有透明度滑杆", hasattr(win, "slider_float_opacity")
          and win.slider_float_opacity.minimum() == 30
          and win.slider_float_opacity.maximum() == 100,
          (win.slider_float_opacity.minimum(), win.slider_float_opacity.maximum()))
    check("阈值编辑框仍在（21 项）", len(win.cfg_edits) == 21, len(win.cfg_edits))

    win.close()
    if fw is not None:
        check("关闭主窗口后浮窗不可见", not fw.isVisible())


def main():
    test_normalize()
    test_store_filters()
    test_multiselect_widget()
    test_window_integration()
    print()
    if FAILS:
        print("FAILED: %d -> %s" % (len(FAILS), FAILS))
        return 1
    print("=" * 60)
    print("ALL TARGET-SELECT CHECKS PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
