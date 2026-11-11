#Requires -Version 7.0
<#
.SYNOPSIS
    StutterTriage 统一测试入口 —— 本地和 CI 共用同一条命令。

.DESCRIPTION
    按套件运行 tests/*.py，逐个打印结果，最后汇总「通过 / 失败 / 跳过 / 超时」，
    并以**首个失败脚本的退出码**结束（CI 据此判红）。

    套件划分：
      core        普通 runner 可跑的离线回归（纯逻辑 / 静态不变量 / offscreen 冒烟）。
                  默认套件，CI 只跑它。
      integration 需要真实 ETW、管理员权限或真实进程的用例（清理残留会话、Job 兜底）。
                  这些结果单独报告，失败不会挡住普通提交。
      preview     渲染 docs/preview_*.png，需要真实 Windows 桌面（offscreen 没有中文字体）。
      all         三者全部。

.PARAMETER Suite
    套件名：core / integration / preview / all，默认 core。

.PARAMETER Python
    解释器路径。默认取环境变量 STUTTERTRIAGE_PYTHON，未设置时用 PATH 里的 python。
    例：-Python F:\Tools\_build_env\stuttertriage_venv\Scripts\python.exe

.PARAMETER Filter
    只跑文件名匹配的脚本（通配符），例：-Filter test_swap*

.PARAMETER TimeoutSec
    单个脚本的超时秒数，默认 300。超时按失败处理（退出码 124）。

.PARAMETER LogDir
    日志目录。默认 %TEMP%\stuttertriage-tests\<时间戳>，失败时打印路径。

.PARAMETER List
    只列出该套件会跑哪些脚本，不执行。

.EXAMPLE
    pwsh -File scripts\test.ps1

.EXAMPLE
    pwsh -File scripts\test.ps1 -Suite integration

.EXAMPLE
    pwsh -File scripts\test.ps1 -Filter test_swap* -Python F:\Tools\_build_env\stuttertriage_venv\Scripts\python.exe
#>
[CmdletBinding()]
param(
    [ValidateSet('core', 'integration', 'preview', 'all')]
    [string]$Suite = 'core',
    [string]$Python = '',
    [string]$Filter = '',
    [int]$TimeoutSec = 300,
    [string]$LogDir = '',
    [switch]$List
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$TestsDir = Join-Path $Root 'tests'

if (-not $Python) {
    $Python = if ($env:STUTTERTRIAGE_PYTHON) { $env:STUTTERTRIAGE_PYTHON } else { 'python' }
}

# 套件分类：只按文件名划分，测试脚本本身不用移动目录（移动会破坏各自算出的 ROOT）。
$IntegrationOnly = @('test_lifecycle.py')
$PreviewOnly = @('render_preview.py')

$All = @(Get-ChildItem -Path $TestsDir -Filter '*.py' -File |
    Where-Object { $_.Name -notlike '_*' } | Sort-Object Name)

function Select-Suite {
    param([string]$Name)
    switch ($Name) {
        'core' { $All | Where-Object { $IntegrationOnly -notcontains $_.Name -and $PreviewOnly -notcontains $_.Name } }
        'integration' { $All | Where-Object { $IntegrationOnly -contains $_.Name } }
        'preview' { $All | Where-Object { $PreviewOnly -contains $_.Name } }
        'all' { $All }
    }
}

$Files = @(Select-Suite -Name $Suite)
if ($Filter) {
    $Files = @($Files | Where-Object { $_.Name -like $Filter })
}
if ($Files.Count -eq 0) {
    Write-Host "没有匹配的测试脚本（Suite=$Suite Filter=$Filter）" -ForegroundColor Yellow
    exit 2
}

if ($List) {
    $Files | ForEach-Object { $_.Name }
    exit 0
}

if (-not $LogDir) {
    $LogDir = Join-Path ([IO.Path]::GetTempPath()) ('stuttertriage-tests\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# offscreen：无窗口、CI 可跑；preview 必须真实桌面（否则预览图里中文全是方框）。
if ($Suite -eq 'preview') {
    if ($env:QT_QPA_PLATFORM -and $env:QT_QPA_PLATFORM -ne 'windows') {
        Write-Host "  提示：preview 需要真实桌面，已把 QT_QPA_PLATFORM=$($env:QT_QPA_PLATFORM) 覆盖为 windows" -ForegroundColor Yellow
    }
    $env:QT_QPA_PLATFORM = 'windows'
} elseif (-not $env:QT_QPA_PLATFORM) {
    $env:QT_QPA_PLATFORM = 'offscreen'
}
$env:PYTHONIOENCODING = 'utf-8'

Write-Host ''
Write-Host '== StutterTriage 测试 ==' -ForegroundColor Cyan
Write-Host "  套件    : $Suite（$($Files.Count) 个脚本）"
Write-Host "  Python  : $Python"
Write-Host "  平台    : $($env:QT_QPA_PLATFORM)"
Write-Host "  日志    : $LogDir"
Write-Host ''

$Results = New-Object System.Collections.Generic.List[object]
$FirstFailureCode = 0

foreach ($f in $Files) {
    $Name = $f.BaseName
    $OutFile = Join-Path $LogDir ($Name + '.out.log')
    $ErrFile = Join-Path $LogDir ($Name + '.err.log')
    Write-Host ("→ {0}" -f $f.Name) -ForegroundColor Cyan

    $sw = [Diagnostics.Stopwatch]::StartNew()
    $ExitCode = 1
    $TimedOut = $false
    try {
        $proc = Start-Process -FilePath $Python `
            -ArgumentList @(('"{0}"' -f $f.FullName)) `
            -WorkingDirectory $Root -NoNewWindow -PassThru `
            -RedirectStandardOutput $OutFile -RedirectStandardError $ErrFile
        if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
            $TimedOut = $true
            $ExitCode = 124
            try { $proc.Kill($true) } catch { }
            try { $proc.WaitForExit(5000) | Out-Null } catch { }
        } else {
            $ExitCode = $proc.ExitCode
        }
    } catch {
        $ExitCode = 1
        Add-Content -Path $ErrFile -Value ('启动失败: ' + $_.Exception.Message) -Encoding utf8
    }
    $sw.Stop()

    $stdout = [string]$(if (Test-Path $OutFile) { Get-Content -Raw -Encoding utf8 $OutFile } else { '' })
    $stderr = [string]$(if (Test-Path $ErrFile) { Get-Content -Raw -Encoding utf8 $ErrFile } else { '' })

    if ($stdout) {
        foreach ($line in ($stdout.TrimEnd() -split "`r?`n")) { Write-Host ('  ' + $line) }
    }
    if ($stderr -and $stderr.Trim()) {
        foreach ($line in ($stderr.TrimEnd() -split "`r?`n")) { Write-Host ('  ! ' + $line) -ForegroundColor DarkYellow }
    }

    # 兼容两种输出风格：本项目的 [PASS]/[OK] 断言，以及 unittest 的 "... ok" / "OK"。
    # unittest 默认把结果写到 stderr，所以统计要合并两路输出。
    $combined = $stdout + "`n" + $stderr
    $PassCount = ([regex]::Matches($combined, '(?m)\[PASS\]|\[OK\s*\]|\.\.\. ok\s*$|^OK\s*$')).Count
    $FailCount = ([regex]::Matches($combined, '(?m)\[FAIL\]|^FAILED')).Count
    $SkipCount = ([regex]::Matches($combined, '(?m)\[SKIP\]|^\s*SKIP[:：]')).Count
    $Status = if ($TimedOut) { 'TIMEOUT' } elseif ($ExitCode -ne 0) { 'FAIL' } else { 'PASS' }

    if ($Status -ne 'PASS' -and $FirstFailureCode -eq 0) { $FirstFailureCode = $ExitCode }

    $Results.Add([pscustomobject]@{
        Script  = $f.Name
        Status  = $Status
        Seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        Pass    = $PassCount
        Fail    = $FailCount
        Skip    = $SkipCount
    })
    Write-Host ''
}

Write-Host '== 汇总 ==' -ForegroundColor Cyan
foreach ($r in $Results) {
    $color = switch ($r.Status) {
        'PASS'    { 'Green' }
        'TIMEOUT' { 'Yellow' }
        default   { 'Red' }
    }
    Write-Host ("  {0,-30} {1,-8} {2,6:N1}s   通过 {3,3}  失败 {4,2}  跳过 {5,2}" -f `
        $r.Script, $r.Status, $r.Seconds, $r.Pass, $r.Fail, $r.Skip) -ForegroundColor $color
}

$FilesPass = @($Results | Where-Object { $_.Status -eq 'PASS' }).Count
$FilesFail = $Results.Count - $FilesPass
$ChecksPass = ($Results | Measure-Object -Property Pass -Sum).Sum
$ChecksFail = ($Results | Measure-Object -Property Fail -Sum).Sum
$ChecksSkip = ($Results | Measure-Object -Property Skip -Sum).Sum

Write-Host ''
Write-Host ("  脚本：{0} 通过 / {1} 失败；检查项：{2} 通过 / {3} 失败 / {4} 跳过" -f `
    $FilesPass, $FilesFail, $ChecksPass, $ChecksFail, $ChecksSkip) `
    -ForegroundColor $(if ($FilesFail -eq 0) { 'Green' } else { 'Red' })
if ($FilesFail -gt 0) {
    Write-Host "  日志：$LogDir" -ForegroundColor Yellow
    Write-Host '  失败脚本：' -NoNewline -ForegroundColor Yellow
    Write-Host (($Results | Where-Object { $_.Status -ne 'PASS' } | ForEach-Object { $_.Script }) -join ', ') -ForegroundColor Yellow
} else {
    Write-Host '  全部通过。' -ForegroundColor Green
}
Write-Host ''

exit $FirstFailureCode
