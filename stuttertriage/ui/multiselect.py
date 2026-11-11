# -*- coding: utf-8 -*-
"""
「选择游戏」多选控件 —— v1.1.2。

【为什么要换掉原来的 QComboBox】
原先只能选**一个**进程名，而且列表来自「当前有可见窗口的进程」：
  · 有的游戏（如全屏独占的 GTA5、被 UAC/反作弊保护的进程）枚举不到窗口，
    或者 PresentMon 报上来的 `Application` 是 `<unknown>`，用户就**无从选择**；
  · 实际排查时经常要「同时看游戏 + 它的启动器/加速器」，单选做不到。

所以这里做成 一个按钮 + 弹出菜单（checkable），支持：
  · 自动锁定主游戏（默认，单选语义）
  · 多选：勾选任意多个进程，视图按时间合并（按 wall_ts 排序）
  · 手动添加：直接输入进程名（`xxx.exe`）或 `<unknown>`，解决「列表里没有」的情况
  · 显示当前已选数量，一眼看出选了几个

【实现取舍】不用 QComboBox + 自定义 model：那种做法要自己处理键盘、宽度、阴影，
在 5 套皮肤下容易糊。QToolButton + QMenu 的 checkable 动作是原生行为，稳定且可测。
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QInputDialog, QMenu, QToolButton, QWidgetAction, QLabel,
                             QWidget, QVBoxLayout)

from .. import theme as T

AUTO_LABEL = "自动锁定主游戏"
UNKNOWN_LABEL = "<unknown>"

# 手动输入时允许的清洗：去空白、强制 .exe 后缀（PresentMon 的 Application 带 .exe）
def _normalize_name(text):
    s = (text or "").strip().strip('"')
    if not s:
        return ""
    if s.lower() == UNKNOWN_LABEL:
        return UNKNOWN_LABEL
    low = s.lower()
    if not low.endswith(".exe") and ":" not in s and "/" not in s and "\\" not in s:
        s = s + ".exe"
    return s


class MultiSelectGames(QToolButton):
    """
    多选游戏控件。

    对外 API：
      set_processes(names)     重建可选项（保留已勾选）
      selected() -> list[str]  空列表 = 自动锁定
      set_selected(list)       程序化设置（用于配置恢复 / 测试）
      selectionChanged(list)   用户改变选择时发出
      text()                   按钮上的摘要文案
    """

    selectionChanged = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MultiSelectGames")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setMinimumHeight(30)
        self._apps = []            # 可选进程名（原样大小写）
        self._checked = []         # 已勾选（原样大小写）
        self._menu = QMenu(self)
        self._menu.setObjectName("MultiSelectMenu")
        self.setMenu(self._menu)
        self._rebuild_menu()
        self.restyle()

    # ------------------------------------------------------------------ 数据
    def processes(self):
        return list(self._apps)

    def selected(self):
        return list(self._checked)

    def set_processes(self, names, keep=True):
        """重建可选项；keep=True 时保留仍在列表里的已勾选项"""
        seen = []
        lower = set()
        for n in names or []:
            s = str(n or "").strip()
            if not s:
                continue
            if s.lower() in lower:
                continue
            lower.add(s.lower())
            seen.append(s)
        # 【v1.1.3 防「卡片抽动」】不再每 2.5s 按字典序整表重排：
        #   ① 新进程只是**追加**到列表尾部（老顺序永不变化）；
        #   ② 列表完全没变时直接返回，不重建菜单 —— 菜单重建会导致
        #      按钮摘要/菜单项闪动，视觉上就是「卡片在抽动」。
        old_low = [a.lower() for a in self._apps]
        new_low = [a.lower() for a in seen]
        if old_low == new_low:
            return
        if set(new_low) >= set(old_low):
            merged = list(self._apps)
            known = set(old_low)
            for s in seen:
                if s.lower() not in known:
                    merged.append(s)
                    known.add(s.lower())
            self._apps = merged
        else:
            self._apps = seen
        if keep:
            want = {c.lower() for c in self._checked}
            self._checked = [a for a in self._apps if a.lower() in want]
        else:
            self._checked = []
        self._rebuild_menu()

    def set_selected(self, names, emit=False):
        """程序化设置已选（不要求存在于可选项里 —— 手动添加的进程也要能生效）"""
        out = []
        seen = set()
        for n in names or []:
            s = str(n or "").strip()
            if not s or s.lower() in seen:
                continue
            seen.add(s.lower())
            out.append(s)
        # 【v1.1.3 防闪动】选择没变时绝不重建菜单：_refresh_processes 每 2.5s
        # 都会调一次 set_selected，重建会让打开着的菜单整块闪一下。
        if out != self._checked:
            self._checked = out
            self._rebuild_menu()
        if emit:
            self.selectionChanged.emit(self.selected())

    # ------------------------------------------------------------------ 菜单
    def _rebuild_menu(self):
        m = self._menu
        m.clear()

        # 标题行（说明这里可以多选）
        head = QWidget()
        hl = QVBoxLayout(head)
        hl.setContentsMargins(10, 6, 10, 4)
        hl.setSpacing(2)
        t1 = QLabel("选择要监控的游戏（可多选）")
        t1.setStyleSheet("font-size: 11px; font-weight: bold; color: %s;" % T.CURRENT_THEME.dim)
        hl.addWidget(t1)
        act_head = QWidgetAction(m)
        act_head.setDefaultWidget(head)
        m.addAction(act_head)
        m.addSeparator()

        # 自动锁定
        auto = m.addAction("自动锁定主游戏（推荐）")
        auto.setCheckable(True)
        auto.setChecked(not self._checked)
        auto.triggered.connect(lambda _c=False: self._choose_auto())
        m.addSeparator()

        # 进程清单
        if self._apps:
            for name in self._apps:
                act = m.addAction(name)
                act.setCheckable(True)
                act.setChecked(any(name.lower() == c.lower() for c in self._checked))
                act.triggered.connect(lambda checked, n=name: self._toggle(n, checked))
        else:
            empty = m.addAction("（暂无有窗口的进程 —— 用下面「手动添加」）")
            empty.setEnabled(False)

        m.addSeparator()
        add_manual = m.addAction("＋ 从文件选择游戏 exe / 手动输入…")
        add_manual.triggered.connect(self._prompt_manual)
        clear = m.addAction("清空选择（回到自动锁定）")
        clear.triggered.connect(lambda _c=False: self._choose_auto())
        allact = m.addAction("全选当前列表")
        allact.triggered.connect(lambda _c=False: self.set_selected(self._apps, emit=True))
        self._update_text()

    def _toggle(self, name, checked):
        low = name.lower()
        if checked:
            if not any(c.lower() == low for c in self._checked):
                self._checked.append(name)
        else:
            self._checked = [c for c in self._checked if c.lower() != low]
        # 【v1.1.3 防闪动】勾选/取消勾选时只更新菜单里对应项的勾选态与
        # 按钮摘要，**不整菜单重建** —— m.clear()+rebuild 会把正在显示的
        # 菜单整块撕掉重画，用户看到的就是「卡片闪一下」。
        self._update_check_states()
        self._update_text()
        self.selectionChanged.emit(self.selected())

    def _choose_auto(self):
        if self._checked:
            self._checked = []
            # 只清勾选态，不重建（理由同 _toggle）
            self._update_check_states()
            self._update_text()
        self.selectionChanged.emit([])

    def _update_check_states(self):
        """菜单已打开时增量同步各 QAction 的勾选态（不重建菜单、不闪）"""
        low_set = {c.lower() for c in self._checked}
        for act in self._menu.actions():
            txt = act.text()
            if act.isCheckable() and txt == "自动锁定主游戏（推荐）":
                act.setChecked(not self._checked)
            elif act.isCheckable() and txt and not txt.startswith("（") :
                act.setChecked(txt.lower() in low_set)

    def _prompt_manual(self):
        # 【v1.1.3 用户反馈】手动添加不该让用户自己拼进程名：
        # 直接打开文件浏览器选任意 exe；找不到（全屏独占/受保护进程）
        # 时再允许手输，两种入口都保留。
        try:
            from PyQt6.QtWidgets import QFileDialog
            path, _flt = QFileDialog.getOpenFileName(
                self, "选择游戏主程序（exe）",
                "", "可执行文件 (*.exe);;所有文件 (*.*)")
        except Exception:
            path = ""
        if path:
            base = (path.replace("\\", "/").rsplit("/", 1)[-1]).strip()
            name = _normalize_name(base) or base
        else:
            name = ""
        if not name:
            # 取消选文件 → 退回手动输入（给 <unknown> 这类枚举不到的目标留口子）
            text, ok = QInputDialog.getText(
                self, "手动添加游戏进程",
                "也可以直接输入进程名（例：TslGame.exe）或 <unknown>：\n"
                "提示：全屏独占的游戏有时会以 <unknown> 上报，勾上它即可追踪。")
            if not ok:
                return
            name = _normalize_name(text)
        if not name:
            return
        # 手动添加的直接加入已勾选（即使它不在有窗口列表里）
        if not any(name.lower() == a.lower() for a in self._apps):
            self._apps.append(name)
        if not any(name.lower() == c.lower() for c in self._checked):
            self._checked.append(name)
        # 文件对话框已经关了，此时重建菜单不会闪（菜单本来就没开）
        self._rebuild_menu()
        self.selectionChanged.emit(self.selected())

    # ------------------------------------------------------------------ 外观
    def _update_text(self):
        n = len(self._checked)
        if n == 0:
            self.setText("游戏：自动锁定 ▾")
        elif n == 1:
            self.setText("游戏：%s ▾" % self._checked[0])
        else:
            self.setText("游戏：多选 %d 个 ▾" % n)
        if n:
            self.setToolTip("已选：\n" + "\n".join(self._checked))
        else:
            self.setToolTip("自动锁定主游戏（在桌面时不会把 dwm 合成帧当成游戏帧）")

    def restyle(self, theme=None):
        th = theme or T.CURRENT_THEME
        self.setStyleSheet(
            "QToolButton#MultiSelectGames { background-color: %s; color: %s;"
            " border: 1px solid %s; border-radius: 8px; padding: 4px 10px;"
            " font-size: 12px; font-weight: bold; text-align: left; }"
            "QToolButton#MultiSelectGames:hover { border-color: %s; }"
            "QToolButton#MultiSelectGames::menu-indicator { image: none; }"
            % (th.panel2, th.fg, th.border, th.accent))
