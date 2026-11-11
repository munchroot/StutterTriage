# -*- coding: utf-8 -*-
"""发布前版本一致性校验（tag / __version__ / PE 资源 / CHANGELOG / README 徽章）。

为什么要有它：版本号在本项目里出现 5 处，任何一处漏改都会让用户看到
「Release 写 1.1.3、EXE 属性写 1.1.2」这种自相矛盾的产物。以前靠人工核对，
现在由 `build_exe.ps1 -Release` 和 release workflow 强制校验。

用法：
    python build_tools/check_version.py --tag v1.1.2
    python build_tools/check_version.py --tag v1.1.2 --exe dist/StutterTriage.exe

退出码：0 全部一致；1 存在不一致；2 读取失败（文件缺失/格式无法解析）。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:  # 控制台默认可能是 GBK：中文检查项会乱码，统一按 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FAILS = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("OK  " if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def read_text(rel):
    path = os.path.join(ROOT, rel)
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def source_version():
    m = re.search(r'^__version__\s*=\s*["\']([0-9]+\.[0-9]+\.[0-9]+)["\']',
                  read_text("stuttertriage/__init__.py"), re.M)
    return m.group(1) if m else ""


def version_info_versions():
    """返回 (filevers 元组, FileVersion 字符串, ProductVersion 字符串)。"""
    text = read_text("build_tools/version_info.txt")
    m = re.search(r"filevers\s*=\s*\(([^)]*)\)", text)
    tup = tuple(int(x.strip()) for x in m.group(1).split(",")) if m else ()
    fv = re.search(r'StringStruct\(\s*"FileVersion"\s*,\s*"([^"]+)"', text)
    pv = re.search(r'StringStruct\(\s*"ProductVersion"\s*,\s*"([^"]+)"', text)
    return tup, (fv.group(1) if fv else ""), (pv.group(1) if pv else "")


def changelog_version():
    m = re.search(r"^##\s*v([0-9]+\.[0-9]+\.[0-9]+)", read_text("CHANGELOG.md"), re.M)
    return m.group(1) if m else ""


def readme_badge_version():
    m = re.search(r"badge/version-([0-9]+\.[0-9]+\.[0-9]+)-", read_text("README.md"))
    return m.group(1) if m else ""


def pe_file_version(path):
    """读 EXE 的 VS_FIXEDFILEINFO 文件版本（(1, 1, 2, 0)）；非 Windows 返回 None。"""
    if os.name != "nt":
        return None
    path = os.path.abspath(path)
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buf):
            return None
        ptr = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(ptr),
                                                    ctypes.byref(length)):
            return None

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", ctypes.c_uint32),
                ("dwStrucVersion", ctypes.c_uint32),
                ("dwFileVersionMS", ctypes.c_uint32),
                ("dwFileVersionLS", ctypes.c_uint32),
                ("dwProductVersionMS", ctypes.c_uint32),
                ("dwProductVersionLS", ctypes.c_uint32),
                ("dwFileFlagsMask", ctypes.c_uint32),
                ("dwFileFlags", ctypes.c_uint32),
                ("dwFileOS", ctypes.c_uint32),
                ("dwFileType", ctypes.c_uint32),
                ("dwFileSubtype", ctypes.c_uint32),
                ("dwFileDateMS", ctypes.c_uint32),
                ("dwFileDateLS", ctypes.c_uint32),
            ]

        info = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        return (info.dwFileVersionMS >> 16, info.dwFileVersionMS & 0xFFFF,
                info.dwFileVersionLS >> 16, info.dwFileVersionLS & 0xFFFF)
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="StutterTriage 发布前版本一致性校验")
    ap.add_argument("--tag", default="", help="发布 tag，如 v1.1.2；缺省时只校验仓库内部一致性")
    ap.add_argument("--exe", default="", help="可选的 EXE 路径，校验 PE 文件版本")
    args = ap.parse_args(argv)

    try:
        src = source_version()
        tup, fv, pv = version_info_versions()
        change = changelog_version()
        badge = readme_badge_version()
    except Exception as ex:
        print("读取版本来源失败：%s" % ex)
        return 2

    if not src:
        print("无法从 stuttertriage/__init__.py 解析 __version__")
        return 2

    tag_ver = args.tag.strip()
    if tag_ver.lower().startswith("v"):
        tag_ver = tag_ver[1:]

    print("== 版本一致性 ==")
    check("__version__ 可解析", bool(src), src)
    if tag_ver:
        check("tag 与 __version__ 一致", tag_ver == src, "tag=%s __version__=%s" % (tag_ver, src))
    check("version_info.txt 与 __version__ 一致",
          tup[:3] == tuple(int(x) for x in src.split(".")) if len(tup) >= 3 else False,
          "filevers=%s" % (tup,))
    check("FileVersion 字符串一致",
          fv == src + ".0", "FileVersion=%s" % fv)
    check("ProductVersion 字符串一致",
          pv == src + ".0", "ProductVersion=%s" % pv)
    check("CHANGELOG 最新条目与 __version__ 一致",
          change == src, "CHANGELOG=%s" % change)
    check("README 徽章与 __version__ 一致",
          badge == src, "README=%s" % badge)

    if args.exe:
        if not os.path.isfile(args.exe):
            print("找不到 EXE：%s" % args.exe)
            return 2
        pe = pe_file_version(args.exe)
        check("EXE 的 PE 文件版本一致",
              pe is not None and pe[:3] == tuple(int(x) for x in src.split(".")),
              "PE=%s" % (pe,))

    print()
    if FAILS:
        print("版本不一致：%s" % ", ".join(FAILS))
        print("请同步修改 __version__ / version_info.txt / CHANGELOG.md / README 徽章后重试。")
        return 1
    print("版本一致：%s" % src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
