param(
    [Parameter(Mandatory = $true)][string]$ImagePath,
    [Parameter(Mandatory = $true)][string]$OutPath,
    [Parameter(Mandatory = $true)][string]$ErrPath,
    [double]$Gap = 18.0,
    # 识别前放大倍数（1 = 不放大）。小字截图放大后准确率明显更高。
    # [接手 2026-11-10] 放大改由本脚本用 System.Drawing 完成：Python 侧原来 import PIL，
    # 而容器/纯净运行时没有 Pillow，也违反「src 纯 stdlib」契约（smoke.py case_stdlib_only）。
    [double]$Scale = 1.0
)
$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

    function Await($WinRtTask, $ResultType) {
        $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
        $netTask = $asTask.Invoke($null, @($WinRtTask))
        $netTask.Wait(-1) | Out-Null
        $netTask.Result
    }

    [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapPixelFormat, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapAlphaMode, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
    [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime] | Out-Null
    [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] | Out-Null

    # 需要放大时先落一张放大图，再喂给 OCR（不依赖任何第三方 Python 库）。
    if ($Scale -gt 1.0) {
        Add-Type -AssemblyName System.Drawing | Out-Null
        $scaled = [System.IO.Path]::ChangeExtension($OutPath, '.scaled.png')
        $src = [System.Drawing.Bitmap]::FromFile((Resolve-Path -LiteralPath $ImagePath).Path)
        try {
            $w = [int][Math]::Max(1, [Math]::Round($src.Width * $Scale))
            $h = [int][Math]::Max(1, [Math]::Round($src.Height * $Scale))
            $big = New-Object System.Drawing.Bitmap($w, $h)
            try {
                $g = [System.Drawing.Graphics]::FromImage($big)
                try {
                    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
                    $g.DrawImage($src, 0, 0, $w, $h)
                } finally { $g.Dispose() }
                $big.Save($scaled, [System.Drawing.Imaging.ImageFormat]::Png)
            } finally { $big.Dispose() }
            $ImagePath = $scaled
        } finally { $src.Dispose() }
    }

    $fs = [System.IO.File]::OpenRead($ImagePath)
    $ras = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($fs)
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync(
        [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])

    $engine = $null
    try { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() } catch { }
    if ($null -eq $engine) {
        $lang = [Windows.Globalization.Language]::new('zh-Hans-CN')
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
    }
    if ($null -eq $engine) { throw 'no OCR engine available' }

    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

    # 按词间距合并：同一标识符内被误插的空格（间距小）拼回去，真正分隔的（间距大）断开
    $tokens = New-Object System.Collections.Generic.List[string]
    foreach ($line in $result.Lines) {
        $cur = ''
        $prevRight = $null
        foreach ($word in $line.Words) {
            $r = $word.BoundingRect
            $left = [double]$r.X
            $right = [double]($r.X + $r.Width)
            if ($null -ne $prevRight -and ($left - $prevRight) -lt $Gap) {
                $cur = $cur + $word.Text
            }
            else {
                if ($cur -ne '') { $tokens.Add($cur) }
                $cur = $word.Text
            }
            $prevRight = $right
        }
        if ($cur -ne '') { $tokens.Add($cur) }
    }

    [System.IO.File]::WriteAllLines($OutPath, $tokens, (New-Object System.Text.UTF8Encoding($false)))
}
catch {
    $ex = $_.Exception
    while ($null -ne $ex.InnerException) { $ex = $ex.InnerException }
    [System.IO.File]::WriteAllText($ErrPath, $ex.Message, (New-Object System.Text.UTF8Encoding($false)))
    exit 1
}