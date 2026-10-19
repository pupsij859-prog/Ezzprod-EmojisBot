# Бот с публичным адресом через быстрый туннель Cloudflare (cloudflared tunnel --url).
# Поднимает туннель, ждёт выданный адрес *.trycloudflare.com, вписывает его в WEBAPP_URL
# (%LOCALAPPDATA%\EzzProdBot\.env) и запускает бота в этом же окне. Остановить: Ctrl+C (туннель закроется тоже).
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $env:LOCALAPPDATA 'emoji-bot-venv\Scripts\python.exe'
$envFile = Join-Path $env:LOCALAPPDATA 'EzzProdBot\.env'
$log = Join-Path $env:TEMP 'ezzprod-cloudflared.log'
$port = 8000

if (-not (Test-Path $py)) { Write-Host 'Нет окружения Python: сначала запусти run.bat'; exit 1 }
if (-not (Test-Path $envFile)) { Write-Host "Нет файла настроек $envFile : сначала запусти run.bat"; exit 1 }
$cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cf) { $cf = 'C:\Program Files (x86)\cloudflared\cloudflared.exe' }
if (-not (Test-Path $cf)) { Write-Host 'cloudflared не найден'; exit 1 }

Remove-Item $log -ErrorAction SilentlyContinue
$tunnel = Start-Process -FilePath $cf -ArgumentList 'tunnel', '--no-autoupdate', '--protocol', 'http2', '--url', "http://localhost:$port", '--logfile', $log `
  -WindowStyle Hidden -PassThru
Write-Host 'Поднимаю туннель Cloudflare...'
$url = $null
for ($i = 0; $i -lt 60 -and -not $url; $i++) {
  Start-Sleep -Seconds 1
  if (Test-Path $log) {
    $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
    if ($m) { $url = $m.Matches[0].Value }
  }
}
if (-not $url) { Write-Host "Туннель не выдал адрес, лог: $log"; Stop-Process -Id $tunnel.Id -Force; exit 1 }

$lines = Get-Content -LiteralPath $envFile -Encoding UTF8
$lines = $lines -replace '^WEBAPP_URL=.*$', "WEBAPP_URL=$url"
[IO.File]::WriteAllLines($envFile, $lines, (New-Object Text.UTF8Encoding $false))
Write-Host "Mini App: $url"

try {
  Set-Location $root
  & $py -m app
} finally {
  Stop-Process -Id $tunnel.Id -Force -ErrorAction SilentlyContinue
}
