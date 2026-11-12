$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$envPath = Join-Path $repoRoot '.env'
if (Test-Path -LiteralPath $envPath) {
  Write-Output 'An .env file already exists; leaving it unchanged.'
  exit 0
}

function New-RandomHexSecret {
  $bytes = New-Object byte[] 32
  $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
  return ([System.BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
}

$secrets = @{
  'POSTGRES_PASSWORD' = New-RandomHexSecret
  'GRAFANA_ADMIN_PASSWORD' = New-RandomHexSecret
  'JWT_SECRET_KEY' = New-RandomHexSecret
  'INITIAL_ADMIN_SETUP_TOKEN' = New-RandomHexSecret
}
$lines = Get-Content -LiteralPath (Join-Path $repoRoot '.env.example')
foreach ($key in $secrets.Keys) {
  $lines = $lines -replace "^$key=.*$", "$key=$($secrets[$key])"
}
[System.IO.File]::WriteAllLines($envPath, $lines, [System.Text.UTF8Encoding]::new($false))
Write-Output 'Created .env with four different random secrets. Keep this file private; it is excluded from Git.'
