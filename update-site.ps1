# Install the downloaded zip into the site folder, then publish.
# Run from anywhere: powershell -ExecutionPolicy Bypass -File update-site.ps1

$zip  = "C:\Users\letua\OneDrive - Lincoln University\JFDA\app\File transformer with AI integration.zip"
$site = "C:\Users\letua\Downloads\jfda-site"
$tmp  = "$env:TEMP\jfda_site_extract"

if (-not (Test-Path $zip)) { throw "Zip not found: $zip" }

Write-Host ">> Extracting" -ForegroundColor Cyan
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive $zip -DestinationPath $tmp

# The zip may or may not have a wrapper folder — find the one holding index.html
$root = Get-ChildItem $tmp -Recurse -File -Filter "index.html" |
        Sort-Object { $_.FullName.Length } | Select-Object -First 1
if (-not $root) { throw "No index.html in the zip - wrong download?" }
$src = $root.Directory.FullName

Write-Host ">> Copying into $site" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $site | Out-Null
# .git must survive, so copy contents rather than replacing the folder
Get-ChildItem $src -Force | Where-Object { $_.Name -ne '.git' } |
    Copy-Item -Destination $site -Recurse -Force

# Dotfiles sometimes do not survive a zip round trip; GitHub Pages needs this
# one or it runs Jekyll and refuses to serve the _ds folder.
New-Item -ItemType File -Path "$site\.nojekyll" -Force | Out-Null

Write-Host ">> Files in place:" -ForegroundColor Green
Get-ChildItem $site | Select-Object Name

Write-Host ""
Write-Host ">> Publishing" -ForegroundColor Cyan
Set-Location $site
git add -A
git commit -m "Update formatter"
git push

Write-Host ""
Write-Host "Done. Wait ~2 minutes, then hard-refresh (Ctrl+F5):" -ForegroundColor Green
Write-Host "  https://anh-tuan-le.github.io/jfda-formatter/" -ForegroundColor Green
