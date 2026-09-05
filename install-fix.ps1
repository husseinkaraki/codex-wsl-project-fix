[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
trap {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}

$variableName = 'CODEX_CLI_PATH'
$installDirectory = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'CodexFixes'
$sourceProxy = Join-Path $PSScriptRoot 'codex-app-server-proxy'
$installedProxy = Join-Path $installDirectory 'codex-app-server-proxy'
$wslExecutable = Join-Path $env:WINDIR 'System32\wsl.exe'

function Convert-ToWslPath([string]$WindowsPath) {
    if ($WindowsPath -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "Expected a local Windows drive path, got: $WindowsPath"
    }
    $drive = $Matches[1].ToLowerInvariant()
    $tail = $Matches[2].Replace('\', '/')
    return "/mnt/$drive/$tail"
}

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

$linuxProxy = Convert-ToWslPath $installedProxy

$chmodProcess = Start-Process -FilePath $wslExecutable -ArgumentList @(
    '--exec', 'chmod', '755', $linuxProxy
) -Wait -PassThru -NoNewWindow
if ($chmodProcess.ExitCode -ne 0) {
    throw 'WSL could not mark the proxy executable.'
}

$windowsCodexHome = Join-Path $env:USERPROFILE '.codex'
$linuxCodexHome = Convert-ToWslPath $windowsCodexHome

$doctorProcess = Start-Process -FilePath $wslExecutable -ArgumentList @(
    '--exec', 'env', "CODEX_HOME=$linuxCodexHome", $linuxProxy, '--codex-fixes-doctor'
) -Wait -PassThru -NoNewWindow
if ($doctorProcess.ExitCode -ne 0) {
    throw 'The installed proxy health check failed; the environment override was not changed.'
}

[Environment]::SetEnvironmentVariable($variableName, $installedProxy, 'User')
Send-EnvironmentChanged

Write-Host ''
Write-Host 'Codex WSL project-path fix enabled.'
Write-Host "$variableName=$installedProxy"
Write-Host 'Fully quit Codex, including its tray process, and reopen it once.'
