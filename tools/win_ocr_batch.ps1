# win_ocr_batch.ps1 - OCR every image in a folder with the built-in Windows OCR engine.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File win_ocr_batch.ps1 -Dir <folder> -OutFile <json>
# Notes: this file is pure ASCII on purpose (PowerShell 5.1 reads .ps1 as ANSI/GBK when there is no BOM).
# Output JSON: [{"name":"frame-00060.jpg","text":"..."}]
param(
    [Parameter(Mandatory = $true)][string]$Dir,
    [Parameter(Mandatory = $true)][string]$OutFile,
    [string]$Lang = "zh-Hans-CN"
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

function Await($op, $type) {
    $m = $asTaskGeneric.MakeGenericMethod($type)
    $t = $m.Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}

$OcrEngine = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$LanguageType = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$OcrResultType = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
$BitmapType = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]
$DecoderType = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]

$engine = $null
try { $engine = $OcrEngine::TryCreateFromLanguage($LanguageType::new($Lang)) } catch { }
if (-not $engine) { $engine = $OcrEngine::TryCreateFromUserProfileLanguages() }
if (-not $engine) { throw "no OCR engine available (no recognizer language installed)" }

$results = New-Object System.Collections.ArrayList
$files = Get-ChildItem -LiteralPath $Dir -Filter *.jpg | Sort-Object Name

foreach ($f in $files) {
    $text = ""
    try {
        $full = $f.FullName
        $file = Await ([Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]::GetFileFromPathAsync($full)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
        $decoder = Await ($DecoderType::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ($BitmapType)
        $result = Await ($engine.RecognizeAsync($bitmap)) ($OcrResultType)
        $text = $result.Text
        $stream.Dispose()
    }
    catch {
        $text = ""
    }
    [void]$results.Add([pscustomobject]@{ name = $f.Name; text = $text })
}

$json = $results | ConvertTo-Json -Depth 3 -Compress
[System.IO.File]::WriteAllText($OutFile, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Output ("OCR done: " + $results.Count + " images -> " + $OutFile)
