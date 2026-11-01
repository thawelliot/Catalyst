# Builds index.html (the website's home page) from job-finder.html.
# Run again after changing job-finder.html.
$src = Join-Path $PSScriptRoot 'job-finder.html'
$out = Join-Path $PSScriptRoot 'index.html'

$page = [System.IO.File]::ReadAllText($src, [System.Text.Encoding]::UTF8)
$head = @'
<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Catalyst: current graduate jobs by degree, pooled from UK and European job sites, with a shortlist and application tracker.">
</head>
<body>
'@
$html = $head + $page + "`n</body>`n</html>`n"
[System.IO.File]::WriteAllText($out, $html, (New-Object System.Text.UTF8Encoding $false))
"Built $out"
