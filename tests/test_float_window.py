# -*- coding: utf-8 -*-
"""
悬浮窗（stuttertriage/ui/float_window.py）自动化测试。

覆盖：
  F1 构造：无边框 / 置顶 / Tool 三个窗口标志 + 圆角卡片存在 + 尺寸紧凑
  F2 set_state 正常数据：游戏名 / 平均帧 / 顿挫 / 证据提示全部上屏
  F3 set_state({}) 与脏值：不炸、缺失显示 —、未锁定游戏兜底
  F4 透明度：clamp 30~100；opacity() 同口径；👁 按钮点击后档位变化
  F5 回调：set_callbacks 后三个按钮各触发一次；未接线时不报错
  F6 折叠：高度显著变小
  F7 hiddenByUser：点 ✕ 时发出
  F8 restyle：两套主题来回切都不抛异常，且颜色真的跟着变
  F9 状态徽章 / 采集按钮文案随 collect_state 变化

运行：
    python tests\\test_float_window.py
"""
import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6 import QtWidgets                      # noqa: E402
from PyQt6.QtCore import Qt                      # noqa: E402

from stuttertriage import theme as T             # noqa: E402
from stuttertriage.ui.float_window import FloatWindow   # noqa: E402

fails = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        fails.append(name)


def all_text(widget):
    """把窗口内所有 QLabel 的文字拼起来 —— 断言「界面上真的能看到」而不是只查内部字段。"""
    return " ".join(lb.text() for lb in widget.findChildren(QtWidgets.QLabel))


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    print("=" * 72)
    print("F1) 构造与窗口标志")
    print("=" * 72)
    w = FloatWindow()
    flags = w.windowFlags()
    check("构造成功", isinstance(w, QtWidgets.QWidget))
    check("无边框", bool(flags & Qt.WindowType.FramelessWindowHint))
    check("置顶", bool(flags & Qt.WindowType.WindowStaysOnTopHint))
    check("Tool 窗口（不进任务栏）", bool(flags & Qt.WindowType.Tool))
    check("背景半透明属性",
          w.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
    card = w.findChild(QtWidgets.QFrame, "FloatCard")
    check("存在 FloatCard 圆角卡片", card is not None)
    check("宽度紧凑 260~300", 260 <= w.width() <= 300, "w=%d" % w.width())
    check("展开高度 120~185", 120 <= w.height() <= 185, "h=%d" % w.height())
    check("默认不折叠", w.is_collapsed() is False)

    print()
    print("=" * 72)
    print("F2) set_state 正常数据")
    print("=" * 72)
    w.set_state({"collect_state": "running", "game": "TslGame.exe",
                 "avg_fps": 95.0, "dropped": 72, "hint": "已排除内存交换"})
    txt = all_text(w)
    check("游戏名上屏", "TslGame" in txt, txt)
    check("平均帧上屏", "95" in txt)
    check("顿挫次数上屏", "72" in txt)
    check("证据提示上屏", "已排除内存交换" in txt)
    check("运行中徽章", "运行中" in w._lbl_badge.text(), w._lbl_badge.text())
    check("运行中按钮显示暂停", w._btn_toggle.text() == "⏸", w._btn_toggle.text())

    print()
    print("=" * 72)
    print("F3) 空状态 / 脏值不炸")
    print("=" * 72)
    try:
        w.set_state({})
        ok = True
    except Exception:
        ok = False
        traceback.print_exc()
    check("set_state({}) 不抛异常", ok)
    txt = all_text(w)
    check("缺失数字显示 —", "—" in txt, txt)
    check("未锁定游戏兜底", "未锁定游戏" in txt, txt)
    check("未开始徽章", "未开始" in w._lbl_badge.text(), w._lbl_badge.text())
    check("未开始时按钮显示开始", w._btn_toggle.text() == "▶", w._btn_toggle.text())

    for bad in ({"collect_state": "???"}, {"avg_fps": None, "dropped": None},
                {"avg_fps": "abc", "game": "  "}, {"hint": ""},
                {"avg_fps": float("nan")}, {"collect_state": "paused"}):
        try:
            w.set_state(bad)
            ok = True
        except Exception:
            ok = False
            traceback.print_exc()
        check("脏值不抛异常 %r" % (bad,), ok)
    w.set_state({"collect_state": "paused"})
    check("暂停徽章", "已暂停" in w._lbl_badge.text(), w._lbl_badge.text())
    check("暂停时按钮显示开始", w._btn_toggle.text() == "▶", w._btn_toggle.text())

    print()
    print("=" * 72)
    print("F4) 透明度 clamp + 👁 循环")
    print("=" * 72)
    w.set_opacity(10)
    check("10 → 30（下限）", w.opacity() == 30, str(w.opacity()))
    w.set_opacity(200)
    check("200 → 100（上限）", w.opacity() == 100, str(w.opacity()))
    w.set_opacity(60)
    check("60 原样保留", w.opacity() == 60, str(w.opacity()))
    check("窗口不透明度与之一致",
          abs(w.windowOpacity() - 0.60) < 0.02, str(w.windowOpacity()))
    before = w.opacity()
    w._btn_eye.click()
    check("👁 点击后档位变化", w.opacity() != before,
          "%d → %d" % (before, w.opacity()))
    check("👁 走到 40（100→80→60→40 的下一个更小档）",
          w.opacity() == 40, str(w.opacity()))
    w._btn_eye.click()
    check("👁 从 40 回到 100", w.opacity() == 100, str(w.opacity()))
    w.set_opacity(30)               # 不在 100/80/60/40 档位上（外部直接设的）
    w._btn_eye.click()
    check("外部直接设 30 后再点回到 100", w.opacity() == 100, str(w.opacity()))

    print()
    print("=" * 72)
    print("F5) 回调接线")
    print("=" * 72)
    hits = {"toggle": 0, "mark": 0, "ask": 0}

    def f1():
        hits["toggle"] += 1

    def f2():
        hits["mark"] += 1

    def f3():
        hits["ask"] += 1

    w.set_callbacks(on_toggle=f1, on_mark=f2, on_ask=f3)
    w._btn_toggle.click()
    w._btn_mark.click()
    w._btn_ask.click()
    check("on_toggle 计数 +1", hits["toggle"] == 1, str(hits))
    check("on_mark 计数 +1", hits["mark"] == 1, str(hits))
    check("on_ask 计数 +1", hits["ask"] == 1, str(hits))

    w.set_callbacks()          # 解绑后点击不能报错
    try:
        w._btn_toggle.click()
        w._btn_mark.click()
        w._btn_ask.click()
        ok = True
    except Exception:
        ok = False
        traceback.print_exc()
    check("未接线时点击不报错", ok)
    check("解绑后计数不变", hits == {"toggle": 1, "mark": 1, "ask": 1}, str(hits))

    # 回调内部抛异常也必须被兜住（否则 PyQt6 会 qFatal 终止进程）
    w.set_callbacks(on_toggle=lambda: 1 / 0)
    old_stderr = sys.stderr
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
    try:
        w._btn_toggle.click()
        ok = True
    except Exception:
        ok = False
    finally:
        sys.stderr.close()
        sys.stderr = old_stderr
    check("回调抛异常不向外冒泡", ok)
    w.set_callbacks()

    print()
    print("=" * 72)
    print("F6) 折叠")
    print("=" * 72)
    w.set_collapsed(False)
    h_open = w.height()
    w.set_collapsed(True)
    h_fold = w.height()
    check("折叠后高度 < 80", h_fold < 80, "h=%d" % h_fold)
    check("折叠后明显比展开矮", h_fold < h_open - 40, "%d → %d" % (h_open, h_fold))
    check("is_collapsed() 为 True", w.is_collapsed() is True)
    w.set_collapsed(False)
    check("展开恢复原高度", abs(w.height() - h_open) <= 2,
          "%d vs %d" % (w.height(), h_open))
    check("is_collapsed() 为 False", w.is_collapsed() is False)

    print()
    print("=" * 72)
    print("F7) hiddenByUser 信号")
    print("=" * 72)
    got = {"n": 0}

    def on_hidden():
        got["n"] += 1

    w.hiddenByUser.connect(on_hidden)
    w._btn_close.click()
    check("点 ✕ 发出 hiddenByUser", got["n"] == 1, str(got))
    check("点 ✕ 后浮窗隐藏", w.isVisible() is False)

    print()
    print("=" * 72)
    print("F8) restyle 两套主题")
    print("=" * 72)
    try:
        w.restyle(T.THEME_DEEP_SPACE)
        w.restyle(T.THEME_LIGHT)
        w.restyle()                     # 缺省 = CURRENT_THEME，主窗口实际这么调
        ok = True
        err = ""
    except Exception:
        ok = False
        err = traceback.format_exc()
        sys.stderr.write(err)
    check("两套主题来回切不抛异常", ok, err.splitlines()[-1] if err else "")

    w.restyle(T.THEME_DEEP_SPACE)
    dark_qss = card.styleSheet()
    w.restyle(T.THEME_LIGHT)
    light_qss = card.styleSheet()
    check("卡片配色真的跟着主题变", dark_qss != light_qss)
    check("浅色主题底色已应用到卡片",
          T.THEME_LIGHT.panel.lower() in light_qss.lower(), light_qss)
    check("主题切换后徽章颜色仍按状态给",
          "running" != w._badge_state or "#" in w._lbl_badge.styleSheet())

    # 每个预设都要能刷一遍（自定义背景派生出的主题也一样）
    bad_presets = []
    for key, preset in T.THEME_PRESETS.items():
        try:
            w.restyle(preset)
        except Exception:
            bad_presets.append(key)
    check("全部主题预设均可 restyle", not bad_presets, str(bad_presets))

    print()
    print("=" * 72)
    print("F9) 主窗口集成契约（签名存在性）")
    print("=" * 72)
    for name in ("set_state", "set_callbacks", "restyle", "set_opacity",
                 "opacity", "is_collapsed", "set_collapsed"):
        check("公开方法存在: %s" % name, callable(getattr(w, name, None)))
    w.restyle()
    w.close()

    print()
    print("=" * 72)
    if fails:
        print("FAILED CHECKS: %s" % fails)
        return 1
    print("ALL FLOAT-WINDOW CHECKS PASSED")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
