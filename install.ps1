# 把打包好的 app 部署到本机并更新桌面快捷方式
# 用法：powershell -ExecutionPolicy Bypass -File install.ps1 [-Dest <安装目录>]
#   -Dest 不填时默认装到 <用户目录>\bilisum-app
param([string]$Dest = '')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$src = Join-Path $PSScriptRoot 'dist\bilisum'
if (-not $Dest) { $Dest = Join-Path $env:USERPROFILE 'bilisum-app' }
$dst = $Dest

if (-not (Test-Path (Join-Path $src 'bilisum.exe'))) {
    throw "找不到 $src\bilisum.exe，请先运行 build.ps1"
}

Write-Host "== 1/3 停止正在运行的实例 ==" -ForegroundColor Cyan
Get-Process bilisum -ErrorAction SilentlyContinue | ForEach-Object {
    $_.CloseMainWindow() | Out-Null
    Start-Sleep -Milliseconds 800
    if (-not $_.HasExited) { Stop-Process -Id $_.Id -Force }
    Write-Host "  已停止 pid=$($_.Id)"
}

Write-Host "== 2/3 同步到 $dst ==" -ForegroundColor Cyan
# data 目录（配置、模型、笔记）不删，只覆盖程序文件
New-Item -ItemType Directory -Force -Path $dst | Out-Null
robocopy $src $dst /E /XD data /NFL /NDL /NJH /NJS /NP | Out-Null
if (-not (Test-Path (Join-Path $dst 'data'))) { New-Item -ItemType Directory -Force -Path (Join-Path $dst 'data') | Out-Null }
foreach ($sub in 'models', 'notes', 'tmp') {
    New-Item -ItemType Directory -Force -Path (Join-Path $dst "data\$sub") | Out-Null
}
Write-Host ("  程序文件 {0:N0} MB" -f ((Get-ChildItem $dst -Recurse -File -Exclude *.exe | Where-Object { $_.FullName -notlike "*\data\*" } | Measure-Object Length -Sum).Sum / 1MB))

# 放一份「真正会被读到」的配置在 exe 旁边（_internal 里那份程序不读，容易误导）
$cfgDst = Join-Path $dst 'config.toml'
$cfgTpl = Join-Path $PSScriptRoot 'config.example.toml'
if (-not (Test-Path $cfgDst) -and (Test-Path $cfgTpl)) {
    Copy-Item $cfgTpl $cfgDst
    Write-Host "  已生成默认配置：$cfgDst（改这里才生效）"
} elseif (Test-Path $cfgDst) {
    Write-Host "  保留已有配置：$cfgDst"
}

Write-Host "== 3/3 桌面快捷方式 ==" -ForegroundColor Cyan
$desktop = [Environment]::GetFolderPath('Desktop')
$lnkPath = Join-Path $desktop 'B站学习笔记.lnk'
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = Join-Path $dst 'bilisum.exe'
$lnk.WorkingDirectory = $dst
$lnk.Description = 'bilisum - 把 B 站视频整理成学习笔记'
$lnk.IconLocation = (Join-Path $dst 'bilisum.exe') + ',0'
$lnk.Save()
Write-Host "  $lnkPath"

# 旧的 pythonw 版快捷方式改个名，避免和新 app 混淆
$old = Join-Path $desktop 'B站视频总结.lnk'
if (Test-Path $old) {
    Rename-Item $old 'B站视频总结(旧-开发版).lnk' -ErrorAction SilentlyContinue
    Write-Host "  旧快捷方式已改名为「B站视频总结(旧-开发版)」"
}

Write-Host "`n完成。双击桌面「B站学习笔记」即可启动。" -ForegroundColor Green
Write-Host "注意：首次使用需要在设置里重新粘贴 SESSDATA（app 的配置存在 $dst\data\settings.json）"
