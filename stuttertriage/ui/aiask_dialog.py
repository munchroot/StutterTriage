# -*- coding: utf-8 -*-
"""
「一键问 AI」窗口 —— 导出 CSV 后弹出来，把结论压成一段可复制的专家提示词。

【交互设计】
一个窗口只做三件事，全部一眼可见、不用翻页：

  ① 顶部告示：CSV 已导到哪里（带「打开文件夹」，解决"导出了但找不到"）
  ② 主体：可复制的两段文本 —— 「专家提示词」（默认，长）与「一句话版」（短，
     适合塞进聊天框第一句）
  ③ 底部按钮：复制提示词 / 复制一句话 / 打开 CSV 位置 / 关闭

提示词本身由 `stuttertriage.aiask.build_prompt()` 生成（纯文本、可单测），
本窗口不参与任何判定，只负责展示与复制。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
                             QPushButton, QTabWidget, QVBoxLayout)

from .. import theme as T


class AskAiDialog(QDialog):
    """
    一键问 AI 结果窗口。

    用法：
        dlg = AskAiDialog(parent)
        dlg.set_content(prompt, short, csv_path)
        dlg.exec()
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AskAiDialog")
        self.setWindowTitle("StutterTriage · 一键问 AI")
        self.resize(880, 620)
        self.prompt_text = ""
        self.short_text = ""
        self.csv_path = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        self.lbl_title = QLabel("🤖 一键问 AI —— 把这份体检结果交给 AI 电脑专家")
        self.lbl_title.setStyleSheet("font-size: 14px; font-weight: bold;")
        root.addWidget(self.lbl_title)

        self.lbl_csv = QLabel("—")
        self.lbl_csv.setWordWrap(True)
        self.lbl_csv.setStyleSheet("font-size: 11px;")
        root.addWidget(self.lbl_csv)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("AskAiTabs")

        self.edit_prompt = QPlainTextEdit()
        self.edit_prompt.setReadOnly(True)
        self.edit_prompt.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.tabs.addTab(self.edit_prompt, "专家提示词（推荐）")

        self.edit_short = QPlainTextEdit()
        self.edit_short.setReadOnly(True)
        self.edit_short.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.tabs.addTab(self.edit_short, "一句话版")
        root.addWidget(self.tabs, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_copy_prompt = QPushButton("📋 复制提示词")
        self.btn_copy_prompt.setObjectName("AskAiPrimary")
        self.btn_copy_prompt.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_prompt.clicked.connect(self.copy_prompt)

        self.btn_copy_short = QPushButton("📋 复制一句话")
        self.btn_copy_short.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_short.clicked.connect(self.copy_short)

        self.btn_open_dir = QPushButton("📂 打开 CSV 所在文件夹")
        self.btn_open_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_dir.clicked.connect(self.open_csv_dir)

        self.btn_close = QPushButton("关闭")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.clicked.connect(self.accept)

        row.addWidget(self.btn_copy_prompt)
        row.addWidget(self.btn_copy_short)
        row.addWidget(self.btn_open_dir)
        row.addStretch()
        row.addWidget(self.btn_close)
        root.addLayout(row)

        self.lbl_status = QLabel("提示：复制后粘到 ChatGPT / DeepSeek / Kimi 等任意对话窗口即可；"
                                 "能上传文件的话，把右边那份 CSV 一起传上去，结论会准得多。")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("font-size: 11px;")
        root.addWidget(self.lbl_status)

        self.restyle()

    # ------------------------------------------------------------------ 内容
    def set_content(self, prompt, short="", csv_path=""):
        """填入提示词 / 一句话版 / CSV 路径"""
        self.prompt_text = str(prompt or "")
        self.short_text = str(short or self.prompt_text)
        self.csv_path = str(csv_path or "")
        self.edit_prompt.setPlainText(self.prompt_text)
        self.edit_short.setPlainText(self.short_text)
        if self.csv_path:
            self.lbl_csv.setText("逐帧 CSV 已导出：%s" % self.csv_path)
        else:
            self.lbl_csv.setText("本次没有导出 CSV（没有采集到帧数据）")
        self.btn_open_dir.setEnabled(bool(self.csv_path))

    # ------------------------------------------------------------------ 动作
    def copy_prompt(self):
        self._copy(self.prompt_text, "✅ 完整提示词已复制到剪贴板")

    def copy_short(self):
        self._copy(self.short_text, "✅ 一句话版已复制到剪贴板")

    def _copy(self, text, ok_msg):
        try:
            cb = QApplication.clipboard()
            cb.setText(text or "")
            self.lbl_status.setText(ok_msg)
        except Exception as ex:          # 剪贴板被其它程序占用时不该崩
            self.lbl_status.setText("复制失败：%s（可手动全选文本后 Ctrl+C）" % ex)

    def open_csv_dir(self):
        import os
        from ..winadmin import open_dir_in_explorer
        try:
            if self.csv_path and os.path.isfile(self.csv_path):
                open_dir_in_explorer(os.path.dirname(self.csv_path))
            elif self.csv_path:
                open_dir_in_explorer(self.csv_path)
        except Exception as ex:
            self.lbl_status.setText("打开目录失败：%s" % ex)

    # ------------------------------------------------------------------ 主题
    def restyle(self):
        th = T.CURRENT_THEME
        self.setStyleSheet(
            "QDialog#AskAiDialog { background-color: %s; }"
            "QLabel { color: %s; }"
            "QTabWidget::pane { border: 1px solid %s; border-radius: 6px; }"
            "QPlainTextEdit { background-color: %s; color: %s; border: 1px solid %s;"
            " border-radius: 6px; padding: 6px; font-family: 'Consolas', 'Microsoft YaHei';"
            " font-size: 12px; }"
            "QPushButton { background-color: %s; color: %s; border: 1px solid %s;"
            " border-radius: 6px; padding: 6px 12px; font-weight: bold; }"
            "QPushButton:hover { border-color: %s; }"
            "QPushButton#AskAiPrimary { background-color: %s; color: %s; border: none; }"
            % (th.bg, th.fg, th.border, th.panel, th.fg, th.border,
               th.panel2, th.fg, th.border, th.accent, th.accent, th.bg))
        self.lbl_title.setStyleSheet("font-size: 14px; font-weight: bold; color: %s;" % th.accent)
        self.lbl_csv.setStyleSheet("font-size: 11px; color: %s;" % th.dim)
        self.lbl_status.setStyleSheet("font-size: 11px; color: %s;" % th.dim)
