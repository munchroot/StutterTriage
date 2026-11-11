# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    把 StutterTriage 打包成单文件 EXE（PyInstaller）。

.DESCRIPTION
    · 构建环境默认放在项目目录**外**：<项目>\..\_build_env\stuttertriage_venv
      可用 -Venv 覆盖，或设环境变量 STUTTERTRIAGE_BUILD_VENV。
    · 环境是**干净的**（不再 --system-site-packages）：依赖一律按 requirements-lock.txt
      安装，本机全局装了什么都影响不到产物 —— 这是「可重复构建」的前提。
    · 产物：<项目>\dist\StutterTriage.exe（单文件，双击即用）
    · PyInstaller 的临时工作目录也在项目外（_build_env\work），
      打包完项目里只多出 dist\StutterTriage.exe，不留一堆 build 垃圾。

    -Release 额外做四件事：
      1. 校验 tag / __version__ / version_info.txt / CHANGELOG / README 徽章一致；
      2. 缺少内置 PresentMon 时直接失败（正式包不能缺件）；
      3. 打包后核对 EXE 的 PE 文件版本；
      4. 输出 dist\SHA256SUMS.txt 供 Release 附件使用。

.EXAMPLE
    pwsh -File build_tools\build_exe.ps1

.EXAMPLE
    pwsh -File build_tools\build_exe.ps1 -RebuildVenv     # 重建构建环境

.EXAMPLE
    pwsh -File build_tools\build_exe.ps1 -Release -Tag v1.1.2
#>
[CmdletBinding()]
param(
    [string]$Venv = "",
    [string]$BasePython = "python",
    [switch]$RebuildVenv,
    [switch]$KeepWork,
    [switch]$Release,
    [string]$Tag = "",
    [switch]$SkipDeps
)

$ErrorActionPreference = 'Stop'
# 从第一行起就统一 UTF-8，避免中文检查项/路径在控制台乱码
$env:PYTHONIOENCODING = 'utf-8'

$Proj = Split-Path -Parent $PSScriptRoot
$Spec = Join-Path $PSScriptRoot 'StutterTriage.spec'
$Dist = Join-Path $Proj 'dist'
$WorkRoot = Join-Path (Split-Path -Parent $Proj) '_build_env'
$Work = Join-Path $WorkRoot 'work'
$Lock = Join-Path $Proj 'requirements-lock.txt'
$PresentMon = Join-Path $Proj 'external\PresentMon\PresentMon-x64.exe'

if (-not $Venv) {
    $Venv = if ($env:STUTTERTRIAGE_BUILD_VENV) { $env:STUTTERTRIAGE_BUILD_VENV }
            else { Join-Path $WorkRoot 'stuttertriage_venv' }
}
if (-not $Tag -and $env:GITHUB_REF_NAME) { $Tag = $env:GITHUB_REF_NAME }

Write-Host "== StutterTriage 打包 ==" -ForegroundColor Cyan
Write-Host "  项目      : $Proj"
Write-Host "  构建环境  : $Venv"
Write-Host "  产物      : $Dist\StutterTriage.exe"
Write-Host "  模式      : $(if ($Release) { "发布（-Release）" } else { "开发" })"
if ($Tag) { Write-Host "  Tag       : $Tag" }
Write-Host ""

# ---- 1. 构建环境 ------------------------------------------------------------
if ($RebuildVenv -and (Test-Path $Venv)) {
    Write-Host "  -> 删除旧构建环境" -ForegroundColor Yellow
    Remove-Item -Recurse -Force $Venv
}
if (-not (Test-Path $Venv)) {
    Write-Host "  -> 创建干净构建环境（不继承系统 site-packages）"
    & $BasePython -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "创建 venv 失败（python=$BasePython）" }
    $SkipDeps = $false
}
$Py = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $Py)) { throw "构建环境缺 python：$Py" }

# ---- 2. 锁定依赖 ------------------------------------------------------------
if ($SkipDeps) {
    Write-Host "  -> 跳过依赖安装（-SkipDeps）" -ForegroundColor Yellow
} else {
    if (-not (Test-Path $Lock)) { throw "缺依赖锁文件：$Lock" }
    Write-Host "  -> 按 requirements-lock.txt 安装依赖（精确版本）"
    & $Py -m pip install --quiet --disable-pip-version-check --upgrade pip
    & $Py -m pip install --quiet --disable-pip-version-check -r $Lock
    if ($LASTEXITCODE -ne 0) { throw "依赖安装失败（exit=$LASTEXITCODE）" }
}
$piVer = ""
try { $piVer = [string](& $Py -m PyInstaller --version 2>$null | Select-Object -First 1) } catch { $piVer = "" }
$piVer = $piVer.Trim()
if (-not $piVer) {
    if ($SkipDeps) { throw "构建环境缺 PyInstaller：$Venv（去掉 -SkipDeps 或用 -RebuildVenv 重建）" }
    Write-Host "  -> 构建环境缺少 PyInstaller，按锁文件补装" -ForegroundColor Yellow
    & $Py -m pip install --quiet --disable-pip-version-check -r $Lock
    if ($LASTEXITCODE -ne 0) { throw "依赖安装失败（exit=$LASTEXITCODE）" }
    $piVer = (& $Py -m PyInstaller --version).Trim()
}
Write-Host "  PyInstaller: $piVer"

# ---- 3. 发布前校验 ----------------------------------------------------------
if ($Release) {
    Write-Host "  -> 校验版本一致性（tag / __version__ / 资源 / CHANGELOG / README）"
    & $Py (Join-Path $PSScriptRoot 'check_version.py') --tag $Tag
    if ($LASTEXITCODE -ne 0) { throw "版本不一致，终止发布构建" }

    if (-not (Test-Path $PresentMon)) {
        throw "发布构建必须内置 PresentMon：$PresentMon 不存在"
    }
    $pmHash = (Get-FileHash -Algorithm SHA256 $PresentMon).Hash.ToLower()
    Write-Host "  PresentMon SHA-256: $pmHash"
}

# 用 UTF-8 输出，避免中文路径/文案在控制台乱码（顶部已设，这里只保留注释）
New-Item -ItemType Directory -Force $Dist, $Work | Out-Null

# ---- 4. 打包 ----------------------------------------------------------------
$t0 = Get-Date
& $Py -m PyInstaller --noconfirm --clean `
    --distpath $Dist `
    --workpath $Work `
    $Spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败（exit=$LASTEXITCODE）" }
$elapsed = [int]((Get-Date) - $t0).TotalSeconds

$exe = Join-Path $Dist 'StutterTriage.exe'
if (-not (Test-Path $exe)) { throw "没有生成 $exe" }
$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)

if (-not $KeepWork) {
    # PyInstaller 的中间产物（几十 MB）用完即删，项目外也不留垃圾
    Remove-Item -Recurse -Force (Join-Path $Work 'StutterTriage') -ErrorAction SilentlyContinue
}

# ---- 5. 产物校验 / 校验值 ---------------------------------------------------
if ($Release) {
    Write-Host "  -> 核对 EXE 的 PE 文件版本"
    & $Py (Join-Path $PSScriptRoot 'check_version.py') --tag $Tag --exe $exe
    if ($LASTEXITCODE -ne 0) { throw "EXE 版本资源与源码版本不一致" }
}

$hash = (Get-FileHash -Algorithm SHA256 $exe).Hash.ToLower()
$sums = Join-Path $Dist 'SHA256SUMS.txt'
"$hash  StutterTriage.exe" | Out-File -FilePath $sums -Encoding ascii
Copy-Item -Force (Join-Path $Proj 'THIRD_PARTY.md') (Join-Path $Dist 'THIRD_PARTY.md')

Write-Host ""
Write-Host "  完成：$exe" -ForegroundColor Green
Write-Host "  体积：$mb MB    耗时：${elapsed}s"
Write-Host "  SHA-256：$hash"
Write-Host "  校验文件：$sums"
Write-Host ""
Write-Host "  提示：exe 是便携式的，配置/日志位置——" -ForegroundColor DarkGray
Write-Host "    · stuttertriage_config.json 会生成在 exe 同目录（把旧的拷过去即可沿用设置）" -ForegroundColor DarkGray
Write-Host "    · 运行日志在 %USERPROFILE%\.stuttertriage\stuttertriage_diag.log" -ForegroundColor DarkGray
Write-Host "    · 需要完整采集（ETW）时：右键 exe → 以管理员身份运行" -ForegroundColor DarkGray
