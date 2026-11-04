# 打包 bilisum 成桌面应用（onedir 模式：启动快、出错易排查）
# 用法：powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host "== 1/2 清理旧产物 ==" -ForegroundColor Cyan
Remove-Item -Recurse -Force 'build', 'dist\bilisum' -ErrorAction SilentlyContinue

Write-Host "== 2/2 PyInstaller 打包 ==" -ForegroundColor Cyan
& .\.venv\Scripts\pyinstaller.exe `
    --noconfirm `
    --clean `
    --windowed `
    --name bilisum `
    --add-data "static;static" `
    --add-data "tools/win_ocr_batch.ps1;tools" `
    --add-data "config.toml;." `
    --collect-submodules uvicorn `
    --collect-submodules fastapi `
    --collect-data faster_whisper `
    --hidden-import ctranslate2 `
    --hidden-import av `
    --hidden-import onnxruntime `
    --hidden-import webview.platforms.edgechromium `
    --hidden-import clr `
    app.py

if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

Write-Host "`n== 产物 ==" -ForegroundColor Green
$exe = 'dist\bilisum\bilisum.exe'
if (Test-Path $exe) {
    $size = (Get-ChildItem 'dist\bilisum' -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
    Write-Host ("{0}  所在目录共 {1:N0} MB" -f $exe, $size)
} else {
    throw "没找到 $exe"
}
