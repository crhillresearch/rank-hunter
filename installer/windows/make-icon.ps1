[CmdletBinding()]
param(
    [string]$SvgPath = "",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $SvgPath) { $SvgPath = Join-Path $ScriptRoot "RankHunterIcon.svg" }
if (-not $OutputPath) { $OutputPath = Join-Path $ScriptRoot "RankHunter.ico" }

Add-Type -AssemblyName System.Drawing

$svg = [IO.File]::ReadAllText($SvgPath)

$viewBoxMatch = [regex]::Match($svg, 'viewBox="0 0 ([0-9.]+) ([0-9.]+)"')
if (-not $viewBoxMatch.Success) {
    throw "Rank Hunter icon SVG is missing the expected viewBox."
}
$viewWidth = [double]$viewBoxMatch.Groups[1].Value
$viewHeight = [double]$viewBoxMatch.Groups[2].Value

$rectMatch = [regex]::Match($svg, '<rect[^>]+rx="([0-9.]+)"[^>]+fill="(#[0-9A-Fa-f]{6})"')
if (-not $rectMatch.Success) {
    throw "Rank Hunter icon SVG is missing the blue background rectangle."
}
$cornerRadius = [double]$rectMatch.Groups[1].Value
$background = [System.Drawing.ColorTranslator]::FromHtml($rectMatch.Groups[2].Value)

$groupMatch = [regex]::Match(
    $svg,
    '<g[^>]+transform="translate\(([0-9.\-]+)\s+([0-9.\-]+)\)\s+scale\(([0-9.\-]+)\)"[^>]+fill="(#[0-9A-Fa-f]{6})"[^>]+stroke="(#[0-9A-Fa-f]{6})"[^>]+stroke-width="([0-9.]+)"'
)
if (-not $groupMatch.Success) {
    throw "Rank Hunter icon SVG is missing the expected logo transform/style."
}

$translateX = [double]$groupMatch.Groups[1].Value
$translateY = [double]$groupMatch.Groups[2].Value
$logoScale = [double]$groupMatch.Groups[3].Value
$fillColor = [System.Drawing.ColorTranslator]::FromHtml($groupMatch.Groups[4].Value)
$strokeColor = [System.Drawing.ColorTranslator]::FromHtml($groupMatch.Groups[5].Value)
$strokeWidth = [double]$groupMatch.Groups[6].Value

$pathMatches = [regex]::Matches($svg, '<path\s+d="([^"]+)"')
if ($pathMatches.Count -lt 1) {
    throw "Rank Hunter icon SVG contains no logo paths."
}

function New-RoundedRectanglePath(
    [float]$Width,
    [float]$Height,
    [float]$Radius
) {
    $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $diameter = 2 * $Radius
    $path.AddArc(0, 0, $diameter, $diameter, 180, 90)
    $path.AddArc($Width - $diameter, 0, $diameter, $diameter, 270, 90)
    $path.AddArc($Width - $diameter, $Height - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc(0, $Height - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return $path
}

function Convert-SvgPathToGraphicsPath(
    [string]$Data,
    [int]$Size
) {
    # The canonical Rank Hunter SVG mark is intentionally polygonal: each path
    # is one M/L contour. Preserve those exact coordinates instead of redrawing
    # or approximating the logo.
    $numbers = [regex]::Matches($Data, '-?[0-9]+(?:\.[0-9]+)?') |
        ForEach-Object { [double]$_.Value }

    if (($numbers.Count -lt 6) -or (($numbers.Count % 2) -ne 0)) {
        throw "Unexpected Rank Hunter SVG path data."
    }

    $points = [System.Collections.Generic.List[System.Drawing.PointF]]::new()
    $pixelScaleX = $Size / $viewWidth
    $pixelScaleY = $Size / $viewHeight

    for ($i = 0; $i -lt $numbers.Count; $i += 2) {
        $x = ($translateX + ($logoScale * $numbers[$i])) * $pixelScaleX
        $y = ($translateY + ($logoScale * $numbers[$i + 1])) * $pixelScaleY
        $points.Add([System.Drawing.PointF]::new([float]$x, [float]$y))
    }

    $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $path.AddPolygon($points.ToArray())
    return $path
}

function Render-IconPng([int]$Size) {
    $bitmap = [System.Drawing.Bitmap]::new($Size, $Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $graphics.Clear([System.Drawing.Color]::Transparent)

    $radius = [float]($cornerRadius * $Size / $viewWidth)
    $backgroundPath = New-RoundedRectanglePath -Width $Size -Height $Size -Radius $radius
    $backgroundBrush = [System.Drawing.SolidBrush]::new($background)
    $graphics.FillPath($backgroundBrush, $backgroundPath)

    $fillBrush = [System.Drawing.SolidBrush]::new($fillColor)
    $penWidth = [float]($strokeWidth * $logoScale * $Size / $viewWidth)
    $strokePen = [System.Drawing.Pen]::new($strokeColor, $penWidth)
    $strokePen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round

    foreach ($match in $pathMatches) {
        $logoPath = Convert-SvgPathToGraphicsPath -Data $match.Groups[1].Value -Size $Size
        $graphics.FillPath($fillBrush, $logoPath)
        $graphics.DrawPath($strokePen, $logoPath)
        $logoPath.Dispose()
    }

    $stream = [IO.MemoryStream]::new()
    $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
    $bytes = $stream.ToArray()

    $stream.Dispose()
    $strokePen.Dispose()
    $fillBrush.Dispose()
    $backgroundBrush.Dispose()
    $backgroundPath.Dispose()
    $graphics.Dispose()
    $bitmap.Dispose()

    return ,$bytes
}

$sizes = @(16, 24, 32, 48, 64, 128, 256)
$images = @()
foreach ($size in $sizes) {
    $images += ,(Render-IconPng -Size $size)
}

$headerBytes = 6 + (16 * $sizes.Count)
$offset = $headerBytes
$stream = New-Object IO.MemoryStream
$writer = [IO.BinaryWriter]::new($stream)

$writer.Write([UInt16]0)
$writer.Write([UInt16]1)
$writer.Write([UInt16]$sizes.Count)

for ($i = 0; $i -lt $sizes.Count; $i++) {
    $size = $sizes[$i]
    $image = $images[$i]
    $dimensionByte = [Byte]$(if ($size -ge 256) { 0 } else { $size })
    $writer.Write($dimensionByte)
    $writer.Write($dimensionByte)
    $writer.Write([Byte]0)
    $writer.Write([Byte]0)
    $writer.Write([UInt16]1)
    $writer.Write([UInt16]32)
    $writer.Write([UInt32]$image.Length)
    $writer.Write([UInt32]$offset)
    $offset += $image.Length
}

foreach ($image in $images) {
    $writer.Write($image)
}

$writer.Flush()
[IO.File]::WriteAllBytes($OutputPath, $stream.ToArray())
$writer.Dispose()
$stream.Dispose()

Write-Host "[Rank Hunter] Windows icon ready: $OutputPath"
