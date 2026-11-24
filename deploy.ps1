# Päivittää Arduinon firmwaren ja käynnistää dashboardin uudelleen.
#
#   .\deploy.ps1                       # luxlamppu, portti tunnistetaan automaattisesti
#   .\deploy.ps1 -Sketch cps_blink_colors
#   .\deploy.ps1 -Port COM3 -NoDashboard
#
# Vaiheet:
#   1. sulkee käynnissä olevan dashboardin (python dashboard.py stop), jotta sarjaportti vapautuu
#   2. kääntää ja lataa firmware/<Sketch>:n arduino-cli:llä
#   3. käynnistää dashboardin uudelleen ilman konsoli-ikkunaa (pythonw), jolloin myös
#      dashboardin uusin koodi on käytössä
#   4. tarkistaa, että dashboard on käynnissä ja kirjoittaa Arduinon dataa lokiin
param(
    [string]$Sketch = "luxlamppu",
    [string]$Port = "",
    [string]$Fqbn = "arduino:avr:uno",
    [switch]$NoDashboard
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$dashDir = Join-Path $root "dashboard"
$sketchDir = Join-Path $root "firmware\$Sketch"
if (-not (Test-Path $sketchDir)) { throw "Sketsiä ei löydy: $sketchDir" }

$cli = (Get-Command arduino-cli -ErrorAction SilentlyContinue).Source
if (-not $cli) { $cli = Join-Path $env:USERPROFILE "scoop\shims\arduino-cli.exe" }
if (-not (Test-Path $cli)) { throw "arduino-cli ei löydy (asenna: scoop install arduino-cli)" }

# 1. Dashboard kiinni
Write-Host "== 1/4 Suljetaan dashboard"
Push-Location $dashDir
try { python dashboard.py stop } finally { Pop-Location }

# 2. Firmware
if (-not $Port) {
    $boards = & $cli board list --format json | ConvertFrom-Json
    $match = $boards.detected_ports | Where-Object { $_.matching_boards.fqbn -contains $Fqbn } | Select-Object -First 1
    if (-not $match) { throw "Korttia $Fqbn ei löytynyt. Onko USB-kaapeli kiinni?" }
    $Port = $match.port.address
}
Write-Host "== 2/4 Ladataan $Sketch porttiin $Port"
& $cli compile --upload -b $Fqbn -p $Port $sketchDir
$uploadOk = ($LASTEXITCODE -eq 0)
if (-not $uploadOk) { Write-Host "Lataus epäonnistui (exit $LASTEXITCODE)" -ForegroundColor Red }

# 3. Dashboard käyntiin (myös epäonnistuneen latauksen jälkeen, koska se suljettiin yllä)
if ($NoDashboard) { if ($uploadOk) { exit 0 } else { exit 1 } }
Write-Host "== 3/4 Käynnistetään dashboard"
$started = Get-Date
$proc = Start-Process pythonw -ArgumentList "dashboard.py --port $Port" -WorkingDirectory $dashDir -PassThru

# 4. Tarkistus: prosessi elossa ja uusi lokitiedosto täyttyy
Write-Host "== 4/4 Tarkistetaan"
Start-Sleep -Seconds 6
if ($proc.HasExited) {
    Write-Host "Dashboard sulkeutui heti (exit $($proc.ExitCode)). Aja 'python dashboard.py' nähdäksesi virheen." -ForegroundColor Red
    exit 1
}
$log = Get-ChildItem $dashDir -Filter "mittaus_*.csv" | Where-Object { $_.LastWriteTime -ge $started } |
       Sort-Object LastWriteTime | Select-Object -Last 1
$rows = if ($log) { @(Get-Content $log.FullName | Select-Object -Skip 1) } else { @() }
if ($rows.Count -gt 0) {
    Write-Host "Dashboard käynnissä (PID $($proc.Id)), $($rows.Count) riviä tiedostossa $($log.Name). Viimeisin:"
    Write-Host "  $($rows[-1])"
} else {
    Write-Host "Dashboard käynnissä (PID $($proc.Id)), mutta Arduinolta ei ole vielä tullut dataa." -ForegroundColor Yellow
    Write-Host "(Kaikki sketsit, esim. cps_blink_colors, eivät lähetä dataa.)"
}
if ($uploadOk) { exit 0 } else { exit 1 }
