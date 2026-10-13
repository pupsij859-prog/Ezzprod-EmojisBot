# Бот с публичным адресом через localhost.run (работает там, где туннели Cloudflare заблокированы).
# Бесплатный localhost.run время от времени меняет адрес, не разрывая соединение. Скрипт следит за ним:
# при новом адресе вписывает его в WEBAPP_URL (%LOCALAPPDATA%\EzzProdBot\.env) и перезапускает бота,
# чтобы кнопка меню и /start вели на рабочий адрес. Если туннель или бот упали — поднимает их снова.
# Остановить: Ctrl+C.
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $env:LOCALAPPDATA 'emoji-bot-venv\Scripts\python.exe'
$envFile = Join-Path $env:LOCALAPPDATA 'EzzProdBot\.env'
$log = Join-Path $env:TEMP 'ezzprod-tunnel.log'
$port = 8000

if (-not (Test-Path $py)) { Write-Host 'Нет окружения Python: сначала запусти run.bat'; exit 1 }
if (-not (Test-Path $envFile)) { Write-Host "Нет файла настроек $envFile : сначала запусти run.bat"; exit 1 }

function Set-WebAppUrl([string]$url) {
  $lines = Get-Content -LiteralPath $envFile -Encoding UTF8
  $lines = $lines -replace '^WEBAPP_URL=.*$', "WEBAPP_URL=$url"
  [IO.File]::WriteAllLines($envFile, $lines, (New-Object Text.UTF8Encoding $false))
}

function Start-Tunnel {
  Remove-Item $log, "$log.err" -ErrorAction SilentlyContinue
  $sshArgs = @('-T', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=3',
    '-o', 'ExitOnForwardFailure=yes', '-R', "80:127.0.0.1:$port", 'nokey@localhost.run')
  Start-Process -FilePath 'ssh' -ArgumentList $sshArgs -RedirectStandardOutput $log -RedirectStandardError "$log.err" -NoNewWindow -PassThru
}

function Start-Bot {
  Start-Process -FilePath $py -ArgumentList '-m', 'app' -WorkingDirectory $root -NoNewWindow -PassThru
}

function Stop-Bot($proc) {
  if ($proc -and -not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    $proc.WaitForExit(10000) | Out-Null
  }
}

$tunnel = $null
$bot = $null
$url = ''
try {
  while ($true) {
    if (-not $tunnel -or $tunnel.HasExited) {
      Write-Host 'туннель: подключаюсь к localhost.run...'
      $tunnel = Start-Tunnel
      $url = ''
      Start-Sleep -Seconds 3
    }
    $new = ''
    if (Test-Path $log) {
      $hits = Select-String -LiteralPath $log -Pattern 'https://[a-z0-9]+\.lhr\.life' -AllMatches -ErrorAction SilentlyContinue
      if ($hits) { $new = @($hits)[-1].Matches[-1].Value }
    }
    if ($new -and $new -ne $url) {
      $url = $new
      Write-Host ''
      Write-Host "  адрес Mini App: $url"
      Write-Host ''
      Set-WebAppUrl $url
      Stop-Bot $bot
      $bot = Start-Bot
    } elseif ($url -and $bot -and $bot.HasExited) {
      Write-Host 'бот остановился, перезапускаю...'
      $bot = Start-Bot
    }
    Start-Sleep -Seconds 2
  }
} finally {
  Stop-Bot $bot
  if ($tunnel -and -not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -ErrorAction SilentlyContinue }
}
