# -*- coding: utf-8 -*-
"""
悬浮窗（浮窗）：游戏里也能看到本软件在跑，并且不切窗口就能暂停 / 打点 / 问 AI。

【为什么单独做一个无边框置顶小窗，而不是复用主窗口】
主窗口一旦最小化，信息就全消失了；而用户真正需要「余光扫一眼」的时刻正是
在游戏里。浮窗刻意只有四行（标题 / 游戏 / 指标 / 提示）+ 一行按钮 —— 字号、
行高都按「离屏幕半米也能读出数字」来定，不放大段说明文字。

【为什么浮窗不做任何业务判定】
状态、游戏名、平均帧、顿挫次数、证据提示全部由主窗口通过 set_state() 灌进来。
浮窗只负责"显示 + 转发点击"，这样诊断口径只有一处（主窗口），不会出现
浮窗说「已暂停」而主窗口还在采集这种两套真相。

【线程约定】所有 set_xxx 只在 UI 线程调用。setWindowOpacity / setFixedHeight /
setWindowFlags 都是 GUI 线程专属操作；主窗口本来就在 UI 定时器里刷新，这里
再加锁或自己起线程只会引入静默丢帧，收益为负。
"""

import traceback

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import theme as T

# 徽章文案：(徽章, 采集按钮文案, 采集按钮提示)
_STATE_TEXT = {
    "running": ("● 运行中", "⏸", "暂停采集"),
    "paused": ("⏸ 已暂停", "▶", "继续采集"),
    "idle": ("○ 未开始", "▶", "开始采集"),
}

# 缺失值统一用长破折号：比 "-" 或 "--" 更不容易被误读成真实数据（如 -1 帧）
_NA = "—"


def _fmt_num(value):
    """
    数字 → 显示文本；`None` / NaN / 非数字一律 `—`。

    【为什么要区分整数与一位小数】平均帧 95.02 写成 "95" 更好扫读，
    但 7.5 帧写成 "8" 会让人误以为数据被抹平，所以只在"已经很接近整数"
    时才省略小数。
    """
    if value is None:
        return _NA
    try:
        f = float(value)
    except (TypeError, ValueError):
        return _NA
    if f != f or f in (float("inf"), float("-inf")):      # NaN / Inf
        return _NA
    if abs(f - round(f)) < 0.05:
        return str(int(round(f)))
    return "%.1f" % f


class FloatWindow(QWidget):
    """置顶无边框的小卡片窗口（圆角卡片 + 标题 / 游戏 / 指标 / 提示 四行 + 按钮条）。"""

    # 用户点 ✕ 时发出：主窗口据此记住「用户主动关了浮窗」，下次启动不要强行弹出来
    hiddenByUser = pyqtSignal()

    OPACITY_STEPS = (100, 80, 60, 40)
    OPACITY_MIN = 30          # 低于 30% 在游戏画面上基本看不见，等于"被误关了"
    OPACITY_MAX = 100
    WIDTH = 272               # 紧凑但不至于把游戏名截断成两个字

    def __init__(self, parent=None):
        super().__init__(parent)
        # Tool：不进任务栏、Alt+Tab 里也不出现，否则游戏内切窗口会先看到它
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # NoFocus：点浮窗不抢走游戏的键盘焦点，避免点一下就再也按不动 WASD
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._state = {}
        self._callbacks = {"on_toggle": None, "on_mark": None, "on_ask": None}
        self._theme = None
        self._badge_state = "idle"
        self._collapsed = False
        self._opacity = self.OPACITY_MAX
        self._drag_offset = None
        self._hint_text = _NA
        self._game_text = "未锁定游戏"

        self._build_ui()
        self.restyle()
        self.set_state({})
        self._apply_layout_mode()
        self.setWindowTitle("StutterTriage 浮窗")

    # ------------------------------------------------------------------ 构建
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(0)

        # 外圈透明 + 内部实心圆角卡片：圆角外的像素才会真的"透"出游戏画面
        self._card = QFrame(self)
        self._card.setObjectName("FloatCard")
        root.addWidget(self._card)

        self._card_lay = QVBoxLayout(self._card)
        self._card_lay.setContentsMargins(11, 9, 11, 9)
        self._card_lay.setSpacing(4)

        # ---- 第一行：标题 + 状态徽章 ----
        self._row_head = QWidget(self._card)
        head = QHBoxLayout(self._row_head)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self._lbl_title = QLabel("◈ StutterTriage", self._row_head)
        self._lbl_badge = QLabel(_STATE_TEXT["idle"][0], self._row_head)
        head.addWidget(self._lbl_title)
        head.addStretch(1)
        head.addWidget(self._lbl_badge)
        self._card_lay.addWidget(self._row_head)

        # ---- 第二~四行：折叠时整块隐藏，所以装进一个容器 ----
        self._body = QWidget(self._card)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)

        # 第二行：当前游戏
        self._row_game = QWidget(self._body)
        gl = QHBoxLayout(self._row_game)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(6)
        self._lbl_game_key = QLabel("游戏", self._row_game)
        self._lbl_game = QLabel("未锁定游戏", self._row_game)
        gl.addWidget(self._lbl_game_key)
        gl.addWidget(self._lbl_game)
        gl.addStretch(1)
        body.addWidget(self._row_game)

        # 第三行：平均帧 · 顿挫次数（同一次刷新一起给出，避免两行数字互相打架）
        self._row_metrics = QWidget(self._body)
        ml = QHBoxLayout(self._row_metrics)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(4)
        self._lbl_avg_key = QLabel("平均", self._row_metrics)
        self._lbl_avg = QLabel(_NA, self._row_metrics)
        self._lbl_avg_unit = QLabel("帧 ·", self._row_metrics)
        self._lbl_drop_key = QLabel("顿挫", self._row_metrics)
        self._lbl_drop = QLabel(_NA, self._row_metrics)
        self._lbl_drop_unit = QLabel("次", self._row_metrics)
        for w in (self._lbl_avg_key, self._lbl_avg, self._lbl_avg_unit,
                  self._lbl_drop_key, self._lbl_drop, self._lbl_drop_unit):
            ml.addWidget(w)
        ml.addStretch(1)
        body.addWidget(self._row_metrics)

        # 第四行：证据 / 提示（由主窗口决定文案，浮窗只负责截断显示）
        self._row_hint = QWidget(self._body)
        hl = QHBoxLayout(self._row_hint)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        self._lbl_hint = QLabel(_NA, self._row_hint)
        hl.addWidget(self._lbl_hint)
        hl.addStretch(1)
        body.addWidget(self._row_hint)

        self._card_lay.addWidget(self._body)

        # ---- 按钮行 ----
        self._row_buttons = QWidget(self._card)
        bl = QHBoxLayout(self._row_buttons)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(5)
        self._btn_toggle = self._make_btn("▶", "开始 / 暂停采集")
        self._btn_mark = self._make_btn("★", "打点：把当前时刻标记进事件流")
        self._btn_ask = self._make_btn("🤖", "一键问 AI：把当前诊断交给 AI 解释")
        self._btn_eye = self._make_btn("👁", "透明度 100% / 80% / 60% / 40% 循环")
        self._btn_close = self._make_btn("✕", "隐藏浮窗")
        for b in (self._btn_toggle, self._btn_mark, self._btn_ask,
                  self._btn_eye, self._btn_close):
            bl.addWidget(b)
        bl.addStretch(1)
        self._card_lay.addWidget(self._row_buttons)

        self._btn_toggle.clicked.connect(self._on_toggle_clicked)
        self._btn_mark.clicked.connect(self._on_mark_clicked)
        self._btn_ask.clicked.connect(self._on_ask_clicked)
        self._btn_eye.clicked.connect(self._on_eye_clicked)
        self._btn_close.clicked.connect(self._on_close_clicked)

    def _make_btn(self, text, tip):
        b = QtWidgets.QPushButton(text, self)
        b.setObjectName("FloatBtn")
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # 同上：别抢游戏键盘焦点
        # 定宽而不是靠文字撑：五个按钮等宽才像一排开关，表情符号字体缺失时也不会缩成一团
        b.setFixedWidth(40)
        b.setFixedHeight(22)
        b.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed,
                        QtWidgets.QSizePolicy.Policy.Fixed)
        return b

    # ------------------------------------------------------------------ 尺寸
    @staticmethod
    def _row_h(*labels):
        """一行的高度 = 该行里最高字体的行高（QLabel 的 sizeHint 高度就是这个）。"""
        return max(lb.fontMetrics().height() for lb in labels)

    def _apply_layout_mode(self):
        """
        展开 / 折叠的边距与高度，全部由字体度量**算出来**。

        【为什么不用 adjustSize()】窗口在 show() 之前，QLayout 的 sizeHint 会返回
        上一次布局的缓存值：折叠后尺寸仍停在展开态，肉眼看到的就是"点了没反应"。
        这里按 字体行高 + 固定边距/间距 直接算总高，任何 DPI 缩放下都精确贴合，
        也避免折叠态留出大片空白。
        """
        self.setFixedWidth(self.WIDTH)
        root = self.layout()
        if self._collapsed:
            root_m, card_m, card_sp, body_sp, btn_h = 2, 4, 3, 0, 19
            self._body.setVisible(False)
        else:
            root_m, card_m, card_sp, body_sp, btn_h = 8, 13, 7, 4, 24
            self._body.setVisible(True)

        root.setContentsMargins(root_m, root_m, root_m, root_m)
        self._card_lay.setContentsMargins(11, card_m, 11, card_m)
        self._card_lay.setSpacing(card_sp)
        self._body.layout().setSpacing(body_sp)
        for b in (self._btn_toggle, self._btn_mark, self._btn_ask,
                  self._btn_eye, self._btn_close):
            b.setFixedHeight(btn_h)

        h = 2 * root_m + 2 * card_m
        h += self._row_h(self._lbl_title, self._lbl_badge) + card_sp
        if not self._collapsed:
            h += self._row_h(self._lbl_game_key, self._lbl_game) + body_sp
            h += self._row_h(self._lbl_avg_key, self._lbl_avg) + body_sp
            h += self._row_h(self._lbl_hint) + card_sp
        h += btn_h
        self.setFixedHeight(h)

    # ------------------------------------------------------------------ 样式
    def _set_font(self, label, px, bold=False, mono=False):
        """
        字号用 QFont 而不是 QSS 的 font-size。

        【为什么】提示行要按真实字宽做省略号截断，QFontMetrics 必须拿到最终字体；
        QSS 里的 font-size 要等 polish 之后才生效，测出来的宽度会骗人。
        """
        f = label.font()
        f.setPixelSize(int(px))
        f.setBold(bool(bold))
        if mono:
            f.setStyleHint(QtGui.QFont.StyleHint.Monospace)
            first = (T.MONO_FAMILY or "").split(",")[0].strip()
            if first:
                f.setFamily(first)
        label.setFont(f)

    def restyle(self, theme=None):
        """按主题重刷卡片 / 文字 / 按钮（主题热切换时由主窗口调用）。"""
        t = theme if theme is not None else T.CURRENT_THEME
        self._theme = t

        self._card.setStyleSheet(
            "QFrame#FloatCard { background-color: %s; border: 1px solid %s;"
            " border-radius: 12px; }" % (t.panel, t.border))

        # 字体先定，颜色再定：截断宽度依赖字体，而字体与主题无关
        self._set_font(self._lbl_title, 11, bold=True)
        self._set_font(self._lbl_badge, 10, bold=True)
        for lb in (self._lbl_game_key, self._lbl_avg_key, self._lbl_drop_key):
            self._set_font(lb, 10)
        self._set_font(self._lbl_game, 11)
        self._set_font(self._lbl_avg, 13, bold=True, mono=True)
        self._set_font(self._lbl_drop, 13, bold=True, mono=True)
        self._set_font(self._lbl_avg_unit, 10)
        self._set_font(self._lbl_drop_unit, 10)
        self._set_font(self._lbl_hint, 10)
        for b in (self._btn_toggle, self._btn_mark, self._btn_ask,
                  self._btn_eye, self._btn_close):
            self._set_font(b, 11)

        self._lbl_title.setStyleSheet("color: %s;" % t.fg)
        for lb in (self._lbl_game_key, self._lbl_avg_key, self._lbl_drop_key):
            lb.setStyleSheet("color: %s;" % t.dim)
        # KV 规范：标签暗、数值亮；平均帧走强调色、顿挫走警告色，一眼分得开
        self._lbl_game.setStyleSheet("color: %s; font-weight: bold;" % t.fg)
        self._lbl_avg.setStyleSheet("color: %s;" % t.accent)
        self._lbl_drop.setStyleSheet("color: %s;" % t.warn)
        self._lbl_avg_unit.setStyleSheet("color: %s;" % t.dim)
        self._lbl_drop_unit.setStyleSheet("color: %s;" % t.dim)
        self._lbl_hint.setStyleSheet("color: %s;" % t.dim)

        btn_qss = (
            "QPushButton#FloatBtn { background-color: %s; color: %s;"
            " border: 1px solid %s; border-radius: 5px; padding: 0px; }"
            "QPushButton#FloatBtn:hover { border-color: %s; color: %s; }"
            "QPushButton#FloatBtn:pressed { background-color: %s; }"
            % (t.panel2, t.fg, t.border, t.border_hot, t.accent, t.border))
        for b in (self._btn_toggle, self._btn_mark, self._btn_ask,
                  self._btn_eye, self._btn_close):
            b.setStyleSheet(btn_qss)

        self._refresh_state_visuals()
        self._refresh_elided()      # 字号变了，截断宽度也要重算

    # ------------------------------------------------------------------ 文案
    def set_state(self, state):
        """
        刷新全部文案。`state` 允许缺键 / 传空 dict（首次装配、采集未开始时都会这样）。

        键：collect_state("running"/"paused"/"idle")、game(str)、
            avg_fps(float|None)、dropped(int|None)、hint(str)
        """
        s = dict(state or {})
        self._state = s

        cs = str(s.get("collect_state") or "idle").strip().lower()
        if cs not in _STATE_TEXT:
            cs = "idle"
        self._badge_state = cs

        game = str(s.get("game") or "").strip()
        self._game_text = game if game else "未锁定游戏"

        self._lbl_avg.setText(_fmt_num(s.get("avg_fps")))
        self._lbl_drop.setText(_fmt_num(s.get("dropped")))

        hint = str(s.get("hint") or "").strip()
        self._hint_text = hint if hint else _NA

        self._refresh_elided()
        self._refresh_state_visuals()

    def _refresh_state_visuals(self):
        """状态相关的颜色 / 文案（主题与状态都会影响它，所以单独抽出来）。"""
        t = self._theme or T.CURRENT_THEME
        badge, toggle_text, toggle_tip = _STATE_TEXT.get(
            self._badge_state, _STATE_TEXT["idle"])
        self._lbl_badge.setText(badge)
        # 运行中=绿（正常）、暂停=琥珀（被中断）、未开始=灰（无信息）
        color = {"running": t.green, "paused": t.warn}.get(self._badge_state, t.dim)
        self._lbl_badge.setStyleSheet("color: %s;" % color)
        self._btn_toggle.setText(toggle_text)
        self._btn_toggle.setToolTip(toggle_tip)

    def _refresh_elided(self):
        """
        游戏名与提示行按可用宽度截断（省略号），完整内容放进 tooltip。

        【为什么必须截断】窗口宽度是固定的，而游戏名（EscapeFromTarkov.exe）
        和提示（"内存交换 12/20 · 硬缺页 384/s"）都会超长；不截断就会顶出卡片
        边缘，被 cards 的圆角裁成半截字，比省略号更难看。
        """
        m = self._card_lay.contentsMargins()
        avail = max(60, self.WIDTH - m.left() - m.right() - 4)

        fm = self._lbl_hint.fontMetrics()
        self._lbl_hint.setText(fm.elidedText(
            str(getattr(self, "_hint_text", _NA)),
            Qt.TextElideMode.ElideRight, avail))
        self._lbl_hint.setToolTip(str(getattr(self, "_hint_text", _NA)))

        key_w = self._lbl_game_key.fontMetrics().horizontalAdvance("游戏") + 6
        game = str(getattr(self, "_game_text", "未锁定游戏"))
        self._lbl_game.setText(self._lbl_game.fontMetrics().elidedText(
            game, Qt.TextElideMode.ElideRight, max(60, avail - key_w)))
        self._lbl_game.setToolTip(game)

    # ------------------------------------------------------------------ API
    def set_callbacks(self, on_toggle=None, on_mark=None, on_ask=None):
        """绑定三个动作；传 None 表示解绑，未绑定时点击按钮静默无操作。"""
        self._callbacks["on_toggle"] = on_toggle
        self._callbacks["on_mark"] = on_mark
        self._callbacks["on_ask"] = on_ask

    def set_opacity(self, pct):
        """设置窗口不透明度（百分比），内部夹到 30~100。"""
        try:
            v = int(round(float(pct)))
        except (TypeError, ValueError):
            return
        v = max(self.OPACITY_MIN, min(self.OPACITY_MAX, v))
        self._opacity = v
        self.setWindowOpacity(v / 100.0)

    def opacity(self):
        """当前不透明度百分比（整数），与 set_opacity() 入参同一口径。"""
        return int(self._opacity)

    def is_collapsed(self):
        return bool(self._collapsed)

    def set_collapsed(self, collapsed):
        """折叠 / 展开（折叠后只剩标题行 + 按钮行）。"""
        collapsed = bool(collapsed)
        if collapsed == self._collapsed and self.height() > 0:
            return
        self._collapsed = collapsed
        self._apply_layout_mode()
        self._refresh_elided()

    # ------------------------------------------------------------------ 槽
    def _emit_cb(self, name):
        fn = self._callbacks.get(name)
        if not callable(fn):
            return          # 主窗口还没接线：点了就什么都不做，但绝不能报错
        try:
            fn()
        except Exception:
            # PyQt6 里槽函数抛出的异常会走 qFatal 直接终止进程；
            # 浮窗是"顺便看一眼"的辅助界面，不能因为它把整场采集带走。
            traceback.print_exc()

    def _on_toggle_clicked(self):
        self._emit_cb("on_toggle")

    def _on_mark_clicked(self):
        self._emit_cb("on_mark")

    def _on_ask_clicked(self):
        self._emit_cb("on_ask")

    def _on_eye_clicked(self):
        # 取"下一个更小的档位"，当前值不在档位上（例如被主窗口设成 30%）时回到 100
        cur = self._opacity
        nxt = next((s for s in self.OPACITY_STEPS if s < cur), self.OPACITY_STEPS[0])
        self.set_opacity(nxt)

    def _on_close_clicked(self):
        self.hide()
        self.hiddenByUser.emit()

    # ------------------------------------------------------------------ 拖动
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and (
                event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        # 只有双击标题栏才折叠：误双击数值区把窗口收起来，用户会以为程序挂了
        if event.button() == Qt.MouseButton.LeftButton:
            head = QtCore.QRect(self._row_head.mapToGlobal(QtCore.QPoint(0, 0)),
                                self._row_head.size())
            if head.contains(event.globalPosition().toPoint()):
                self.set_collapsed(not self._collapsed)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)
