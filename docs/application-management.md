# Application launch management

The Apps / Terminal inspector contains workspace launch profiles and live runs.
Implementation and validation use the same backend through the `application` tool.
This is not a browser launcher: URLs, HTML, and HTTP checks are not prerequisites.

## Profiles

- Direct mode uses an executable and an argument array, with no shell interpolation.
- Commands use an explicitly selected shell. Windows batch scripts require CMD.
- Working directories must resolve inside the selected workspace. Executables may
  be installed elsewhere (Unity Editor, dotnet, Python, or a built WPF application).
- Environment variables are inherited from StratumCode. No credentials editor is
  provided in this version. Select the actual runtime executable explicitly.
- `task` means an exit-code-based batch run; `service` and `desktop` are long-lived.
- `visible` explicitly requests a window. It is off by default.
- `stdin` enables asynchronous pipe input and EOF. This is not PTY/TUI emulation.
- `encoding` controls stdout/stderr decoding and stdin encoding (default UTF-8).
- An optional literal `ready_text` records that startup output was observed.

Example direct launch profiles (paths and arguments must match the real project):

| Project | Executable | Arguments | Kind |
| --- | --- | --- | --- |
| Python CLI | `.venv/Scripts/python.exe` | `["-u", "main.py"]` | task |
| .NET desktop | `dotnet` | `["run", "--project", "MyApp.csproj"]` | desktop |
| Built WPF | `bin/MyApp.exe` | `[]` | desktop |
| Unity | Absolute path to `Unity.exe` | `["-projectPath", "<absolute project path>"]` | desktop |
| Web service | Actual server executable | Its argument array | service |

These are configuration examples, not automatically detected or validated commands.

## State and ownership

Process state and readiness are independent. Running does not mean ready; a log
marker or an observation-based readiness confirmation does not mean acceptance.
Even task exit code zero only means the process succeeded, not all user requirements.
Only this manager's owned runs may be stopped/restarted, scoped to the workspace.
Duplicate starts of the same profile are rejected; restart stops first and creates
a new run ID. Desktop data must be saved before forced process-tree termination.

Profiles are persisted in StratumCode's SQLite database. Process handles, bounded
combined logs (100,000 characters), and run history live only in the current host
process. Log reads default to the last 12,000 characters. Polling lists omit logs.
Normal Python shutdown attempts to stop live owned runs. A crash cannot guarantee
cleanup; the next host never adopts an old PID or claims a previous process is ready.
Detached launchers, existing Unity/desktop instances, full-screen console apps,
automatic GUI interaction, file-log tailing, and durable run recovery are not yet
supported. Launch the real foreground executable, not a launcher that exits early.

## API

- GET `/api/applications`: current workspace, saved profiles, current-host runs.
- POST `/api/applications/action`: `workspace` from the snapshot and `action`.
- Actions: list, save, delete, start, read, stop, restart, input, confirm_ready.
- Runtime actions use `run_id`; start/delete use `profile_id`; save uses `profile`.
- Input uses `text` or `eof`; confirm_ready requires an observation `note`.
- A workspace change rejects stale UI actions rather than targeting a different project.
- Mutating HTTP actions require application/json; ordinary cross-origin form posts
  are not accepted.

## Verification of this implementation

Validation's main prompt describes evidence requirements and generic capability
selection, not concrete execution tool names. Tool schemas own their intended use,
parameters, limits, and lifecycle semantics. Validation-capable registered tools
are exposed and executable without changing the main prompt. Application lifecycle
bookkeeping is explicitly not an observation; reading actual output or exit results
can count as a verification attempt, never automatic task acceptance.

Seven focused checks cover process exit/logs, input, duplicate starts, stop/restart,
workspace isolation, invalid launches, tool availability, and module MIME handling.
The production frontend builds successfully. An isolated browser preview exercised
profile creation, launching a real Python CLI, and display of output plus exit code 0.
Unity/WPF launches and full-screen console interaction were not exercised.
