# Builds website/index.html from job-finder.html so the app can be hosted on any static web host.
# Run again after changing job-finder.html.
$src = Join-Path $PSScriptRoot 'job-finder.html'
$outDir = Join-Path $PSScriptRoot 'website'
New-Item -ItemType Directory -Force $outDir | Out-Null

$page = [System.IO.File]::ReadAllText($src, [System.Text.Encoding]::UTF8)
$head = @'
<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Search UK and European job boards by degree, and track the jobs you shortlist and apply for.">
</head>
<body>
'@
$html = $head + $page + "`n</body>`n</html>`n"
[System.IO.File]::WriteAllText((Join-Path $outDir 'index.html'), $html, (New-Object System.Text.UTF8Encoding $false))
"Built $(Join-Path $outDir 'index.html')"
