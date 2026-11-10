# -*- coding: utf-8 -*-
"""
「一键问 AI」—— 把当前体检结果变成一份能直接交给 AI 的专家提问（v1.1.0 小改动）。

【为什么要有这个模块】
玩家最常见的下一步不是「再点一个按钮看曲线」，而是「拿着数据去问 AI / 论坛」。
但直接丢一份 32 列、几万行的 CSV 给 AI，得到的多半是泛泛而谈。所以这里做两件事：

  ① **导出**：把当前会话的逐帧数据写到桌面（复用 `_write_record_csv` 的 32 列，
     含 `WallClock` 绝对时间戳，可被 `perfmon.align` 继续对齐）。
  ② **提问**：把「已经算好的结论」压成一段**有角色的提示词** ——
     明确要求 AI 扮演 Windows 性能调优专家、按证据强度排序、给出可执行步骤、
     指出不可判定项与需要的补充证据。这样 AI 的答案才落在数据上，而不是编故事。

模块只做纯文本拼装（`build_prompt` / `summary_lines`），不碰 Qt、不碰 store，
因此可单测；界面在 `ui/aiask_dialog.py`。
"""

import os
import time

# 提示词里要求的角色与输出格式（改这几行就等于改产品的「问 AI」风格）
ROLE_TEXT = (
    "你是一名资深的 Windows 游戏性能与系统调优专家：熟悉 PresentMon 帧时间分析、"
    "Windows 虚拟内存/页面文件机制、DWM 桌面合成与窗口模式开销、CPU 线程调度与异构核、"
    "GPU 驱动与显存管理。下面是我用 StutterTriage 采集到的一份**真实**体检报告。"
)

OUTPUT_RULES = (
    "请严格按下面格式回答，不要客套、不要复述我的数据：\n"
    "1）根因排序：按证据强度列出最多 3 个候选根因，每个后面标注置信度（高/中/低）与"
    "一句话理由（引用我给出的具体数字）。\n"
    "2）修复步骤：最多 6 条，每条写清「改什么 → 在哪改 → 改完怎么验证」，优先给能立刻做、"
    "成本低的（关闭后台/改全屏/换电源计划），再给需要花钱或重装的（加内存/换 NVMe）。\n"
    "3）不可判定项：明确指出我这份数据里哪些结论站不住脚，以及还需要采集什么才能定论"
    "（例如需要 perfmon 同步录制、需要事件级 PIX 抓取、需要更长的采样窗口）。\n"
    "4）矛盾检查：如果证据互相矛盾（比如内存交换很高但帧时间平稳），直接说出来，不许强行下结论。"
)


# ==============================================================================
# 1) 桌面路径与文件名
# ==============================================================================
def desktop_dir():
    """
    当前用户的桌面目录。

    【为什么要读注册表】开了 OneDrive「备份桌面」的机器，桌面真实路径是
    `%USERPROFILE%\\OneDrive\\Desktop`，硬编码 `~/Desktop` 会把文件放到一个
    用户看不见的幽灵目录里（这是"导出到桌面但找不到文件"的头号原因）。
    """
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
        try:
            val, _ = winreg.QueryValueEx(key, "Desktop")
        finally:
            winreg.CloseKey(key)
        val = os.path.expandvars(str(val or ""))
        if val and os.path.isdir(val):
            return val
    except Exception:
        pass
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, "OneDrive", "Desktop"),
                 os.path.join(home, "桌面"),
                 os.path.join(home, "Desktop"),
                 os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")):
        if cand and os.path.isdir(cand):
            return cand
    return home


def report_filename(ts=None):
    """报告文件名（带本地时间戳，避免覆盖上一次）"""
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(ts if ts is not None else time.time()))
    return "StutterTriage体检报告_%s.csv" % stamp


# ==============================================================================
# 2) 人读摘要
# ==============================================================================
def _num(v, fmt="%.0f", default="—"):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return fmt % f


def summary_lines(data):
    """
    把采集上下文压成若干行「事实」（提示词与窗口里都用它）。

    data 需要（缺的键一律显示「—」，绝不编数字）：
      app / data_source / frames / duration_s / avg_fps / max_fps / low1_fps / low01_fps
      dropped / dropped_pct / gpu_busy_pct / spikes_33 / spikes_100
      hw: {cpu_total, cpu0_usage, mem_avail_mb, pagefile_pct, pagein_mb, pageout_mb,
           disk_read_ms, disk_write_ms, disk_queue, dwm_cpu, vram_pct, gpu_clock, gpu_temp}
      swap: {total, kinds, rates, verdicts}
      issues: [(dim, verdict), ...]
      events: [(hh:mm:ss, kind, text), ...]
    """
    d = data or {}
    hw = d.get("hw") or {}
    swap = d.get("swap") or {}
    lines = []

    head = "采集对象：%s ｜ 数据源：%s ｜ 帧数：%s ｜ 时长：%s 秒" % (
        d.get("app") or "未知",
        d.get("data_source") or "未知",
        _num(d.get("frames")),
        _num(d.get("duration_s"), "%.1f"))
    lines.append(head)

    lines.append(
        "帧表现：平均 %s FPS ｜ 最高 %s ｜ 1%% Low %s ｜ 0.1%% Low %s ｜ 顿挫 %s 次(%s%%) ｜ "
        "GPU 忙时比 %s%%" % (
            _num(d.get("avg_fps"), "%.1f"), _num(d.get("max_fps"), "%.1f"),
            _num(d.get("low1_fps"), "%.1f"), _num(d.get("low01_fps"), "%.1f"),
            _num(d.get("dropped")), _num(d.get("dropped_pct"), "%.1f"),
            _num(d.get("gpu_busy_pct"), "%.0f")))

    lines.append(
        "尖峰计数：帧耗时 ≥33ms 共 %s 帧 ｜ ≥100ms 共 %s 帧" % (
            _num(d.get("spikes_33")), _num(d.get("spikes_100"))))

    lines.append(
        "硬件侧（0.4s 采样）：CPU 总占用 %s%% / CPU0 %s%% ｜ 可用内存 %s MB ｜ "
        "页面文件占用 %s%% ｜ 换入 %s / 换出 %s MB/s ｜ 磁盘延迟 读%s/写%s ms（队列 %s）｜ "
        "dwm.exe %s%% ｜ 显存占用 %s%%%s%s" % (
            _num(hw.get("cpu_total")), _num(hw.get("cpu0_usage")),
            _num(hw.get("mem_avail_mb")),
            _num(hw.get("pagefile_pct")),
            _num(hw.get("pagein_mb"), "%.2f"), _num(hw.get("pageout_mb"), "%.2f"),
            _num(hw.get("disk_read_ms"), "%.1f"), _num(hw.get("disk_write_ms"), "%.1f"),
            _num(hw.get("disk_queue"), "%.1f"),
            _num(hw.get("dwm_cpu"), "%.1f"),
            _num(hw.get("vram_pct")),
            (" ｜ GPU 频率 %s MHz" % _num(hw.get("gpu_clock"))) if hw.get("gpu_clock") else "",
            (" ｜ 温度 %s℃" % _num(hw.get("gpu_temp"))) if hw.get("gpu_temp") else ""))

    total = (swap or {}).get("total") or 0
    if total:
        lab = {k: v for k, v in (swap.get("kinds") or {}).items()}
        txt = "、".join("%s %d 次" % (LABELS.get(k, k), v)
                       for k, v in sorted(lab.items(), key=lambda kv: -kv[1])) or "0 次同向"
        lines.append("交换证据：共 %d 个尖峰，其中 %s（说明：0 次同向即「已排除内存交换」）"
                     % (total, txt))
    else:
        lines.append("交换证据：本次会话还没有尖峰帧，暂无会话级统计")

    issues = d.get("issues") or []
    if issues:
        lines.append("归因表命中项（按严重度排序，原文照抄，含 🟠/🔴 标记）：")
        for dim, verdict in issues:
            lines.append("  · %s：%s" % (dim, verdict))
    else:
        lines.append("归因表命中项：无（12 维全部正常或不可判定）")

    events = d.get("events") or []
    if events:
        lines.append("引擎告警事件（最近 %d 条）：" % len(events))
        for ts, kind, text in events:
            lines.append("  · [%s] %s：%s" % (ts, kind, text))
    return lines


LABELS = {"swap": "内存交换(磁盘)", "disk": "磁盘排队", "dwm": "桌面合成 DWM",
          "cpu": "CPU 调度抢占", "none": "链路平稳", "unknown": "不可判定"}


# ==============================================================================
# 3) 提示词
# ==============================================================================
def build_prompt(data, csv_path=""):
    """
    生成可一键复制的「问 AI」提示词。

    结构 = 角色 + 数据事实 + 逐帧文件说明 + 输出格式要求。
    末尾再补一句「如果你需要，我可以贴 CSV 片段」—— 引导用户把关键行也贴进去。
    """
    lines = [ROLE_TEXT, "", "===== 我的体检数据 =====", ""]
    lines.extend(summary_lines(data))
    lines.append("")
    if csv_path:
        lines.append("逐帧原始数据已导出为 CSV：")
        lines.append("  %s" % csv_path)
        lines.append("（列含 WallClock 绝对时间戳、MsBetweenPresents、PageFile_Pct、"
                     "PagesIn_MBps、DiskReadLatency_ms、DiskQueue、DwmCPU_Pct、SwapVerdict 等；"
                     "如果你能读文件，请按 WallClock 对齐分析尖峰前后的计数器变化。）")
    lines.append("")
    lines.append("===== 回答格式 =====")
    lines.append(OUTPUT_RULES)
    lines.append("")
    lines.append("补充说明：我主要关心的场景是「窗口化/无边框模式出现大于 100ms 的卡顿，"
                 "全屏时明显减轻」。如果你的判断需要更多数据，请直接列出还需要我采集哪几项。")
    return "\n".join(lines)


def short_sentences(data, csv_path=""):
    """
    窗口里显示的「一句话版本」（用户想直接粘贴到聊天框时用）。

    只保留最关键的 3~4 句，避免一段话太长被聊天框折叠。
    """
    d = data or {}
    hw = d.get("hw") or {}
    swap = d.get("swap") or {}
    bits = []
    bits.append("我这边《%s》采集到平均 %s FPS、1%% Low %s FPS，帧耗时 ≥100ms 有 %s 帧。"
                % (d.get("app") or "游戏", _num(d.get("avg_fps"), "%.1f"),
                   _num(d.get("low1_fps"), "%.1f"), _num(d.get("spikes_100"))))
    bits.append("系统侧：CPU %s%%、可用内存 %s MB、页面文件 %s%%、磁盘延迟 %s ms、dwm %s%%。"
                % (_num(hw.get("cpu_total")), _num(hw.get("mem_avail_mb")),
                   _num(hw.get("pagefile_pct")), _num(hw.get("disk_read_ms"), "%.1f"),
                   _num(hw.get("dwm_cpu"), "%.1f")))
    if (swap or {}).get("total"):
        kinds = swap.get("kinds") or {}
        if kinds:
            top = sorted(kinds.items(), key=lambda kv: -kv[1])[0]
            bits.append("尖峰与「%s」同向 %d/%d 次。"
                        % (LABELS.get(top[0], top[0]), top[1], swap["total"]))
        else:
            bits.append("尖峰与内存交换/磁盘排队/DWM 均不同向（交换链路可排除）。")
    if csv_path:
        bits.append("逐帧 CSV 已导出到 %s。" % csv_path)
    bits.append("请以 Windows 游戏性能专家身份，按证据强度给出根因排序和可执行的修复步骤，"
                "并指出还需要补充什么数据。")
    return "".join(bits)
