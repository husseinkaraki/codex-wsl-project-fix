# Codex WSL project-path fix

This is a temporary fix for the [Codex Desktop Windows/WSL project bug](https://github.com/openai/codex/issues/41290)
that sends Windows or UNC project paths to its WSL app-server.

On this computer, the fix is installed at
`C:\Users\karak\CodexFixes\codex-app-server-proxy`, and the Windows user
environment variable `CODEX_CLI_PATH` points to that file.

## How it works

The Windows user environment variable `CODEX_CLI_PATH` points Codex Desktop to
`%USERPROFILE%\CodexFixes\codex-app-server-proxy`.

Codex starts that small proxy instead of starting its bundled CLI directly. The
proxy starts the real bundled CLI and relays its JSON-RPC traffic. It changes
only the root paths in `project/create`, `project/import`, and `project/update`;
all other traffic passes through unchanged. It runs only while Codex is open.

## Normal use

After installation, fully quit and reopen Codex once. Then create projects from
the normal Codex interface. Nothing needs to be run for each project.

## Remove it after the Windows bug is fixed

Fully quit Codex, including its tray process, and run `remove-fix.ps1` from
PowerShell. It deletes `CODEX_CLI_PATH` and the installed proxy. With that
environment variable gone, Codex automatically returns to its bundled CLI.

`install-fix.ps1` reinstalls the proxy and user environment variable if needed.
