# 参与贡献

欢迎提交 Issue 与 Pull Request。

## 提交 Issue 前请准备

- 操作系统版本（`winver` 输出）、显卡型号与驱动版本
- StutterTriage 版本（标题栏，或 `stuttertriage/__init__.py` 里的 `__version__`）
- 是否以管理员身份运行
- `%USERPROFILE%\.stuttertriage\stuttertriage_diag.log` 的相关片段（该日志只记本地诊断，不含个人信息）

## 开发约定

以下约定来自项目踩过的坑，请不要轻易改动。

1. **UI 层不得改动采集与归因语义。** 归因算法可以改，但「何时调用、调用频率」必须留证据再改。
2. **主题色不得写死十六进制。** 一律映射到主题角色名（`bad` / `warn` / `green` / `dim`），在访问时解析。浅色主题的语义色对白底对比度需 ≥ 4.5:1。
3. **配置保存必须广播。** `Config.apply_to_globals()` 不带参数，让它广播到所有已注册模块；传 `globals()` 只会更新本模块副本，表现为「看着保存了但要重启才生效」。
4. **高频路径必须节流。** 30Hz 路径上做 O(会话) 计算会淹没 UI 定时器。节流常量写在 `main_window` 的类属性里，便于集中调参。
5. **ctypes 回调分配的内存，生命周期由驱动接管。** AMD ADL 要用驱动自己的 `free()`，不能把 Python 缓冲区的裸地址交出去，否则会 use-after-free，表现为随机闪退。
6. **退出必须收干净。** 顶层窗口要全部关掉（否则 Qt 不退出），子进程用 Job Object 兜底，重复 `deleteLater()` 会让 PyQt6 在解释器收尾时 fastfail。

## 跑测试

提交前请跑一遍离线回归（本地与 CI 是同一条命令，见 `scripts/test.ps1`）：

```powershell
python -m pip install -r requirements-lock.txt   # 与 CI/发布构建一致的版本
pwsh -File scripts\test.ps1
```

该入口会逐个打印结果，并在最后汇总「通过 / 失败 / 跳过」，以首个失败脚本的退出码结束。
需要真实 ETW、管理员权限或真实桌面的用例单独成组，按需运行：

```powershell
pwsh -File scripts\test.ps1 -Suite integration   # 真实 ETW 会话清理 / 生命周期
pwsh -File scripts\test.ps1 -Suite preview       # 渲染 docs/preview_*.png（真实桌面）
```

`tests/render_preview.py` 必须用 `QT_QPA_PLATFORM=windows` 运行（offscreen 渲染不出中文字体），
脚本已自动设置。各脚本的用途与套件划分见 [开发与打包指南](docs/DEVELOPMENT.md)。

## 许可

提交的代码将按项目的 [MIT 许可](LICENSE) 发布。
