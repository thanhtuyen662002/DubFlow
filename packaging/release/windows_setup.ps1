# DubFlow Windows setup window (#301).
#
# Started hidden by windows_setup.rs, which embeds this script, the release
# ZIP and the root DubFlow.exe stub. Windows PowerShell 5.1 and WinForms ship
# with every supported Windows, so the setup needs nothing installed first.
#
# Modes:
#  - wizard (default): choose the install folder, install with a progress bar,
#    then offer to open DubFlow;
#  - update: started from <install root>\updates by the in-app updater, which
#    passes no arguments; installs into that root and reopens DubFlow;
#  - silent (-Silent): no window, for the release smoke and scripted installs.
#
# The verified install itself is bootstrap.py from the bundle; this script
# only unpacks, reports progress and adds the Windows integration (stable
# DubFlow.exe, shortcuts, "Installed apps" entry and uninstaller).

param(
    [Parameter(Mandatory = $true)][string]$Archive,
    [Parameter(Mandatory = $true)][string]$Work,
    [Parameter(Mandatory = $true)][string]$Launcher,
    [Parameter(Mandatory = $true)][string]$SetupPath,
    [string]$Version = '',
    [string]$InstallRoot = '',
    [switch]$Silent,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
$UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\DubFlow'
$LogPath = Join-Path $env:TEMP 'DubFlow-setup.log'
$Bundle = Join-Path $Work 'bundle'
$ProgressFile = Join-Path $Work 'progress.txt'
$ResultFile = Join-Path $Work 'result.json'
$ErrorFile = Join-Path $Work 'bootstrap-error.txt'
# App files (~0.9 GB) plus the models downloaded on first launch (~1.1 GB).
$RequiredFreeBytes = 2.5GB

function Write-Log([string]$Message) {
    try { Add-Content -LiteralPath $LogPath -Value ("{0:u} {1}" -f (Get-Date), $Message) -Encoding UTF8 } catch { }
}

# Folder ownership (#304). A folder is DubFlow's only when it holds a working
# install, setup's ownership marker, or bootstrap's own interrupted staging;
# a stray `versions` or `control` folder proves nothing. The uninstaller
# reuses these functions (see Write-Uninstaller), so they use param() blocks
# and [IO.Path]::Combine and keep the version pattern of bootstrap.py inline.

# A working install: current.json points at an installed release.
function Test-DubFlowInstall {
    param([string]$Path)
    try {
        $pointer = [IO.File]::ReadAllText([IO.Path]::Combine($Path, 'current.json')) | ConvertFrom-Json
        $version = [string]$pointer.current_version
        if ($pointer.schema_version -ne 1 -or $version -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$' -or
            [string]$pointer.version_path -cne "versions/$version") { return $false }
        return [IO.File]::Exists([IO.Path]::Combine($Path, 'versions', $version, 'release-manifest.json'))
    } catch { return $false }
}

# Setup writes this marker before it copies anything into a folder it has
# checked, so an install interrupted before activation stays recognisable.
function Test-OwnerMarker {
    param([string]$Path)
    try {
        $marker = [IO.File]::ReadAllText([IO.Path]::Combine($Path, '.dubflow-install.json')) | ConvertFrom-Json
        $recorded = [IO.Path]::GetFullPath([string]$marker.install_root).TrimEnd('\', '/')
        return ($marker.schema_version -eq 1) -and ($marker.product -ceq 'DubFlow') -and
            ($recorded -ieq [IO.Path]::GetFullPath($Path).TrimEnd('\', '/'))
    } catch { return $false }
}

# An install interrupted by an older setup, which wrote no marker: bootstrap's
# resume state names its own staging folder inside this root.
function Test-InterruptedInstall {
    param([string]$Path)
    foreach ($file in @(Get-ChildItem -LiteralPath $Path -Filter 'install-progress-*.json' -File -Force -ErrorAction SilentlyContinue)) {
        try {
            $resume = [IO.File]::ReadAllText($file.FullName) | ConvertFrom-Json
            $version = [string]$resume.version
            if ($resume.schema_version -ne 1 -or @('staging', 'failed') -cnotcontains $resume.status -or
                $version -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$' -or $file.Name -cne "install-progress-$version.json") { continue }
            $staging = [IO.Path]::Combine($Path, 'versions', ".$version.staging")
            if ([IO.Directory]::Exists($staging) -and
                ([IO.Path]::GetFullPath([string]$resume.staging_path) -ieq [IO.Path]::GetFullPath($staging))) { return $true }
        } catch { }
    }
    return $false
}

function Test-DubFlowRoot {
    param([string]$Path)
    return (Test-DubFlowInstall $Path) -or (Test-OwnerMarker $Path) -or (Test-InterruptedInstall $Path)
}

function Write-OwnerMarker([string]$Root) {
    $marker = [ordered]@{ schema_version = 1; product = 'DubFlow'; install_root = [IO.Path]::GetFullPath($Root) }
    [IO.File]::WriteAllText([IO.Path]::Combine($Root, '.dubflow-install.json'), ($marker | ConvertTo-Json))
}

# Removes DubFlow's own entries from a DubFlow root and keeps everything
# else: only release folders with a manifest and bootstrap's staging folders
# inside `versions`, and only DubFlow's names at the top. Returns the entries
# that could not be removed (locked files).
function Remove-DubFlowFiles {
    param([string]$Root)
    $failed = @()
    $versions = [IO.Path]::Combine($Root, 'versions')
    if ([IO.Directory]::Exists($versions)) {
        foreach ($entry in @(Get-ChildItem -LiteralPath $versions -Directory -Force)) {
            $release = ($entry.Name -cmatch '^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$') -and
                [IO.File]::Exists([IO.Path]::Combine($entry.FullName, 'release-manifest.json'))
            $staging = $entry.Name -cmatch '^\.[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\.staging$'
            if (-not ($release -or $staging)) { continue }
            try { Remove-Item -LiteralPath $entry.FullName -Recurse -Force } catch { $failed += "versions\$($entry.Name)" }
        }
        if (@(Get-ChildItem -LiteralPath $versions -Force).Count -eq 0) { Remove-Item -LiteralPath $versions -Force }
    }
    foreach ($name in @('control', 'models', 'source-work', 'updates', 'current.json', 'install-state.json', 'DubFlow.cmd', 'DubFlow.exe', 'DubFlow.ico')) {
        $path = [IO.Path]::Combine($Root, $name)
        if (Test-Path -LiteralPath $path) {
            try { Remove-Item -LiteralPath $path -Recurse -Force } catch { $failed += $name }
        }
    }
    foreach ($file in @(Get-ChildItem -LiteralPath $Root -Filter 'install-progress-*.json' -File -Force -ErrorAction SilentlyContinue)) {
        if ($file.Name -cmatch '^install-progress-[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\.json$') {
            try { Remove-Item -LiteralPath $file.FullName -Force } catch { $failed += $file.Name }
        }
    }
    # The marker goes last, so a retry after a locked file still recognises the folder.
    if ($failed.Count -eq 0) { Remove-Item -LiteralPath ([IO.Path]::Combine($Root, '.dubflow-install.json')) -Force -ErrorAction SilentlyContinue }
    return , $failed
}

function Get-InstalledVersion([string]$Path) {
    try { return (Get-Content -LiteralPath (Join-Path $Path 'current.json') -Raw | ConvertFrom-Json).current_version } catch { return $null }
}

function Get-ExistingRoot {
    try {
        $registered = (Get-ItemProperty -LiteralPath $UninstallKey -ErrorAction Stop).InstallLocation
        if ($registered -and (Test-DubFlowRoot $registered)) { return $registered }
    } catch { }
    $default = Join-Path $env:LOCALAPPDATA 'DubFlow'
    if (Test-DubFlowRoot $default) { return $default }
    return $null
}

function Get-UpdateRoot {
    $folder = Split-Path -Parent $SetupPath
    if ((Split-Path -Leaf $folder) -ieq 'updates') {
        $root = Split-Path -Parent $folder
        if (Test-DubFlowInstall $root) { return $root }
    }
    return $null
}

# Returns an error message for the user, or $null when the folder is usable.
function Test-InstallRoot([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return 'Hãy chọn thư mục cài đặt.' }
    if (-not [IO.Path]::IsPathRooted($Path) -or -not ($Path -match '^[A-Za-z]:\\' -or $Path.StartsWith('\\'))) {
        return 'Thư mục cài đặt phải là đường dẫn đầy đủ, ví dụ D:\Apps\DubFlow.'
    }
    $full = [IO.Path]::GetFullPath($Path)
    foreach ($protected in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:SystemRoot)) {
        if ($protected -and $full.TrimEnd('\').StartsWith($protected.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
            return "DubFlow cài cho riêng tài khoản của bạn nên không cài được vào $protected (cần quyền quản trị). Hãy chọn thư mục khác."
        }
    }
    if ((Test-Path -LiteralPath $full -PathType Container) -and -not (Test-DubFlowRoot $full) -and
        @(Get-ChildItem -LiteralPath $full -Force -ErrorAction SilentlyContinue).Count -gt 0) {
        return "Thư mục này đã có tệp khác. Hãy chọn một thư mục trống, hoặc chọn thư mục cha để trình cài tạo thư mục DubFlow bên trong."
    }
    try {
        New-Item -ItemType Directory -Force -Path $full | Out-Null
        $probe = Join-Path $full ('.dubflow-write-test-' + [Guid]::NewGuid().ToString('N'))
        [IO.File]::WriteAllText($probe, 'ok')
        Remove-Item -LiteralPath $probe -Force
    } catch {
        return "Không ghi được vào thư mục này: $($_.Exception.Message)"
    }
    try {
        $drive = New-Object IO.DriveInfo ([IO.Path]::GetPathRoot($full))
        $needed = if (Test-DubFlowRoot $full) { 1.2GB } else { $RequiredFreeBytes }
        if ($drive.AvailableFreeSpace -lt $needed) {
            return ("Ổ {0} chỉ còn trống {1:N1} GB; DubFlow cần khoảng {2:N1} GB. Hãy giải phóng dung lượng hoặc chọn ổ khác." -f $drive.Name, ($drive.AvailableFreeSpace / 1GB), ($needed / 1GB))
        }
    } catch { }
    return $null
}

function Get-FreeSpaceText([string]$Path) {
    try {
        $drive = New-Object IO.DriveInfo ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path)))
        return ("Ổ {0} còn trống {1:N1} GB." -f $drive.Name.TrimEnd('\'), ($drive.AvailableFreeSpace / 1GB))
    } catch { return '' }
}

# Console tools started from this window-less setup must not open consoles.
function Start-Hidden([string]$FilePath, [string]$Arguments) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $info.Arguments = $Arguments
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    return [Diagnostics.Process]::Start($info)
}

function Start-Unpack {
    if (Test-Path -LiteralPath $Bundle) { Remove-Item -LiteralPath $Bundle -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $Bundle | Out-Null
    Remove-Item -LiteralPath $ProgressFile -Force -ErrorAction SilentlyContinue
    # A separate process keeps the window responsive. .NET's ZIP reader is far
    # faster than Expand-Archive; the inbox tar.exe is not used because it
    # silently skips entries whose names it cannot decode. Every entry must
    # stay inside the bundle folder; bootstrap.py then verifies each file.
    $quote = { param($text) "'" + $text.Replace("'", "''") + "'" }
    $command = @"
`$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
`$bundle = [IO.Path]::GetFullPath($(& $quote $Bundle)).TrimEnd('\') + '\'
`$zip = [IO.Compression.ZipFile]::OpenRead($(& $quote $Archive))
try {
    `$total = `$zip.Entries.Count; `$count = 0
    foreach (`$entry in `$zip.Entries) {
        `$count++
        `$target = [IO.Path]::GetFullPath([IO.Path]::Combine(`$bundle, `$entry.FullName))
        if (-not `$target.StartsWith(`$bundle, [StringComparison]::OrdinalIgnoreCase)) { throw "unsafe archive entry: `$(`$entry.FullName)" }
        if (`$entry.FullName.EndsWith('/')) { [void][IO.Directory]::CreateDirectory(`$target); continue }
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName(`$target))
        [IO.Compression.ZipFileExtensions]::ExtractToFile(`$entry, `$target, `$true)
        if (`$count % 100 -eq 0) { try { [IO.File]::WriteAllText($(& $quote $ProgressFile), "unpack `$count `$total") } catch { } }
    }
} finally { `$zip.Dispose() }
"@
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    return Start-Hidden $powershell "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $encoded"
}

function Start-Bootstrap([string]$Root) {
    foreach ($file in @($ProgressFile, $ResultFile, $ErrorFile)) { Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue }
    $python = Join-Path $Bundle 'runtime\python.exe'
    $script = Join-Path $Bundle 'app\packaging\release\bootstrap.py'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $script -PathType Leaf)) {
        throw 'Tệp cài đặt bị thiếu sau khi giải nén. Hãy tải lại trình cài đặt.'
    }
    # cmd redirects the one-line JSON result and any traceback to files the
    # window reads afterwards; /s keeps the outer quotes literal.
    $command = "`"`"$python`" -B `"$script`" --bundle-root `"$Bundle`" --install-root `"$($Root.TrimEnd('\'))`" --progress-file `"$ProgressFile`" > `"$ResultFile`" 2> `"$ErrorFile`"`""
    return Start-Hidden $env:ComSpec "/d /s /c $command"
}

function Read-Progress {
    try {
        $parts = ([IO.File]::ReadAllText($ProgressFile)).Trim().Split(' ')
        if ($parts.Count -eq 3) { return @{ Stage = $parts[0]; Done = [int]$parts[1]; Total = [int]$parts[2] } }
    } catch { }
    return $null
}

function Get-BootstrapFailure {
    $result = $null
    try {
        $line = @(Get-Content -LiteralPath $ResultFile -Encoding UTF8 | Where-Object { $_.Trim().StartsWith('{') })[-1]
        $result = $line | ConvertFrom-Json
    } catch { }
    if ($result -and $result.ready) { return $null }
    $detail = ''
    if ($result) { $detail = "$($result.code): $($result.message)" }
    else { try { $detail = (Get-Content -LiteralPath $ErrorFile -Raw -Encoding UTF8).Trim() } catch { } }
    Write-Log "bootstrap failed: $detail"
    $hint = switch -Regex ($detail) {
        '^(HASH_MISMATCH|SIZE_MISMATCH|ARTIFACT_MISSING|UNMANIFESTED_ARTIFACT|MANIFEST_)' { 'Trình cài đặt bị hỏng hoặc tải chưa trọn. Hãy tải lại file setup từ trang phát hành.'; break }
        'No space|disk full|There is not enough space|WinError 112' { 'Ổ đĩa đã đầy. Hãy giải phóng dung lượng rồi chạy lại trình cài; nó sẽ tiếp tục từ chỗ dừng.'; break }
        '^(STAGING_COPY_FAILED|INSTALL_ROOT_UNAVAILABLE|STAGING_UNAVAILABLE)' { 'Không chép được tệp vào thư mục cài đặt. Hãy kiểm tra quyền ghi hoặc phần mềm diệt virus, rồi chạy lại trình cài.'; break }
        '^VERSION_CONFLICT' { 'Thư mục này đã có một bản DubFlow cùng số phiên bản nhưng khác nội dung. Hãy chọn thư mục khác.'; break }
        default { 'Cài đặt không hoàn tất. Chạy lại trình cài đặt sẽ tiếp tục từ chỗ dừng.' }
    }
    return "$hint`r`n`r`nChi tiết: $detail"
}

function Write-Uninstaller([string]$Root) {
    # The uninstaller carries the same ownership checks and removal rules.
    $functions = foreach ($name in @('Test-DubFlowInstall', 'Test-OwnerMarker', 'Test-InterruptedInstall', 'Test-DubFlowRoot', 'Remove-DubFlowFiles')) {
        "function $name {$((Get-Item "function:$name").ScriptBlock.ToString())}`r`n"
    }
    $script = @'
# DubFlow uninstaller, written by setup. Removes only DubFlow's own files.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
#FUNCTIONS#
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$title = 'Gỡ cài đặt DubFlow'
if (-not (Test-DubFlowRoot $root)) {
    [Windows.Forms.MessageBox]::Show("Không nhận ra bản cài DubFlow trong:`r`n$root`r`n`r`nTrình gỡ không xóa gì. Bạn có thể tự xóa thư mục này nếu chắc chắn.", $title, 'OK', 'Warning') | Out-Null
    exit 3
}
$running = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase) })
if ($running.Count -gt 0) {
    [Windows.Forms.MessageBox]::Show("DubFlow đang chạy. Hãy đóng DubFlow (và chờ video đang xử lý dừng) rồi gỡ lại.", $title, 'OK', 'Warning') | Out-Null
    exit 1
}
$answer = [Windows.Forms.MessageBox]::Show("Gỡ DubFlow khỏi máy?`r`n`r`nSẽ xóa app, model và hàng đợi trong:`r`n$root`r`n`r`nVideo kết quả của bạn không bị xóa.", $title, 'YesNo', 'Question')
if ($answer -ne 'Yes') { exit 2 }
foreach ($link in @((Join-Path ([Environment]::GetFolderPath('Programs')) 'DubFlow.lnk'), (Join-Path ([Environment]::GetFolderPath('Desktop')) 'DubFlow.lnk'))) {
    try {
        if ((Test-Path -LiteralPath $link) -and ((New-Object -ComObject WScript.Shell).CreateShortcut($link).TargetPath -like "$root\*")) { Remove-Item -LiteralPath $link -Force }
    } catch { }
}
try {
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\DubFlow'
    if ((Get-ItemProperty -LiteralPath $key -ErrorAction Stop).InstallLocation -eq $root) { Remove-Item -LiteralPath $key -Recurse -Force }
} catch { }
$failed = Remove-DubFlowFiles $root
if ($failed.Count -gt 0) {
    [Windows.Forms.MessageBox]::Show("Đã gỡ DubFlow, nhưng một số tệp đang bị khóa nên chưa xóa được:`r`n$($failed -join ', ')`r`n`r`nKhởi động lại máy rồi chạy lại trình gỡ (Settings > Apps > Installed apps).", $title, 'OK', 'Warning') | Out-Null
} else {
    $kept = @(Get-ChildItem -LiteralPath $root -Force | Where-Object { $_.Name -ne 'uninstall.ps1' })
    # This script is DubFlow's last file; remove it once it has exited. rd
    # removes the folder only when nothing else is left in it.
    $command = "ping -n 3 127.0.0.1 >nul & del /f /q `"$root\uninstall.ps1`" & rd `"$root`""
    Start-Process -FilePath $env:ComSpec -ArgumentList @('/d', '/c', $command) -WindowStyle Hidden
    $message = if ($kept.Count -gt 0) { "Đã gỡ DubFlow khỏi máy.`r`n`r`nThư mục $root vẫn còn tệp không phải của DubFlow nên được giữ lại." } else { 'Đã gỡ DubFlow khỏi máy.' }
    [Windows.Forms.MessageBox]::Show($message, $title, 'OK', 'Information') | Out-Null
}
'@
    $script = $script.Replace('#FUNCTIONS#', ($functions -join ''))
    # Windows PowerShell 5.1 reads scripts without a BOM as the ANSI code page.
    [IO.File]::WriteAllText((Join-Path $Root 'uninstall.ps1'), $script, (New-Object Text.UTF8Encoding $true))
}

function Set-Shortcut([string]$Path, [string]$Root) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = Join-Path $Root 'DubFlow.exe'
    $shortcut.WorkingDirectory = $Root
    $shortcut.IconLocation = (Join-Path $Root 'DubFlow.ico') + ',0'
    $shortcut.Description = 'DubFlow: lồng tiếng và phụ đề tiếng Việt'
    $shortcut.Save()
}

# Everything after the verified install: the stable launcher, icon, shortcuts,
# uninstaller and the "Installed apps" entry. Failures here are logged but do
# not undo a working install.
function Complete-Install([string]$Root, [bool]$DesktopShortcut) {
    $version = Get-InstalledVersion $Root
    $stub = Join-Path $Root 'DubFlow.exe'
    try { Copy-Item -LiteralPath $Launcher -Destination $stub -Force } catch { Write-Log "launcher copy failed: $_" }
    try {
        Copy-Item -LiteralPath (Join-Path $Root "versions\$version\app\apps\desktop\src-tauri\icons\icon.ico") -Destination (Join-Path $Root 'DubFlow.ico') -Force
    } catch { Write-Log "icon copy failed: $_" }
    try { Write-Uninstaller $Root } catch { Write-Log "uninstaller failed: $_" }
    try {
        Set-Shortcut (Join-Path ([Environment]::GetFolderPath('Programs')) 'DubFlow.lnk') $Root
        $desktop = Join-Path ([Environment]::GetFolderPath('Desktop')) 'DubFlow.lnk'
        if ($DesktopShortcut -or (Test-Path -LiteralPath $desktop)) { Set-Shortcut $desktop $Root }
    } catch { Write-Log "shortcut failed: $_" }
    try {
        $sizeKb = [int](((Get-ChildItem -LiteralPath (Join-Path $Root "versions\$version") -Recurse -File -Force | Measure-Object Length -Sum).Sum) / 1KB)
        New-Item -Path $UninstallKey -Force | Out-Null
        $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $values = @{
            DisplayName = 'DubFlow'; DisplayVersion = "$version"; Publisher = 'DubFlow'
            InstallLocation = $Root; DisplayIcon = (Join-Path $Root 'DubFlow.ico')
            UninstallString = "`"$powershell`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $Root 'uninstall.ps1')`""
            URLInfoAbout = 'https://github.com/thanhtuyen662002/DubFlow'
        }
        foreach ($name in $values.Keys) { New-ItemProperty -Path $UninstallKey -Name $name -Value $values[$name] -PropertyType String -Force | Out-Null }
        foreach ($name in @('NoModify', 'NoRepair')) { New-ItemProperty -Path $UninstallKey -Name $name -Value 1 -PropertyType DWord -Force | Out-Null }
        New-ItemProperty -Path $UninstallKey -Name 'EstimatedSize' -Value $sizeKb -PropertyType DWord -Force | Out-Null
    } catch { Write-Log "registration failed: $_" }
    Write-Log "installed $version into $Root"
}

function Start-DubFlow([string]$Root) {
    $stub = Join-Path $Root 'DubFlow.exe'
    if (Test-Path -LiteralPath $stub -PathType Leaf) { Start-Process -FilePath $stub -WorkingDirectory $Root }
    else { Start-Process -FilePath $env:ComSpec -ArgumentList @('/d', '/c', "`"$(Join-Path $Root 'DubFlow.cmd')`"") -WindowStyle Hidden }
}

Write-Log "setup $Version started from $SetupPath"
$updateRoot = Get-UpdateRoot

# ---------------------------------------------------------------- silent ---
if ($Silent) {
    try {
        $root = if ($InstallRoot) { $InstallRoot } elseif ($updateRoot) { $updateRoot } elseif (Get-ExistingRoot) { Get-ExistingRoot } else { Join-Path $env:LOCALAPPDATA 'DubFlow' }
        $problem = Test-InstallRoot $root
        if ($problem) { throw $problem }
        Write-OwnerMarker $root
        $unpack = Start-Unpack; $unpack.WaitForExit()
        if ($unpack.ExitCode -ne 0) { throw "unpack failed with exit code $($unpack.ExitCode)" }
        $install = Start-Bootstrap $root; $install.WaitForExit()
        $failure = Get-BootstrapFailure
        if ($failure) { throw $failure }
        Complete-Install $root $true
        if (-not $NoLaunch) { Start-DubFlow $root }
        exit 0
    } catch {
        Write-Log "silent setup failed: $_"
        exit 1
    }
}

# ---------------------------------------------------------------- window ---
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()

$font = New-Object Drawing.Font('Segoe UI', 9.5)
$titleFont = New-Object Drawing.Font('Segoe UI Semibold', 14)
$form = New-Object Windows.Forms.Form
$form.Text = if ($Version) { "Cài đặt DubFlow $Version" } else { 'Cài đặt DubFlow' }
$form.ClientSize = New-Object Drawing.Size(560, 340)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.StartPosition = 'CenterScreen'
$form.Font = $font
$form.BackColor = [Drawing.Color]::White
try { $form.Icon = [Drawing.Icon]::ExtractAssociatedIcon($SetupPath) } catch { }

function New-Label([string]$Text, [int]$X, [int]$Y, [int]$Width, [int]$Height, $Font = $null) {
    $label = New-Object Windows.Forms.Label
    $label.Text = $Text
    $label.Location = New-Object Drawing.Point($X, $Y)
    $label.Size = New-Object Drawing.Size($Width, $Height)
    if ($Font) { $label.Font = $Font }
    return $label
}

function New-Button([string]$Text, [int]$X, [int]$Y, [int]$Width = 110) {
    $button = New-Object Windows.Forms.Button
    $button.Text = $Text
    $button.Location = New-Object Drawing.Point($X, $Y)
    $button.Size = New-Object Drawing.Size($Width, 32)
    return $button
}

function New-Page {
    $panel = New-Object Windows.Forms.Panel
    $panel.Dock = 'Fill'
    $panel.Visible = $false
    $form.Controls.Add($panel)
    return $panel
}

# Page 1: choose the folder.
$choose = New-Page
$choose.Controls.Add((New-Label $form.Text 24 20 510 34 $titleFont))
$choose.Controls.Add((New-Label 'DubFlow lồng tiếng và làm phụ đề tiếng Việt cho video tiếng Trung, chạy hoàn toàn trên máy của bạn.' 24 60 510 40))
$choose.Controls.Add((New-Label 'Thư mục cài đặt:' 24 108 510 20))
$pathBox = New-Object Windows.Forms.TextBox
$pathBox.Location = New-Object Drawing.Point(24, 132)
$pathBox.Size = New-Object Drawing.Size(390, 26)
$choose.Controls.Add($pathBox)
$browse = New-Button 'Chọn…' 424 129 110
$choose.Controls.Add($browse)
$spaceLabel = New-Label '' 24 164 510 20
$spaceLabel.ForeColor = [Drawing.Color]::DimGray
$choose.Controls.Add($spaceLabel)
$noteLabel = New-Label 'App, các model (khoảng 1 GB, tải khi mở lần đầu) và hàng đợi được lưu trong thư mục này; cần khoảng 2,5 GB trống. Video kết quả vẫn lưu cạnh video gốc hoặc nơi bạn chọn trong app.' 24 186 510 52
$noteLabel.ForeColor = [Drawing.Color]::DimGray
$choose.Controls.Add($noteLabel)
$desktopBox = New-Object Windows.Forms.CheckBox
$desktopBox.Text = 'Tạo lối tắt trên Desktop'
$desktopBox.Checked = $true
$desktopBox.Location = New-Object Drawing.Point(24, 244)
$desktopBox.Size = New-Object Drawing.Size(300, 24)
$choose.Controls.Add($desktopBox)
$install = New-Button 'Cài đặt' 304 290
$cancel = New-Button 'Hủy' 424 290
$choose.Controls.Add($install)
$choose.Controls.Add($cancel)

# Page 2: progress.
$working = New-Page
$workingTitle = New-Label 'Đang cài đặt DubFlow…' 24 20 510 34 $titleFont
$working.Controls.Add($workingTitle)
$statusLabel = New-Label 'Đang chuẩn bị…' 24 80 510 24
$working.Controls.Add($statusLabel)
$bar = New-Object Windows.Forms.ProgressBar
$bar.Location = New-Object Drawing.Point(24, 110)
$bar.Size = New-Object Drawing.Size(510, 22)
$bar.Style = 'Marquee'
$working.Controls.Add($bar)
$hintLabel = New-Label 'Thường mất 2–5 phút. Nếu bị gián đoạn, chạy lại trình cài đặt sẽ tiếp tục từ chỗ dừng.' 24 144 510 40
$hintLabel.ForeColor = [Drawing.Color]::DimGray
$working.Controls.Add($hintLabel)

# Page 3: done.
$done = New-Page
$doneTitle = New-Label 'Đã cài xong DubFlow' 24 20 510 34 $titleFont
$done.Controls.Add($doneTitle)
$doneText = New-Label '' 24 64 510 110
$done.Controls.Add($doneText)
$openBox = New-Object Windows.Forms.CheckBox
$openBox.Text = 'Mở DubFlow ngay'
$openBox.Checked = $true
$openBox.Location = New-Object Drawing.Point(24, 190)
$openBox.Size = New-Object Drawing.Size(300, 24)
$done.Controls.Add($openBox)
$finish = New-Button 'Hoàn tất' 424 290
$done.Controls.Add($finish)

# Page 4: error.
$failedPage = New-Page
$failedPage.Controls.Add((New-Label 'Chưa cài được DubFlow' 24 20 510 34 $titleFont))
$errorBox = New-Object Windows.Forms.TextBox
$errorBox.Multiline = $true
$errorBox.ReadOnly = $true
$errorBox.ScrollBars = 'Vertical'
$errorBox.Location = New-Object Drawing.Point(24, 64)
$errorBox.Size = New-Object Drawing.Size(510, 200)
$failedPage.Controls.Add($errorBox)
$retry = New-Button 'Thử lại' 304 290
$close = New-Button 'Đóng' 424 290
$failedPage.Controls.Add($retry)
$failedPage.Controls.Add($close)

$state = @{ Step = 'idle'; Process = $null; Root = ''; Desktop = $true; Update = [bool]$updateRoot; ExitCode = 2 }

function Show-Page($page) {
    foreach ($candidate in @($choose, $working, $done, $failedPage)) { $candidate.Visible = ($candidate -eq $page) }
}

function Show-Failure([string]$Message) {
    $timer.Stop()
    $state.Step = 'failed'
    $state.ExitCode = 1
    $errorBox.Text = "$Message`r`n`r`nNhật ký cài đặt: $LogPath"
    $retry.Visible = -not $state.Update
    Show-Page $failedPage
}

$defaultNote = $noteLabel.Text
function Update-SpaceLabel {
    $spaceLabel.Text = Get-FreeSpaceText $pathBox.Text
    $current = $null
    try { if (Test-DubFlowRoot $pathBox.Text) { $current = Get-InstalledVersion $pathBox.Text } } catch { }
    $noteLabel.Text = if ($current) {
        "Thư mục này đã có DubFlow $current. Bản mới được cài bên cạnh; hàng đợi, model và cài đặt của bạn được giữ nguyên."
    } else { $defaultNote }
}

$existing = Get-ExistingRoot
$pathBox.Text = if ($InstallRoot) { $InstallRoot } elseif ($existing) { $existing } else { Join-Path $env:LOCALAPPDATA 'DubFlow' }
Update-SpaceLabel
$pathBox.Add_TextChanged({ Update-SpaceLabel })

$browse.Add_Click({
    $dialog = New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Chọn nơi cài DubFlow. Trình cài sẽ tạo thư mục DubFlow bên trong, trừ khi bạn chọn một thư mục DubFlow hoặc thư mục trống.'
    $dialog.ShowNewFolderButton = $true
    $start = $pathBox.Text
    while ($start -and -not (Test-Path -LiteralPath $start -PathType Container)) { $start = Split-Path -Parent $start }
    if ($start) { $dialog.SelectedPath = $start }
    if ($dialog.ShowDialog($form) -eq 'OK') {
        $picked = $dialog.SelectedPath
        $empty = @(Get-ChildItem -LiteralPath $picked -Force -ErrorAction SilentlyContinue).Count -eq 0
        $isRootOfDrive = $picked.TrimEnd('\').Length -le 2
        if ((Test-DubFlowRoot $picked) -or ((Split-Path -Leaf $picked) -ieq 'DubFlow') -or ($empty -and -not $isRootOfDrive)) {
            $pathBox.Text = $picked
        } else {
            $pathBox.Text = Join-Path $picked 'DubFlow'
        }
    }
})

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 300
$timer.Add_Tick({
    try {
        $process = $state.Process
        if ($state.Step -eq 'unpack') {
            $progress = Read-Progress
            if ($progress -and $progress.Total -gt 0) {
                if ($bar.Style -ne 'Continuous') { $bar.Style = 'Continuous' }
                $bar.Maximum = $progress.Total
                $bar.Value = [Math]::Min($progress.Done, $progress.Total)
                $statusLabel.Text = ('Đang giải nén… {0}%' -f [int](100 * $progress.Done / $progress.Total))
            }
            if (-not $process.HasExited) { return }
            if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $Bundle 'release-manifest.json'))) {
                Show-Failure "Không giải nén được trình cài đặt (mã $($process.ExitCode)). Ổ C: có thể đã đầy, hoặc file setup bị hỏng; hãy tải lại."
                return
            }
            if (-not $Version) {
                try { $script:Version = (Get-Content -LiteralPath (Join-Path $Bundle 'release-manifest.json') -Raw | ConvertFrom-Json).version } catch { }
            }
            $statusLabel.Text = 'Đang kiểm tra tệp cài đặt…'
            $bar.Style = 'Marquee'
            $state.Process = Start-Bootstrap $state.Root
            $state.Step = 'install'
            return
        }
        if ($state.Step -eq 'install') {
            $progress = Read-Progress
            if ($progress -and $progress.Total -gt 0) {
                switch ($progress.Stage) {
                    'verify' { $statusLabel.Text = 'Đang kiểm tra tệp cài đặt…' }
                    'copy' {
                        if ($bar.Style -ne 'Continuous') { $bar.Style = 'Continuous' }
                        $bar.Maximum = $progress.Total
                        $bar.Value = [Math]::Min($progress.Done, $bar.Maximum)
                        $statusLabel.Text = ('Đang chép tệp… {0:N0}/{1:N0} ({2}%)' -f $progress.Done, $progress.Total, [int](100 * $progress.Done / $progress.Total))
                    }
                    'check' { $bar.Value = $bar.Maximum; $statusLabel.Text = 'Đang kiểm tra lại bản đã cài…' }
                    'activate' { $statusLabel.Text = 'Đang hoàn tất…' }
                }
            }
            if (-not $process.HasExited) { return }
            $failure = Get-BootstrapFailure
            if ($failure) { Show-Failure $failure; return }
            $statusLabel.Text = 'Đang tạo lối tắt…'
            $timer.Stop()
            Complete-Install $state.Root $state.Desktop
            $state.Step = 'done'
            $state.ExitCode = 0
            if ($state.Update) {
                Start-DubFlow $state.Root
                $form.Close()
                return
            }
            $version = Get-InstalledVersion $state.Root
            $doneTitle.Text = "Đã cài xong DubFlow $version"
            $shortcutText = if ($state.Desktop) { 'menu Start hoặc lối tắt DubFlow trên Desktop' } else { 'menu Start (gõ "DubFlow")' }
            $doneText.Text = "Mở DubFlow từ $shortcutText.`r`n`r`nThư mục cài đặt: $($state.Root)`r`n`r`nĐể gỡ cài đặt: Settings > Apps > Installed apps > DubFlow."
            Show-Page $done
            $finish.Focus() | Out-Null
        }
    } catch {
        Write-Log "setup step failed: $_"
        Show-Failure "Lỗi khi cài đặt: $($_.Exception.Message)"
    }
})

function Start-Install([string]$Root) {
    $state.Root = $Root.TrimEnd('\')
    $state.Desktop = $desktopBox.Checked
    $bar.Style = 'Marquee'
    $statusLabel.Text = 'Đang giải nén…'
    Show-Page $working
    try {
        Write-OwnerMarker $state.Root
        $state.Process = Start-Unpack
        $state.Step = 'unpack'
        $timer.Start()
    } catch {
        Show-Failure "Không giải nén được trình cài đặt: $($_.Exception.Message)"
    }
}

$install.Add_Click({
    $target = $pathBox.Text.Trim()
    $problem = Test-InstallRoot $target
    if ($problem) {
        [Windows.Forms.MessageBox]::Show($form, $problem, $form.Text, 'OK', 'Warning') | Out-Null
        return
    }
    Write-Log "installing into $target"
    Start-Install $target
})
$cancel.Add_Click({ $form.Close() })
$close.Add_Click({ $form.Close() })
$retry.Add_Click({ Show-Page $choose })
$finish.Add_Click({
    if ($openBox.Checked -and -not $NoLaunch) { Start-DubFlow $state.Root }
    $form.Close()
})

$form.Add_FormClosing({
    param($sender, $closing)
    if ($state.Step -eq 'unpack' -or $state.Step -eq 'install') {
        $answer = [Windows.Forms.MessageBox]::Show($form, 'DubFlow đang được cài. Dừng lại bây giờ?' + "`r`n`r`n" + 'Lần chạy trình cài sau sẽ tiếp tục từ chỗ dừng.', $form.Text, 'YesNo', 'Warning')
        if ($answer -ne 'Yes') { $closing.Cancel = $true; return }
        $timer.Stop()
        # Stop the whole tree (cmd and python); staged files resume next run.
        try { if ($state.Process -and -not $state.Process.HasExited) { & taskkill.exe /T /F /PID $state.Process.Id | Out-Null } } catch { }
        Write-Log 'setup cancelled during install'
    }
})

$form.Add_Shown({
    $form.Activate()
    if ($state.Update) {
        $workingTitle.Text = if ($Version) { "Đang cập nhật DubFlow lên $Version…" } else { 'Đang cập nhật DubFlow…' }
        $hintLabel.Text = 'DubFlow sẽ tự mở lại khi cập nhật xong. Hàng đợi và cài đặt của bạn được giữ nguyên.'
        Start-Install $updateRoot
    } else {
        Show-Page $choose
        $install.Focus() | Out-Null
    }
})
$form.AcceptButton = $install
$form.CancelButton = $cancel

[Windows.Forms.Application]::Run($form)
exit $state.ExitCode
