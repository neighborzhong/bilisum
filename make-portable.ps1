# 生成两个分发版：
#   完整版 release\bilisum-full   自带 Ollama 运行时 + 模型，解压双击就能用（约 8.8 GB）
#   精简版 release\bilisum-lite   不自带 Ollama，调用你电脑上已装的 Ollama（约 1.7 GB）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File make-portable.ps1            # 只出完整版
#   powershell -ExecutionPolicy Bypass -File make-portable.ps1 -Lite      # 只出精简版
#   powershell -ExecutionPolicy Bypass -File make-portable.ps1 -Both      # 两个都出
param(
    [switch]$Lite,
    [switch]$Both,
    [string]$OutDir = ''
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$src      = Join-Path $PSScriptRoot 'dist\bilisum'
$ollamaRt = Join-Path $env:LOCALAPPDATA 'Programs\Ollama'
$ollamaMd = $env:OLLAMA_MODELS
if (-not $ollamaMd) { $ollamaMd = Join-Path $env:USERPROFILE '.ollama\models' }
$whisper  = Join-Path $PSScriptRoot 'data\models'

if (-not (Test-Path (Join-Path $src 'bilisum.exe'))) {
    throw "找不到 dist\bilisum\bilisum.exe，请先运行 build.ps1"
}
if ($Both) { $Lite = $false }

function Build-Package {
    param([bool]$WithOllama, [string]$Target)

    Write-Host ""
    Write-Host "======== 生成 $(if ($WithOllama) {'完整版'} else {'精简版'}) ========" -ForegroundColor Magenta
    Write-Host "输出：$Target"

    Remove-Item -Recurse -Force $Target -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $Target | Out-Null

    Write-Host "[1/5] 程序文件" -ForegroundColor Cyan
    robocopy $src $Target /E /XD data /NFL /NDL /NJH /NJS /NP | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $Target 'data\models') | Out-Null

    Write-Host "[2/5] whisper 语音识别模型" -ForegroundColor Cyan
    if (Test-Path $whisper) {
        robocopy $whisper (Join-Path $Target 'data\models') /E /NFL /NDL /NJH /NJS /NP | Out-Null
        Write-Host ("      {0:N0} MB" -f ((Get-ChildItem (Join-Path $Target 'data\models') -Recurse -File | Measure-Object Length -Sum).Sum/1MB))
    } else {
        Write-Host "      跳过（对方首次跑语音识别时会自动下载）" -ForegroundColor Yellow
    }

    if ($WithOllama) {
        Write-Host "[3/5] Ollama 运行时（排除卸载器/GUI/WebView2）" -ForegroundColor Cyan
        if (-not (Test-Path $ollamaRt)) { throw "找不到 Ollama 安装目录：$ollamaRt" }
        $dstRt = Join-Path $Target 'runtime\ollama'
        New-Item -ItemType Directory -Force -Path $dstRt | Out-Null
        robocopy $ollamaRt $dstRt /E /XF 'unins*' 'ollama app.exe' /XD 'ollama app.exe.WebView2' /NFL /NDL /NJH /NJS /NP | Out-Null
        Write-Host ("      {0:N0} MB" -f ((Get-ChildItem $dstRt -Recurse -File | Measure-Object Length -Sum).Sum/1MB))

        Write-Host "[4/5] Ollama 模型 qwen2.5:7b-instruct" -ForegroundColor Cyan
        if (-not (Test-Path $ollamaMd)) { throw "找不到 Ollama 模型目录：$ollamaMd" }
        robocopy $ollamaMd (Join-Path $Target 'runtime\models') /E /NFL /NDL /NJH /NJS /NP | Out-Null
        Write-Host ("      {0:N0} MB" -f ((Get-ChildItem (Join-Path $Target 'runtime\models') -Recurse -File | Measure-Object Length -Sum).Sum/1MB))
    } else {
        Write-Host "[3/5][4/5] 跳过 Ollama（精简版，调用你电脑上已装的）" -ForegroundColor Yellow
    }

    Write-Host "[5/5] 使用说明 + 清理敏感数据" -ForegroundColor Cyan

    if ($WithOllama) {
        $readme = @'
bilisum 完整版 —— 把 B 站视频整理成学习笔记
============================================

【这个版本不需要你装任何东西】
自带 Ollama 运行时 + qwen2.5:7b 模型 + whisper 语音识别模型。
解压后双击 bilisum.exe 就能用。


【第一步：解压】
解压到一个路径不含中文和空格的目录，比如 D:\bilisum
（不要放在桌面或「新建文件夹」里，路径别带中文）


【第二步：双击 bilisum.exe】
会出现一个窗口。第一次启动要等 10-20 秒（在加载本地模型），
期间双击第二次是没用的，耐心等一下。


【第三步：配置 SESSDATA（强烈建议）】
不配也能用，但会走本地语音识别，慢很多，专有名词也容易错。
配了之后，有字幕的视频几秒钟就出结果。

1. 浏览器登录 B 站
2. 按 F12 → 「应用 / Application」→ 左侧「存储 → Cookie → https://www.bilibili.com」
3. 找到 SESSDATA 这一行，复制它的值
4. 在 bilisum 窗口点「设置（SESSDATA / 模型）」，粘贴，保存

顶部状态灯变绿即生效。Cookie 一般几个月过期，过期后重贴即可。
（找不到的话可以整段复制浏览器 Cookie 粘贴，会自动提取。）


【怎么用】
把 B 站视频链接或 BV 号粘进输入框，点「总结」。
支持 BVxxx p79 这种指定分 P 的写法。
勾上「同时提取画面文字」能抓到 PPT / 代码里的内容（会多花几十秒）。


【重要提示】
1. 杀毒软件可能误报并拦截 bilisum.exe（打包程序的常见现象）。
   在弹窗里选「仍要运行」，或把整个文件夹加进白名单。
2. 笔记保存在 data\notes，出问题看 data\app.log
3. 关掉窗口 = 程序和相关服务全部退出，不留后台
4. 完全绿色，不写注册表，卸载直接删文件夹
5. 需要 Windows 10/11（依赖系统自带 WebView2，一般都有）
6. 有独立显卡（NVIDIA）会快很多；没有的话也能跑，只是慢
'@
    } else {
        $readme = @'
bilisum 精简版 —— 把 B 站视频整理成学习笔记
============================================

【这个版本需要你先装 Ollama】
它本身不含 Ollama 和模型（所以只有 1.7 GB）。
请按下面三步准备，只需做一次。


【第一步：安装 Ollama】
打开 https://ollama.com/download 下载 Windows 版，双击安装。
安装完它会在后台自动运行（右下角托盘能看到）。


【第二步：下载模型】
按 Win+R 输入 cmd 回车，打开命令提示符，执行：

    ollama pull qwen2.5:7b-instruct

约需下载 4.7 GB，看网速可能十几分钟。等它跑完（出现 success）。


【第三步：解压并运行】
把本压缩包解压到路径不含中文和空格的目录（比如 D:\bilisum），
双击 bilisum.exe。

如果提示「检测不到 Ollama」，说明第一步或第二步没做好，
或者装完没重启电脑，按提示再来一次即可。


【配置 SESSDATA（强烈建议）】
不配也能用，但会走本地语音识别，慢且专有名词容易错。

1. 浏览器登录 B 站
2. 按 F12 → 「应用 / Application」→ 左侧「存储 → Cookie → https://www.bilibili.com」
3. 找到 SESSDATA，复制它的值
4. 在 bilisum 窗口点「设置（SESSDATA / 模型）」，粘贴，保存


【怎么用】
把 B 站视频链接或 BV 号粘进输入框，点「总结」。
支持 BVxxx p79 这种指定分 P 的写法。


【重要提示】
1. 杀毒软件可能误报（打包程序常见现象），选「仍要运行」或加白名单
2. 你自己电脑上已有的 Ollama 模型不会被动到，程序只是读用
3. 笔记保存在 data\notes，出问题看 data\app.log
4. 需要 Windows 10/11（依赖系统自带 WebView2）
5. 有独立显卡（NVIDIA）会快很多
'@
    }

    $readmeName = if ($WithOllama) { '使用说明-完整版.txt' } else { '使用说明-精简版.txt' }
    [System.IO.File]::WriteAllText(
        (Join-Path $Target $readmeName),
        $readme,
        (New-Object System.Text.UTF8Encoding($true))
    )

    # 分发前必须清掉属于作者本人的东西
    Remove-Item (Join-Path $Target 'data\settings.json') -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $Target 'data\app.log') -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $Target 'data\notes\*') -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $Target 'data\tmp\*') -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $Target 'data\hf') -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $Target 'bilisum.exe.WebView2') -Recurse -Force -ErrorAction SilentlyContinue
    foreach ($sub in 'notes', 'tmp') {
        New-Item -ItemType Directory -Force -Path (Join-Path $Target "data\$sub") | Out-Null
    }

    $sensitive = @(Get-ChildItem (Join-Path $Target 'data') -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq 'settings.json' -or $_.Name -eq 'app.log' })
    if ($sensitive.Count -gt 0) {
        Write-Host "      [!] 仍有敏感文件：" -ForegroundColor Red
        $sensitive | ForEach-Object { Write-Host "          $($_.FullName)" }
    } else {
        Write-Host "      [OK] 已清空 SESSDATA / 日志 / 笔记 / 缓存" -ForegroundColor Green
    }

    $size = (Get-ChildItem $Target -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
    Write-Host ("      完成：{0:N0} MB ({1:N1} GB)" -f $size, ($size/1024)) -ForegroundColor Green
}

if ($Both) {
    Build-Package -WithOllama $true  -Target (Join-Path $PSScriptRoot 'release\bilisum-full')
    Build-Package -WithOllama $false -Target (Join-Path $PSScriptRoot 'release\bilisum-lite')
} elseif ($Lite) {
    Build-Package -WithOllama $false -Target $(if ($OutDir) { $OutDir } else { Join-Path $PSScriptRoot 'release\bilisum-lite' })
} else {
    Build-Package -WithOllama $true  -Target $(if ($OutDir) { $OutDir } else { Join-Path $PSScriptRoot 'release\bilisum-full' })
}

Write-Host ""
Write-Host "全部完成。" -ForegroundColor Green
Get-ChildItem (Join-Path $PSScriptRoot 'release') -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host ("  {0,-16} {1:N2} GB" -f $_.Name, ((Get-ChildItem $_.FullName -Recurse -File | Measure-Object Length -Sum).Sum/1GB))
}
