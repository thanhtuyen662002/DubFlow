//! User-owned Windows setup for a DubFlow release.
//!
//! The executable embeds the exact release ZIP, the root `DubFlow.exe` stub
//! (windows_launcher.rs) and the setup window script (windows_setup.ps1) at
//! build time. It is a GUI-subsystem program, so no console opens: it writes
//! those files to a private temporary folder and runs the script with the
//! inbox Windows PowerShell, which shows the setup window (or installs
//! silently with `--silent`). No elevation manifest or installer service is
//! requested.
//!
//! Arguments: `--silent` (no window), `--no-launch` (do not open DubFlow
//! afterwards) and `--install-root <folder>` (preselect or, when silent, use
//! that folder).

#![windows_subsystem = "windows"]

use std::env;
use std::error::Error;
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const PAYLOAD: &[u8] = include_bytes!(env!("DUBFLOW_PAYLOAD"));
const LAUNCHER: &[u8] = include_bytes!(env!("DUBFLOW_LAUNCHER"));
const SCRIPT: &str = include_str!("windows_setup.ps1");
const VERSION: Option<&str> = option_env!("DUBFLOW_VERSION");
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[cfg(windows)]
#[link(name = "user32")]
extern "system" {
    fn MessageBoxW(window: *mut core::ffi::c_void, text: *const u16, caption: *const u16, kind: u32) -> i32;
}

/// Without a console, an error before the setup window exists is shown here.
fn show_error(message: &str) {
    #[cfg(windows)]
    {
        let wide = |text: &str| text.encode_utf16().chain(std::iter::once(0)).collect::<Vec<u16>>();
        let (text, caption) = (wide(message), wide("Cài đặt DubFlow"));
        // MB_OK | MB_ICONERROR
        unsafe { MessageBoxW(std::ptr::null_mut(), text.as_ptr(), caption.as_ptr(), 0x10) };
    }
    #[cfg(not(windows))]
    eprintln!("{message}");
}

struct Options {
    silent: bool,
    no_launch: bool,
    install_root: Option<String>,
}

fn options() -> Result<Options, String> {
    let mut options = Options { silent: false, no_launch: false, install_root: None };
    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--silent" | "/S" => options.silent = true,
            "--no-launch" => options.no_launch = true,
            "--install-root" => options.install_root = Some(args.next().ok_or("--install-root needs a folder")?),
            other => return Err(format!("unknown argument: {other}")),
        }
    }
    Ok(options)
}

fn temporary_root() -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    env::temp_dir().join(format!("DubFlow-setup-{}-{}", std::process::id(), nonce))
}

fn system_binary(parts: &[&str]) -> Result<PathBuf, Box<dyn Error>> {
    let system_root = env::var_os("SystemRoot").ok_or("SystemRoot is not set")?;
    let mut path = PathBuf::from(system_root);
    for part in parts {
        path.push(part);
    }
    if !path.is_file() || path.is_symlink() {
        return Err(format!("system binary is missing or unsafe: {}", path.display()).into());
    }
    Ok(path)
}

fn write_file(path: &Path, parts: &[&[u8]]) -> Result<(), Box<dyn Error>> {
    let mut output = File::create(path)?;
    for part in parts {
        output.write_all(part)?;
    }
    output.flush()?;
    output.sync_all()?;
    Ok(())
}

fn run(options: &Options) -> Result<i32, Box<dyn Error>> {
    if PAYLOAD.is_empty() || LAUNCHER.is_empty() {
        return Err("embedded release payload is empty".into());
    }
    let setup = env::current_exe()?;
    let root = temporary_root();
    // A single create_dir call makes a pre-created predictable temp path fail
    // closed instead of silently reusing an attacker-controlled directory.
    fs::create_dir(&root)?;
    let result = (|| -> Result<i32, Box<dyn Error>> {
        let archive = root.join("release.zip");
        let launcher = root.join("DubFlow.exe");
        let script = root.join("setup.ps1");
        write_file(&archive, &[PAYLOAD])?;
        write_file(&launcher, &[LAUNCHER])?;
        // Windows PowerShell 5.1 reads a script without a BOM as the ANSI code
        // page, which would garble the Vietnamese text.
        write_file(&script, &[b"\xEF\xBB\xBF", SCRIPT.as_bytes()])?;

        let powershell = system_binary(&["System32", "WindowsPowerShell", "v1.0", "powershell.exe"])?;
        let mut command = Command::new(powershell);
        command
            .args(["-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(&script)
            .arg("-Archive").arg(&archive)
            .arg("-Work").arg(&root)
            .arg("-Launcher").arg(&launcher)
            .arg("-SetupPath").arg(&setup);
        if let Some(version) = VERSION.filter(|version| !version.is_empty()) {
            command.arg("-Version").arg(version);
        }
        if let Some(folder) = &options.install_root {
            command.arg("-InstallRoot").arg(folder);
        }
        if options.silent {
            command.arg("-Silent");
        }
        if options.no_launch {
            command.arg("-NoLaunch");
        }
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        let status = command.status()?;
        Ok(status.code().unwrap_or(1))
    })();
    let _ = fs::remove_dir_all(&root);
    result
}

fn main() -> ExitCode {
    let options = match options() {
        Ok(options) => options,
        Err(error) => {
            show_error(&format!("Tham số không hợp lệ: {error}\n\nDùng: --silent, --no-launch, --install-root <thư mục>"));
            return ExitCode::from(1);
        }
    };
    match run(&options) {
        Ok(code) => ExitCode::from(code.clamp(0, 255) as u8),
        Err(error) => {
            if !options.silent {
                show_error(&format!("Không mở được trình cài đặt DubFlow.\n\n{error}\n\nỔ C: có thể đã đầy; hãy giải phóng dung lượng rồi chạy lại."));
            }
            ExitCode::from(1)
        }
    }
}
