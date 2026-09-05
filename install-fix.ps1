[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$variableName = 'CODEX_CLI_PATH'
$installDirectory = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'CodexFixes'
$sourceProxy = Join-Path $PSScriptRoot 'codex-app-server-proxy'
$installedProxy = Join-Path $installDirectory 'codex-app-server-proxy'

function Send-EnvironmentChanged {
    if (-not ('CodexFixes.EnvironmentBroadcast' -as [type])) {
        Add-Type @'
using System;
using System.Runtime.InteropServices;
namespace CodexFixes {
    public static class EnvironmentBroadcast {
        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        public static extern IntPtr SendMessageTimeout(
            IntPtr hWnd,
            uint message,
            UIntPtr wParam,
            string lParam,
            uint flags,
            uint timeout,
            out UIntPtr result);
    }
}
'@
    }

    $result = [UIntPtr]::Zero
    [CodexFixes.EnvironmentBroadcast]::SendMessageTimeout(
        [IntPtr]0xffff,
        0x001A,
        [UIntPtr]::Zero,
        'Environment',
        0x0002,
        5000,
        [ref]$result
    ) | Out-Null
}

if (-not (Test-Path -LiteralPath $sourceProxy -PathType Leaf)) {
    throw "The proxy file is missing: $sourceProxy"
}

$marker = 'codex-fixes-project-path-proxy-v1'
if (-not (Select-String -LiteralPath $sourceProxy -SimpleMatch $marker -Quiet)) {
    throw 'The proxy file does not contain the expected ownership marker.'
}

$currentValue = [Environment]::GetEnvironmentVariable($variableName, 'User')
if ($currentValue -and $currentValue -ne $installedProxy) {
    throw "$variableName already points somewhere else: $currentValue"
}

New-Item -ItemType Directory -Force -Path $installDirectory | Out-Null
$temporaryProxy = Join-Path $installDirectory ('.codex-app-server-proxy.' + $PID + '.tmp')
try {
    Copy-Item -LiteralPath $sourceProxy -Destination $temporaryProxy -Force
    Move-Item -LiteralPath $temporaryProxy -Destination $installedProxy -Force
} finally {
    Remove-Item -LiteralPath $temporaryProxy -Force -ErrorAction SilentlyContinue
}

$linuxProxy = (& wsl.exe --exec wslpath -u $installedProxy | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $linuxProxy) {
    throw 'WSL could not translate the installed proxy path.'
}

& wsl.exe --exec chmod 755 $linuxProxy
if ($LASTEXITCODE -ne 0) {
    throw 'WSL could not mark the proxy executable.'
}

$windowsCodexHome = Join-Path $env:USERPROFILE '.codex'
$linuxCodexHome = (& wsl.exe --exec wslpath -u $windowsCodexHome | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $linuxCodexHome) {
    throw 'WSL could not translate the Codex home path.'
}

& wsl.exe --exec env "CODEX_HOME=$linuxCodexHome" $linuxProxy --codex-fixes-doctor
if ($LASTEXITCODE -ne 0) {
    throw 'The installed proxy health check failed; the environment override was not changed.'
}

[Environment]::SetEnvironmentVariable($variableName, $installedProxy, 'User')
Send-EnvironmentChanged

Write-Host ''
Write-Host 'Codex WSL project-path fix enabled.'
Write-Host "$variableName=$installedProxy"
Write-Host 'Fully quit Codex, including its tray process, and reopen it once.'
