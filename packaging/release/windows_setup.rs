//! User-owned Windows bootstrapper for the downloadable release candidate.
//!
//! The executable embeds the exact release ZIP at build time. It uses the
//! inbox PowerShell `Expand-Archive` command only to unpack that payload, then
//! invokes the existing user-owned `setup.cmd`. No elevation manifest or
//! installer service is requested.

use std::env;
use std::error::Error;
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};
use std::time::{SystemTime, UNIX_EPOCH};

const PAYLOAD: &[u8] = include_bytes!(env!("DUBFLOW_PAYLOAD"));

fn quote_powershell(path: &Path) -> String {
    path.to_string_lossy().replace('\'', "''")
}

fn temporary_root() -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    env::temp_dir().join(format!("DubFlow-setup-{}-{}", std::process::id(), nonce))
}

fn run() -> Result<i32, Box<dyn Error>> {
    if PAYLOAD.is_empty() {
        return Err("embedded release payload is empty".into());
    }
    let root = temporary_root();
    let archive = root.join("release.zip");
    let extracted = root.join("bundle");
    fs::create_dir_all(&extracted)?;
    let result = (|| -> Result<i32, Box<dyn Error>> {
        let mut output = File::create(&archive)?;
        output.write_all(PAYLOAD)?;
        output.flush()?;
        output.sync_all()?;
        // PowerShell must be able to open the ZIP for reading on Windows.
        // Explicitly close the writer before starting the extractor.
        drop(output);

        let archive_literal = quote_powershell(&archive);
        let extracted_literal = quote_powershell(&extracted);
        let script = format!(
            "$ErrorActionPreference='Stop'; Expand-Archive -LiteralPath '{archive}' -DestinationPath '{destination}' -Force",
            archive = archive_literal,
            destination = extracted_literal,
        );
        let unpack = Command::new("powershell.exe")
            .args([
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
            ])
            .arg(script)
            .status()?;
        if !unpack.success() {
            return Err(format!("Expand-Archive failed with status {unpack}").into());
        }

        let setup = extracted.join("setup.cmd");
        if !setup.is_file() {
            return Err(format!("release payload did not contain setup.cmd: {}", setup.display()).into());
        }
        // Avoid passing a quoted temporary path through `cmd /C`: Windows
        // command parsing differs between `Command` and `cmd.exe` for paths
        // containing spaces. Running from the extracted directory lets the
        // batch file resolve `%~dp0` itself and keeps the command literal.
        let install = Command::new("cmd.exe")
            .current_dir(&extracted)
            .args(["/D", "/S", "/C", "setup.cmd"])
            .status()?;
        Ok(install.code().unwrap_or(1))
    })();
    let cleanup = fs::remove_dir_all(&root);
    if let Err(error) = cleanup {
        eprintln!("warning: unable to remove temporary setup directory {}: {error}", root.display());
    }
    result
}

fn main() -> ExitCode {
    match run() {
        Ok(code) => ExitCode::from(code.clamp(0, 255) as u8),
        Err(error) => {
            eprintln!("DubFlow setup failed: {error}");
            ExitCode::from(1)
        }
    }
}
