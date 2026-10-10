param(
    [ValidateSet("backend", "frontend", "web", "desktop", "all")]
    [string]$Target = "all"
)

$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$dashboardRoot = Join-Path $repoRoot "dashboard"
$desktopRoot = Join-Path $repoRoot "desktop\src"
$uvCacheRoot = Join-Path $repoRoot ".uv-cache"

function ConvertTo-SingleQuotedLiteral([string]$Value) {
    return $Value.Replace("'", "''")
}

function Start-DevWindow([string]$Title, [string]$WorkingDirectory, [string]$Command) {
    Start-Process powershell.exe -WorkingDirectory $WorkingDirectory -ArgumentList @(
        "-NoExit",
        "-Command",
        "`$Host.UI.RawUI.WindowTitle = '$Title'; $Command"
    )
}

function Test-DevPortInUse([int]$Port) {
    return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Find-AvailableDevPort([int]$StartPort) {
    for ($port = $StartPort; $port -le ($StartPort + 20); $port++) {
        if (-not (Test-DevPortInUse $port)) {
            return $port
        }
    }
    throw "No available development port found between $StartPort and $($StartPort + 20)."
}

function Wait-HttpReady([string]$Url, [System.Diagnostics.Process]$Process) {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($Process.HasExited) {
            throw "Frontend exited with code $($Process.ExitCode). Run 'npm run dev' in '$dashboardRoot' to see the error."
        }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 1
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "Frontend process $($Process.Id) started, but $Url did not become ready within 15 seconds."
}

$escapedRepoRoot = ConvertTo-SingleQuotedLiteral $repoRoot
$escapedDesktopRoot = ConvertTo-SingleQuotedLiteral $desktopRoot
$escapedUvCacheRoot = ConvertTo-SingleQuotedLiteral $uvCacheRoot

$startBackend = $Target -in @("backend", "web", "desktop", "all")
$startFrontend = $Target -in @("frontend", "web", "all")
$startDesktop = $Target -in @("desktop", "all")

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCommand) {
    $npmCommand = Get-Command npm -ErrorAction SilentlyContinue
}
$wailsCommand = Get-Command wails3 -ErrorAction SilentlyContinue

if ($startBackend -and -not $uvCommand) {
    throw "uv was not found in PATH. Install uv or open a terminal where uv is available."
}
if ($startFrontend -and -not $npmCommand) {
    throw "npm was not found in PATH. Install Node.js 18+ and reopen PowerShell."
}
if ($startDesktop -and -not $wailsCommand) {
    throw "wails3 was not found in PATH. Install Wails v3 and reopen PowerShell."
}

if ($startBackend) {
    if (Test-DevPortInUse 8088) {
        Write-Host "Backend is already listening on http://127.0.0.1:8088; reusing it."
    } else {
        Start-DevWindow "HomeMind Backend" $repoRoot (
            "Set-Location -LiteralPath '$escapedRepoRoot'; " +
            "`$env:UV_CACHE_DIR = '$escapedUvCacheRoot'; " +
            "uv run --frozen homemind run --host 127.0.0.1 --port 8088"
        )
    }
}

if ($startFrontend) {
    $frontendPort = Find-AvailableDevPort 5173
    $previousDevPort = $env:VITE_DEV_PORT
    $previousApiPort = $env:VITE_API_PORT
    try {
        $env:VITE_DEV_PORT = [string]$frontendPort
        $env:VITE_API_PORT = "8088"
        $frontendProcess = Start-Process -FilePath $npmCommand.Source `
            -ArgumentList @("run", "dev") `
            -WorkingDirectory $dashboardRoot `
            -PassThru
    } finally {
        $env:VITE_DEV_PORT = $previousDevPort
        $env:VITE_API_PORT = $previousApiPort
    }
    $frontendUrl = "http://127.0.0.1:$frontendPort"
    Wait-HttpReady $frontendUrl $frontendProcess
    Write-Host "Frontend started (PID $($frontendProcess.Id)): $frontendUrl"
}

if ($startDesktop) {
    if (Test-DevPortInUse 9245) {
        Write-Host "Wails dev server is already listening on 127.0.0.1:9245; skipping duplicate desktop startup."
    } else {
        Start-DevWindow "HomeMind Desktop" $desktopRoot (
            "Set-Location -LiteralPath '$escapedDesktopRoot'; " +
            "`$env:OCTOP_DESKTOP_URL = 'http://127.0.0.1:8088'; " +
            "wails3 dev"
        )
    }
}
