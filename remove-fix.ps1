[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$variableName = 'CODEX_CLI_PATH'
$installDirectory = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'CodexFixes'
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

if (Get-Process -Name Codex -ErrorAction SilentlyContinue) {
    throw 'Fully quit Codex, including its tray process, before removing the fix.'
}

$currentValue = [Environment]::GetEnvironmentVariable($variableName, 'User')
if ($currentValue -and $currentValue -ne $installedProxy) {
    throw "$variableName points somewhere else and was not changed: $currentValue"
}

if (Test-Path -LiteralPath $installedProxy -PathType Leaf) {
    $marker = 'codex-fixes-project-path-proxy-v1'
    if (-not (Select-String -LiteralPath $installedProxy -SimpleMatch $marker -Quiet)) {
        throw 'The installed file is not owned by Codex Fixes and was not removed.'
    }
}

[Environment]::SetEnvironmentVariable($variableName, $null, 'User')
Send-EnvironmentChanged

if (Test-Path -LiteralPath $installedProxy) {
    Remove-Item -LiteralPath $installedProxy -Force
}
if (Test-Path -LiteralPath $installDirectory) {
    $remaining = @(Get-ChildItem -LiteralPath $installDirectory -Force)
    if ($remaining.Count -eq 0) {
        Remove-Item -LiteralPath $installDirectory -Force
    }
}

Write-Host 'Codex WSL project-path fix removed.'
Write-Host "$variableName was deleted. Codex will use its bundled CLI after reopening."
