$ErrorActionPreference = "Stop"

$codexHome = $env:CODEX_HOME
if ([string]::IsNullOrWhiteSpace($codexHome)) {
    $codexHome = Join-Path $HOME ".codex"
}

$installer = Join-Path $codexHome "skills\verdantflare-video\scripts\install-config.py"
$python = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $python) {
    & $python.Source -3 $installer
} else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $python) {
        Write-Error "Python 3.10 or newer is required."
        exit 1
    }
    & $python.Source $installer
}

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
