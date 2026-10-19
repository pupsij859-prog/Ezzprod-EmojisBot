# Меняет одну настройку в %LOCALAPPDATA%\EzzProdBot\.env: set-env.ps1 BOT_TOKEN 123:abc
# Пустое значение (set-env.ps1 BOT_USERNAME "") очищает настройку. Значение в консоль не печатается.
param([Parameter(Mandatory)][string]$Name, [string]$Value = '')
$envFile = Join-Path $env:LOCALAPPDATA 'EzzProdBot\.env'
if (-not (Test-Path $envFile)) { Write-Host "Нет файла настроек $envFile : сначала запусти run.bat"; exit 1 }
$lines = @(Get-Content -LiteralPath $envFile -Encoding UTF8)
$pattern = '^' + [regex]::Escape($Name) + '='
if ($lines | Where-Object { $_ -match $pattern }) {
  $lines = $lines | ForEach-Object { if ($_ -match $pattern) { "$Name=$Value" } else { $_ } }
} else {
  $lines += "$Name=$Value"
}
[IO.File]::WriteAllLines($envFile, $lines, (New-Object Text.UTF8Encoding $false))
Write-Host "$Name обновлён в $envFile"
