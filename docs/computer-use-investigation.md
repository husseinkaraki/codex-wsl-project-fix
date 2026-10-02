# Native Computer Use with a WSL agent: investigation

Updated: 2026-10-02. Status: **three full native Chrome tests passed; immediate
state reads after tab creation remain unresolved; accessibility-startup
experiment requires a manual Chrome restart**.

The goal is to operate Windows Chrome through Codex's bundled native Computer
Use API from an existing WSL-backed chat. The agent and repositories stay in
WSL. Completion requires verified typing, navigation, and mouse clicking in
that affected chat, followed by a successful test in another fresh turn.

## What the evidence currently establishes

The investigation has encountered several independent failures. Repairing an
earlier stage allows the next stage to run; it does not prove the later stage
works.

```mermaid
flowchart TD
    A[WSL Codex app-server] --> B[Windows-valid MCP metadata]
    B --> C[Windows node runtime and bridge CLI]
    C --> D[Selected permissions reach the tool]
    D --> E[Active and unlocked Windows desktop]
    E --> F[Fresh target window and matching capture]
    F --> G[Native browser URL resolution and policy check]
    G --> H[Verified typing, navigation, and clicking]
```

The URI and Windows-child launch repairs have cleared startup failures. Reloading
MCP connections cleared stale permissions in the affected chat. Chrome keyboard
and mouse navigation passed in the troubleshooting chat on October 1. An earlier
affected-chat attempt opened a tab and typed, but navigation ended with the
native URL-confidence stop; that attempt did not verify page load or mouse control.

On October 2, a later native inventory exposed no targetable Chrome window.
The supported native launch restored one New Tab window. A fresh read-only
baseline then returned from `get_window` but failed in `get_window_state` with
the same URL-confidence stop, before any keyboard or mouse input. This pins
down that attempt's failing API.

The user then loaded Example Domain and put Chrome in the foreground. A native
read-only baseline succeeded. **Two subsequent fresh-turn tests in the existing
affected chat each verified typing, committed navigation, and mouse clicking.**
The typed URLs were `https://example.com/?codex_wsl_cua=1` and
`https://example.com/?codex_wsl_cua=2`; each loaded the expected document and its
visible Learn more link led to `https://www.iana.org/help/example-domains`.
The second test started on IANA, establishing cross-origin navigation as well.
Screenshot, accessibility text, and document URL agreed; neither test returned
a native error. This satisfies the two fresh-turn input acceptance tests.

A separate controlled test then explicitly activated Chrome and confirmed a
matching IANA baseline. `Ctrl+T` returned successfully, but its immediate
`get_window_state` produced the URL-confidence stop. No typing or navigation
from the New Tab was attempted. **Foreground activation alone did not repair
this failure.** The two successful loaded-page tests do not establish a universal
URL-resolver repair.

A later fresh-turn observation of the existing New Tab succeeded with matching
screenshot/accessibility and native document URL `chrome://new-tab-page/`.
Another fresh turn then completed typing `https://example.com/?codex_wsl_cua=3`,
committed navigation, and the Learn more click to IANA with no native error.
**Three complete native input tests have now passed in the affected chat.**

A controlled comparison then focused the address bar on IANA, typed
`https://example.com/?codex_wsl_cua=4`, and used Chrome's `Alt+Enter` shortcut.
Typing and the key action returned, but the immediate `get_window_state` again
produced the URL-confidence stop. No observation or input followed; new-tab
creation, committed navigation, and the link destination were not verified.

These observations distinguish successful use of a previously opened New Tab
from unsuccessful immediate state reads after tab-creation actions. A
transition or accessibility-initialization issue is a hypothesis, not a proven
internal cause. The latter failure remains the reliability gap.

The exact unresolved error is:

```text
Computer Use has been stopped for this turn because it could not determine
the current browser URL on Windows with enough confidence to enforce policy.
Stop your work and send a final message noting why Computer Use ended.
```

That error reports unsuccessful URL verification. It does not identify which
internal stage failed or establish that the intended public site was denied.

## Issues and attempts recorded so far

The numbered evidence references below identify local diagnostic checkpoints.
They distinguish live native-tool results from isolated probes, unit tests, and
untested proposals. Private logs and configuration are not published here.

| Issue | What we tried | Observed result | Current conclusion / evidence |
| --- | --- | --- | --- |
| Project creation sends Windows/UNC roots to the WSL app-server | Separate project-root JSONL proxy | Existing project-path workaround published independently | Keep separate from Computer Use; see the main README |
| Windows `node_repl` rejects `file:///home/...` before JavaScript runs | Compare original URI with the same directory represented through WSL UNC | Original rejected; translated URI executed the isolated test | URI mismatch confirmed; checkpoints 1–3 |
| Initial adapter handled mounted Windows drives but missed Linux-native WSL directories | Add `wslpath` mapping and reverse directory-identity validation | Both drive and WSL mappings passed checks; native app enumeration subsequently worked | Startup repair works; checkpoints 2–5 |
| Windows bridge inherited the Linux project-proxy script as `CODEX_CLI_PATH` | Give only the Windows child a copied, verified Windows CLI; retain the WSL agent | Cleared the Windows-executable launch problem | Child launch repair retained; checkpoints 5–8, 13 |
| Shared Desktop native helper route remains incompatible with the global WSL selector | Use the bundled SDK's per-runtime fallback with `SKY_CUA_NATIVE_PIPE=0` | Native API calls and later Chrome input worked through that route | Native `@oai/sky` API is retained, but the Desktop shared connection has not been restored; checkpoint 13 |
| Windows child CLI differed from the installed package | Align the child with bundled CLI 0.159.0 and required native companions | Fresh configured probe imported Sky and enumerated apps | Version repair cleared startup; did not establish a URL-check repair; checkpoint 8 |
| An old adapter process kept serving calls after source changes | Identify the serving adapter, retire the old process, reload MCP connections; later add request-time loading | Actual tool received the URI translation | JavaScript reset alone does not replace the MCP launch; checkpoints 10, 12 |
| A standalone probe could not display app approval | Test import/enumeration without granting app consent; use the real chat tool for subsequent actions | Probe reached approval but lacked its UI | Standalone discovery is not a browser-control acceptance test; checkpoint 5 |
| Managed permissions still contained Linux filesystem paths | Translate permission paths to the same Windows-accessible objects in an isolated experiment | Windows path parsing advanced, then ACL setup failed on WSL UNC roots | Restricted WSL support remains unresolved; experimental changes rolled back; checkpoints 9–10 |
| Windows ACL enforcement failed on the WSL workspace | Test the existing elevated backend and repair missing bundled sandbox companions | `GetSecurityInfo` error 1 on UNC workspace roots | Simple path translation cannot repair this managed-profile failure; checkpoint 9 |
| The user selected Full Access, but the native tool still received a managed profile | Reset JavaScript, compare turn metadata with adapter audit, send supported `config/mcpServer/reload` | Reset failed to refresh permissions; MCP reload delivered `disabled` and restored enumeration | Confirmed stale-connection failure; checkpoints 16, 21–23 |
| Disconnected or locked Windows session blocked capture/input | Query Windows session/process metadata; reconnect and retest; later use the physical desktop | `GetCursorPos` access denied and monitor-capture errors correlated with inactive/locked sessions | An active desktop is necessary; physical-desktop Chrome test later passed; checkpoints 11, 18–20 |
| Remote access appeared usable while the helper's session changed state | Compare target process session, lock state, and live browser marker; bounded wait for active session | A session briefly active/unlocked later became disconnected/locked | Remote UI access alone did not establish a continuously usable helper desktop; checkpoint 19 |
| A possible accidental cancellation interrupted a test | Honor the stop, then perform a user-authorized fresh retry after checking session state | Session remained disconnected during the bounded wait | No successful input claim from that retry; checkpoint 19 |
| Chrome accessibility described Chrome while the screenshot showed Codex | Explicitly activate the returned Chrome window and reacquire screenshot/text | Capture then matched Chrome; subsequent root-chat input succeeded | Confirmed capture/focus discrepancy; its cause in the affected chat is still unproven; checkpoint 20 |
| Brave captures worked but later browser input hit URL-confidence enforcement | Native enumeration, activation, fresh capture, keyboard/click tests | Some early capture/click success; later input stopped | Reliable Brave control remains unverified; Chrome is the user's selected browser; checkpoints 6, 17, 20 |
| Full Codex restart did not produce a complete browser repair | User fully quit and reopened the application; inspect actual post-restart state | Discovery worked; inactive desktop and URL-confidence failures remained | Restart was not a verified universal fix; checkpoints 17–19 |
| Browser settings displayed an organization/region availability message | Read actual availability logs and the installed frontend decision | Local `reason=wsl-disabled` and corresponding WSL checks found | This was a Browser Use availability decision, not proof of an organization/region restriction; checkpoint 15 |
| Browser extension / Chrome developer integration might solve it | Inspect installed extension metadata and native/browser availability | Chrome extension was present; the WSL availability gate remained | Extension installation does not remove that gate; no proven native URL repair; checkpoints 14–15 |
| Different chats appeared to have different browser capabilities | Compare actual turn and MCP permission metadata; serialize native browser tests | Managed-vs-disabled and stale permissions explained startup differences | Shared foreground can interfere with input, but it was not the demonstrated cause of the path-parser failure; checkpoints 21–23 |
| Chrome worked in one chat but remained unreliable in the affected chat | In a fresh target-chat turn, select/activate Chrome, open a new tab, type a public URL, then press Enter and refresh | New tab and typing verified; navigation step stopped; click test never ran | Earlier incomplete acceptance attempt; checkpoint 23; subsequent loaded-page tests passed at checkpoints 27–28 |
| New-tab input/refresh returned an uncertain outcome | Reacquire a fresh window/state before another action | Confirmed the tab had opened | The wrapper did not preserve the original cause, so the specific initial failure remains unknown; checkpoint 23 |
| Need a reusable permissions refresh | Add `refresh-computer-use.py` with a dry run and process/pipe identity checks | 46 repository tests passed; real dry run and reload request succeeded; published in `bd9eb79` | Repairs stale MCP connections, not the native URL resolver; checkpoint 23 |
| Native inventory later reported no targetable Chrome window | Compare native discovery with Windows process/session metadata, then use the documented native `launch_app` recovery | Chrome had a Windows main window, but native inventory returned zero; one native launch returned one Chrome New Tab window | Target visibility restored for this attempt; its previous absence remains unexplained; checkpoint 26 |
| Fresh New Tab observation failed before any browser input | Fresh selection, separate `get_window` and `get_window_state` phase markers, one native observation | `get_window` returned; `get_window_state` produced the original URL-confidence turn-stop; no state or input followed | Pre-input state-read failure for New Tab; later loaded HTTPS baseline succeeded; checkpoints 26–27 |
| Need complete input acceptance in the affected chat | User prepares loaded Example Domain; select and explicitly activate a fresh native Chrome window; type, commit, observe, click, observe | Both query-bearing documents and both Learn more destinations verified in two fresh turns; no native errors | Loaded-page control verified in the existing WSL-backed chat; checkpoints 27–28 |
| Does foreground activation alone fix New Tab? | Start from matching IANA screenshot/text/URL after explicit activation; one `Ctrl+T` followed immediately by native state refresh | Key action returned; `get_window_state` failed with the original URL-confidence stop; no further input | New Tab startup unresolved; foreground alone insufficient for this controlled attempt; checkpoint 28 |
| Is a previously opened New Tab permanently unreadable? | One fresh read-only selection/activation/state observation, followed by a complete input test in another turn | Native `chrome://new-tab-page/` state read passed; typing, committed Example Domain navigation and IANA click all passed without native errors | Existing New Tab is usable in these later observations; immediate post-creation refresh remains unresolved; checkpoint 29 |
| Does opening a public URL directly in a new tab avoid the failing refresh? | From a verified IANA baseline, type the harmless fourth test URL and use native `Alt+Enter`, then immediately observe | Typing and key action returned; refresh failed with the original URL-confidence stop; tab creation and navigation were not verified | The immediate-refresh failure extends beyond blank `Ctrl+T`; no input or state read followed; checkpoint 30 |
| Could Chrome accessibility initialization affect the first observation? | Check only Chrome process flags; compare Chromium's documented mode behavior; prepare a separate default-dry-run startup experiment | Current Chrome has neither force nor disable renderer-accessibility flags; launcher syntax and real dry run passed; launch was not applied | `--force-renderer-accessibility=complete` is an unverified candidate requiring human-controlled Chrome restart; confidence medium-low; checkpoint 30 |

## Proposals that were not implemented

| Proposal | Status and reason |
| --- | --- |
| Move the agent to Windows | Excluded by the user's requirement; WSL remains selected |
| Remove permission metadata or fabricate Full Access | Not implemented; repairs preserve the actual selected profile |
| Restrict the Windows child to a projected temporary workspace | A draft was saved but never installed; abandoned after the user selected Full Access; no effectiveness claim |
| Replace native Computer Use with CDP, Playwright, or another MCP browser | Not implemented as a repair of the requested native mechanism |
| Apply older binary offsets from a community patch | Not implemented; current artifact compatibility and a safe source-level repair have not been established |
| Force complete Chrome accessibility at startup | Prepared as a separate private diagnostic launcher; dry run only; no effectiveness claim or public fix-script addition until tested |

The previously proposed loaded-public-page baseline has now been tested. It
enabled two complete native input tests in the affected chat, but does not
repair the separately observed New Tab state-read failure.

## Community research ledger

Read primary author reports and code, rather than treating search snippets or a
utility's static health check as proof. Dates below are source dates or the
latest checked update, not a promise that the workaround works on this machine.

| Source | Finding | How it changes this investigation |
| --- | --- | --- |
| [Manuel Parra's WSL URI patch](https://th3nolo.com/articles/codex-computer-use-wsl-sandboxcwd) | Maps tool-call cwd metadata for Windows, uses a Windows-native child CLI, and separates Chrome connection recovery | Independently supports the startup boundaries already repaired here; not a demonstrated fix for our remaining native URL error |
| [Upstream native URL issue #25271](https://github.com/openai/codex/issues/25271) — open; updated September 27 | Reproductions persist across browser generations; later comments separate extension recovery from native window failure | Investigate the Windows native URL path independently of WSL startup |
| [UIA document/focus investigation, August 29](https://github.com/openai/codex/issues/25271#issuecomment-5459908436) | Proposes stable numeric Document identity and corrected focus/refresh behavior; verified by its author on an older build | Concrete hypothesis, not a portable patch; compare local language, capture, and post-action behavior first |
| [English-label counterexample, September 10](https://github.com/openai/codex/issues/25271#issuecomment-5618209961) | Reports native failure despite English Document labels | Do not assume changing browser/system language solves every URL failure |
| [WinBridge Recovery](https://github.com/zemeng5208/winbridge-recovery) — main commit August 23; rechecked October 2 | Checks and repairs plugin/cache/runtime/registration drift; maintainer distinguishes URL enforcement from local consistency | Borrow its diagnostic boundaries; installing it is not evidence of a URL-resolution fix |
| [Windows Fast Patch context script](https://github.com/chen0416ccc-cpu/codex-windows-fast-patch-skill/blob/main/scripts/patch-computer-use-node-repl-context.ps1) — repository updated September 30 | A hash/version-specific SDK request-context patch for Sky 0.6.2 | Compare the actual installed SDK before considering it; its documented symptom differs from our final native stop |
| [Cross-call context report](https://gist.github.com/MSWEIMZ/0b8368f34a20c7ab6a89d53afebde14c) — August 7 | Reports `node_repl exec context not found` and same-call recovery in native Windows | Distinct error family; do not adopt an action batch that skips the current skill's observation requirements |
| [Rotated native-pipe repair #41453](https://github.com/openai/codex/issues/41453) — updated September 5 | Author describes refreshing an obsolete product-generated pipe identifier once after `FILE_NOT_FOUND` | Relevant to shared-connection lifecycle; our current per-runtime route is different and our final error is not missing-pipe |
| [Current Chrome/Edge reproduction #46943](https://github.com/openai/codex/issues/46943#issuecomment-5869800007) — September 28 | Native URL verification still fails after extension diagnostics pass; a separate non-browser capture timeout is also reported | Browser integration health does not prove native URL verification; do not assume the capture timeout explains our successful captures |
| [Current Edge reproduction #31221](https://github.com/openai/codex/issues/31221#issuecomment-5872343234) — September 28 | Sky 0.7.4 still fails after removing an obsolete CLI override, despite independently readable URL sources | Correct CLI selection is necessary but not sufficient; this newer generation has no verified native recovery in the report |
| [Chrome window/tab association report #42766](https://github.com/openai/codex/issues/42766) — September 4 | Native Chrome state fails while the separate connector can list tabs | Window association is a hypothesis; the reporter's interpretation is not a maintainer-confirmed diagnosis |
| [Same-SDK current-build report #45996](https://github.com/openai/codex/issues/45996#issuecomment-5948262149) — October 2 | Native Edge URL determination still fails on Sky 0.7.5 and package 26.930.2377.0 after the separate browser integration works | A newer release is not a demonstrated universal fix; the report uses Chinese UI, so it does not establish our English-UI cause |
| [Recent Chrome report #40474](https://github.com/openai/codex/issues/40474#issuecomment-5927363911) — October 1 | Native Chrome state fails on bundle 26.928.31416, including in a fresh chat, while Browser Use succeeds | A problem confined to one chat cannot explain every reproduction; our affected chat now passes loaded-page tests, so New Tab state is the remaining local comparison |
| [Isolated Windows runtime home #27463](https://github.com/openai/codex/issues/27463) — June 10 | Author reports desktop-app control after separating Windows helper files from the shared WSL home | Its acceptance examples do not establish Chrome navigation; our current shared helper directory is empty and shell execution works, so no matching failure was demonstrated and no home change was applied |
| [Chrome New Tab state report #46200](https://github.com/openai/codex/issues/46200) — September 17 | Native Chrome enumeration succeeds but a read-only New Tab state request terminates with the URL-confidence error | Closely matches our failing API and page class; no compatible repair is established by the report; its locale and versions differ |
| [Overlapping address-field report #34715](https://github.com/openai/codex/issues/34715) — July 22 | An Opera reporter finds an empty outer address element overlapping an inner element with a valid URL and proposes examining all candidates | A specific extraction hypothesis, not a demonstrated cause for our Chrome New Tab failure; no matching local observation or portable patch was established |
| [Chromium accessibility overview](https://chromium.googlesource.com/chromium/src.git/+/HEAD/docs/accessibility/overview.md) | Accessibility normally enables on demand; an explicit `--force-renderer-accessibility=complete` keeps the full mode enabled | Supports a controlled initialization experiment; does not establish that it fixes Codex's URL resolver |
| [Chrome keyboard shortcuts](https://support.google.com/chrome/answer/157179) | Documents address-bar `Alt+Enter` for opening a new tab | Provided a normal native-input comparison; our immediate refresh still failed after that action |

### Local applicability checks on October 2

- Both running adapters identify the same configured Windows node runtime and
  **Sky 0.7.5**. Their helper-transport module hashes agree.
- Both serving adapters were started after the installed adapter and child-CLI
  settings were last modified. Stale launch settings are therefore a weaker
  explanation for this comparison. The ordinary JavaScript environment exposes
  neither child environment values nor `process.env`, so that probe did not
  establish the live trusted service's environment; absent exposed values are
  not evidence that the child settings are missing.
- The community request-context patch requires **Sky 0.6.2** and a different
  exact module hash. It is not applicable to these artifacts; no vendor module
  was patched.
- Windows UI culture, installed UI culture, and ordinary culture report
  **en-US**. There is no user `ComSpec` override and the process value points to
  the standard Windows command interpreter. Those proposed explanations are
  deprioritized; these checks alone do not prove live Document selection.
- The copied Windows child CLI exists and matches its configured SHA-256 and
  installed-package provenance. WSL remains enabled in the Desktop config.
- Chrome, Desktop, and the Windows node runtimes share the active console
  session. This query establishes an active session, not continuous foreground
  ownership or unlocked status throughout a future test.
- The generated launch still advertises a shared pipe, while our child adapter
  selects the previously documented per-runtime route. A shared-pipe rotation
  patch is therefore not a direct repair for the current fallback URL failure.
- During the later zero-window audit, the actual affected-chat MCP requests
  still carried the user's disabled permission profile. The Windows input
  desktop was accessible `Default`, with no LogonUI process. The old
  managed-profile parser failure and externally inaccessible-desktop symptom
  were not reproduced; the native worker's complete effective context and the
  reason its inventory omitted a window remain unverified.
- A subsequent inventory in the affected chat returned exactly one Chrome
  window classified as New Tab. A fresh native state observation then succeeded;
  the earlier failure is not a demonstrated permanent New Tab restriction.
- The active Chrome main process has no force-renderer-accessibility or
  disable-renderer-accessibility startup switch. The prepared complete-mode
  launcher defaults to a dry run, refuses to launch while Chrome processes
  remain, and does not edit profile, registry, native policy, or engine settings.
  It has not been applied.
- Upstream issue #25271 still has 44 comments and is open; #46200 is also open.
  The reviewed Fast Patch and WinBridge repository heads are unchanged since
  the previous review. No newly compatible implementation was found in those
  refreshed sources.

### Current research conclusion

The sources support separating WSL startup repair, runtime/request-context
repair, desktop capture, and native URL verification. Local acceptance now
establishes a working native Chrome flow from loaded public pages in the
affected chat. The checked reports do not provide a portable, verified native
post-tab-creation URL fix for our current artifacts.
Older source-level hypotheses are useful for designing the next observation;
their binary offsets and version-pinned replacements are not compatible fixes.
This is an investigation result, not a claim that every remaining cause has
been ruled out or that the failure is proven to be an upstream defect.

Confidence is **high** that the documented loaded-page workflow passes the
observed acceptance tests and that a previously opened New Tab passed its full
test. Confidence is **low** in any specific internal
explanation for New Tab: the supported API reports the URL-confidence stop but
does not expose the extraction, window association, or validation stage.
The startup flag has **medium-low** confidence as a candidate and remains
untested. A prior community reproduction tried a renderer-accessibility flag
without resolving its native URL error, so the flag is not a general solution.

## Next experiments and acceptance rules

| Experiment | Evidence to collect | Decision rule | Confidence before test |
| --- | --- | --- | --- |
| Read-only runtime consistency audit | Actual adapter parent, active runtime, Sky version, native CLI selection, plugin generation, metadata shape | Repair only a demonstrated mismatch; keep the already working layers stable | High for identifying drift; low for proving it causes the URL stop |
| Check Windows UI language and native Document output | Read-only culture metadata and a supported native state observation on a loaded public page | If labels are English, deprioritize the language-only theory; do not change system language speculatively | Medium |
| Distinguish action failure from immediate-refresh failure | Record which supported API call returned or failed, while retaining the original error | A native stop ends input for that turn; no blind action retry | High diagnostic value |
| Compare a stable loaded page with a new-tab transition | Fresh native state, matching screenshot/text, explicit activation, one allowed action and refresh | Completed: loaded-page flows passed; explicitly activated New Tab state refresh failed; internal failing stage remains unknown | Medium before test; high for the observed difference |
| Recheck the affected chat in another fresh turn | Actual MCP profile, selected returned window, screenshot/focus, native typing, committed URL, clicked destination | Completed: two full tests passed; this verifies the loaded-page workflow, while New Tab startup remains unresolved | High for verification |
| Find a compatible New Tab repair | Primary source implementation or supported diagnostic that matches the current Sky/native build and retains URL verification | Test only an evidence-backed change; the already observed New Tab stop does not justify another unchanged retry | Low until a matching implementation or diagnostic is available |
| Compare direct-URL and blank-tab creation | Native input from a verified public baseline, one tab-creation action, immediate state read | Completed: `Alt+Enter` also stopped at refresh; new-tab creation/navigation unverified after stop | Medium before test; high for the observed failing phase |
| Pre-enable complete Chrome accessibility | Human exits Chrome and starts the reviewed diagnostic launcher; verify its real startup flag, then test native `Ctrl+T` plus immediate state in a fresh turn | A passing first state and complete input flow must be repeated; do not publish this candidate as a fix based on process flags alone | Medium-low |

Continue recording a source, its applicability, the single changed variable,
the exact observed result, and what the result rules out for every experiment.
Do not reinstall everything, repeat restarts, widen permissions, or modify URL
policy on the basis of an untested theory.

Windows foreground and app-approval behavior are described in the official
[Computer Use documentation](https://learn.chatgpt.com/docs/computer-use).
