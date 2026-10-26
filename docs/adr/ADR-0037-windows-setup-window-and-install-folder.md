# ADR-0037: Windows setup window, user-chosen install folder and data root

Status: Accepted, Issue #301. Amends the per-user, side-by-side install layout
of ADR-0033 and its in-app update flow; the default layout is unchanged.

## Context

The downloadable setup was a console program. It unpacked about 8,000 files
and ran `setup.cmd`, always into `%LOCALAPPDATA%\DubFlow`. The console stayed
blank for minutes, so a user closed it mid-install. There was no folder
choice, no shortcut, no "Installed apps" entry and no uninstaller. The desktop
host was also a console-subsystem program, so every launch opened a terminal
next to the window.

## Decision

- **Setup.**
  - `windows_setup.rs` is a GUI-subsystem program that opens no console. It embeds the release ZIP, the root launcher stub and `windows_setup.ps1`, a WinForms window run by the inbox Windows PowerShell 5.1.
  - The window:
    - checks the install folder: write access, free space, refusal of Program Files and Windows, and refusal of a non-empty folder that is not a DubFlow install;
    - unpacks with .NET `ZipFile`, keeping every entry inside the bundle folder;
    - runs the unchanged verify/stage/activate `bootstrap.py`, which now stages files in parallel and reports `stage done total` through `--progress-file`.

    `tar.exe` is not used, because it silently skips entries whose names it cannot decode.
- **Modes.**
  - **Wizard** is the default.
  - **Update** applies when the setup runs from `<root>\updates`, which is how existing hosts start it, with no arguments. It installs into that root and reopens DubFlow.
  - **`--silent [--install-root X] [--no-launch]`** is for CI and scripted installs.
- **Windows integration.** After a verified install the setup adds:
  - a root `DubFlow.exe` stub (`windows_launcher.rs`) that starts the version named in `current.json`, so shortcuts survive updates and rollbacks;
  - a Start menu shortcut and an optional Desktop shortcut;
  - an `HKCU` uninstall entry;
  - `uninstall.ps1`, which deletes only DubFlow's own entries in the folder (`versions`, `control`, `models`, …) and removes the folder only if it is then empty.

  Failures in these extras are logged and never undo a working install.
- **Data root.** The host's data root (models, job state, WebView profile) is the install root derived from its own path (`<root>\versions\<v>\app\bin`). Choosing another drive therefore moves the large data too. Development builds outside that layout keep `%LOCALAPPDATA%\DubFlow`. For the default install, both resolve to the same folder, so existing installs keep their data and profiles.
- **Host.** A release build of the host is a GUI-subsystem program. Every console child it starts already uses `CREATE_NO_WINDOW`.

## Consequences

- Installs into a custom folder start with no models and download them on first use. Moving an existing install is not supported; install fresh and let the old one be uninstalled.
- The setup needs about 1.3 GB of temporary space on the system drive while it unpacks, in addition to the install folder.
- The uninstaller is a PowerShell script started from "Installed apps", so a console may flash briefly.
