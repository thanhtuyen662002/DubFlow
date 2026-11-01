//! Âm thanh (Issue #420): the CC0 sound-effect pack DubFlow downloads on
//! first use into `<data root>\sfx\v1`, the user's own sound folder, and
//! which audio files the editor may play. The pack is a release asset pinned
//! by SHA-256 and size; it is fetched with the bundled Python's updater
//! client (the same HTTP path as app updates), verified here, and only the
//! files its catalog lists are written out. Sounds play in the preview only
//! from the pack, a folder the user chose, or files the user picked or dropped.
use serde_json::{json, Value};
use std::collections::HashSet;
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{LazyLock, Mutex};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use crate::{data_root, display_path, fail, version_root, CREATE_NO_WINDOW};

pub(crate) const PACK_VERSION: &str = "v1";
const PACK_URL: &str = "https://github.com/thanhtuyen662002/DubFlow/releases/download/sfx-pack-v1/dubflow-sfx-v1.zip";
const PACK_SHA256: &str = "eed1adad0c7f9cc4943a05e76e128ef193c8b7aa89c3c010900353e8385840d2";
const PACK_BYTES: u64 = 2_194_688;
const CATALOG_FILE: &str = "sfx-catalog.json";
const INSTALLED_FILE: &str = "installed.json";
const MAX_SOUNDS: usize = 2000;
/// Files `user_audio_list` returns at most, three folder levels deep.
const MAX_USER_FILES: usize = 2000;
const MAX_PICKED: usize = 200;
pub(crate) const AUDIO_EXTENSIONS: [&str; 6] = ["mp3", "wav", "ogg", "m4a", "aac", "flac"];

/// Streams the asset with the updater's client; each line of stdout is JSON.
const DOWNLOAD_SCRIPT: &str = r#"import json, sys
from engine.dubflow.update.client import urllib_fetch_stream
url, dest, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
received = 0
with open(dest, "xb") as output:
    for chunk in urllib_fetch_stream(url):
        received += len(chunk)
        if received > limit:
            print(json.dumps({"error": "too_large"}), flush=True)
            sys.exit(3)
        output.write(chunk)
        print(json.dumps({"received": received}), flush=True)
print(json.dumps({"done": received}), flush=True)
"#;

#[derive(Debug, Clone, Default)]
struct Install {
    /// "idle", "downloading", "installing" or "failed".
    state: &'static str,
    received: u64,
    error: Option<String>,
}

static INSTALL: LazyLock<Mutex<Install>> = LazyLock::new(|| Mutex::new(Install { state: "idle", ..Install::default() }));

/// Folders and files the user chose this session; their audio may be streamed.
#[derive(Default)]
struct Allowed {
    folders: Vec<PathBuf>,
    files: HashSet<PathBuf>,
}

static ALLOWED: LazyLock<Mutex<Allowed>> = LazyLock::new(|| Mutex::new(Allowed::default()));

fn pack_root() -> Result<PathBuf, String> {
    Ok(data_root()?.join("sfx"))
}

fn has_audio_extension(path: &Path) -> bool {
    path.extension().and_then(|extension| extension.to_str())
        .is_some_and(|extension| AUDIO_EXTENSIONS.contains(&extension.to_ascii_lowercase().as_str()))
}

/// A sound id from the catalog: lower-case letters, digits and dashes.
pub(crate) fn valid_sound_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 40 && !id.starts_with('-') && id.bytes().all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
}

/// How many sounds an installed pack folder holds, when its marker matches the pinned pack.
fn installed_count(dir: &Path) -> Option<usize> {
    let marker: Value = serde_json::from_slice(&fs::read(dir.join(INSTALLED_FILE)).ok()?).ok()?;
    (marker["sha256"] == PACK_SHA256 && dir.join(CATALOG_FILE).is_file()).then_some(())?;
    marker["count"].as_u64().and_then(|count| usize::try_from(count).ok())
}

fn status_value(install: &Install, count: Option<usize>) -> Value {
    json!({"installed": count.is_some(), "version": if count.is_some() { PACK_VERSION } else { "" }, "count": count.unwrap_or(0),
        "size_bytes": PACK_BYTES, "state": install.state, "received": install.received, "error": install.error})
}

/// Whether the pack is installed (and how many sounds), or how its download goes.
#[tauri::command]
pub fn sfx_status() -> Result<Value, String> {
    let count = installed_count(&pack_root()?.join(PACK_VERSION));
    let install = INSTALL.lock().map_err(|_| fail("Hãy mở lại ứng dụng"))?.clone();
    Ok(status_value(&install, count))
}

/// Start downloading the pack (no-op when installed or already downloading); poll `sfx_status`.
#[tauri::command]
pub fn sfx_install() -> Result<Value, String> {
    let root = pack_root()?;
    let count = installed_count(&root.join(PACK_VERSION));
    let mut install = INSTALL.lock().map_err(|_| fail("Hãy mở lại ứng dụng"))?;
    if count.is_some() || install.state == "downloading" || install.state == "installing" {
        return Ok(status_value(&install, count));
    }
    *install = Install { state: "downloading", received: 0, error: None };
    let started = status_value(&install, None);
    drop(install);
    std::thread::spawn(move || {
        let result = download_and_install(&root);
        if let Ok(mut install) = INSTALL.lock() {
            *install = match result {
                Ok(()) => Install { state: "idle", received: PACK_BYTES, error: None },
                Err(error) => Install { state: "failed", received: 0, error: Some(error) },
            };
        }
    });
    Ok(started)
}

fn set_progress(state: &'static str, received: u64) {
    if let Ok(mut install) = INSTALL.lock() {
        install.state = state;
        install.received = received;
    }
}

fn download_and_install(root: &Path) -> Result<(), String> {
    fs::create_dir_all(root).map_err(|error| fail(format!("Không tạo được thư mục kho âm thanh: {error}")))?;
    let archive = root.join("dubflow-sfx-v1.zip.partial");
    let _ = fs::remove_file(&archive);
    let downloaded = download(&archive);
    let bytes = downloaded.and_then(|()| fs::read(&archive).map_err(|_| fail("Không đọc được kho âm thanh đã tải")));
    let _ = fs::remove_file(&archive);
    let bytes = bytes?;
    check_pack(&bytes)?;
    set_progress("installing", PACK_BYTES);
    install_pack(&bytes, root)
}

/// The pinned asset, streamed by the bundled Python into `dest`.
fn download(dest: &Path) -> Result<(), String> {
    let root = version_root()?;
    let python = root.join("runtime").join("python.exe");
    if !python.is_file() {
        return Err(fail("Bản DubFlow này không có Python đi kèm để tải kho âm thanh"));
    }
    let mut command = Command::new(python);
    command.current_dir(root.join("app")).args(["-B", "-c", DOWNLOAD_SCRIPT, PACK_URL]).arg(dest).arg(PACK_BYTES.to_string())
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let mut child = command.spawn().map_err(|error| fail(format!("Không chạy được trình tải: {error}")))?;
    let mut done = false;
    if let Some(stdout) = child.stdout.take() {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let Ok(value) = serde_json::from_str::<Value>(&line) else { continue };
            if let Some(received) = value["received"].as_u64() {
                set_progress("downloading", received.min(PACK_BYTES));
            }
            done |= value["done"].is_u64();
        }
    }
    let status = child.wait().map_err(|_| fail("Trình tải dừng bất thường"))?;
    if !status.success() || !done {
        return Err(fail("Không tải được kho âm thanh. Kiểm tra mạng rồi thử lại."));
    }
    Ok(())
}

fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    format!("{:x}", Sha256::digest(bytes))
}

fn check_pack(bytes: &[u8]) -> Result<(), String> {
    if bytes.len() as u64 != PACK_BYTES || sha256_hex(bytes) != PACK_SHA256 {
        return Err(fail("Kho âm thanh tải về bị lỗi (không khớp mã kiểm tra). Hãy thử lại."));
    }
    Ok(())
}

// ---------- The archive: a stored (uncompressed) zip ----------

fn crc32(bytes: &[u8]) -> u32 {
    let mut crc = 0xFFFF_FFFFu32;
    for &byte in bytes {
        crc ^= u32::from(byte);
        for _ in 0..8 {
            crc = if crc & 1 == 1 { (crc >> 1) ^ 0xEDB8_8320 } else { crc >> 1 };
        }
    }
    !crc
}

fn le16(bytes: &[u8], at: usize) -> Option<usize> {
    bytes.get(at..at + 2).map(|b| usize::from(u16::from_le_bytes([b[0], b[1]])))
}

fn le32(bytes: &[u8], at: usize) -> Option<u32> {
    bytes.get(at..at + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
}

/// Every entry of a zip whose entries are all stored and unencrypted, with
/// its data checked against its CRC; anything else is refused.
fn read_zip(bytes: &[u8]) -> Result<Vec<(String, &[u8])>, String> {
    let corrupt = || fail("Kho âm thanh không đúng định dạng");
    let end = (0..bytes.len().saturating_sub(21)).rev().take(65_557)
        .find(|&at| le32(bytes, at) == Some(0x0605_4b50)).ok_or_else(corrupt)?;
    let count = le16(bytes, end + 10).ok_or_else(corrupt)?;
    let mut at = le32(bytes, end + 16).ok_or_else(corrupt)? as usize;
    let mut entries = Vec::with_capacity(count);
    for _ in 0..count {
        if le32(bytes, at) != Some(0x0201_4b50) {
            return Err(corrupt());
        }
        let flags = le16(bytes, at + 8).ok_or_else(corrupt)?;
        let method = le16(bytes, at + 10).ok_or_else(corrupt)?;
        let crc = le32(bytes, at + 16).ok_or_else(corrupt)?;
        let size = le32(bytes, at + 20).ok_or_else(corrupt)? as usize;
        let plain = le32(bytes, at + 24).ok_or_else(corrupt)? as usize;
        let (name_len, extra_len, comment_len) = (le16(bytes, at + 28).ok_or_else(corrupt)?, le16(bytes, at + 30).ok_or_else(corrupt)?,
            le16(bytes, at + 32).ok_or_else(corrupt)?);
        let local = le32(bytes, at + 42).ok_or_else(corrupt)? as usize;
        let name = bytes.get(at + 46..at + 46 + name_len).and_then(|name| std::str::from_utf8(name).ok()).ok_or_else(corrupt)?;
        if flags & 1 != 0 || method != 0 || size != plain {
            return Err(corrupt());
        }
        if le32(bytes, local) != Some(0x0403_4b50) {
            return Err(corrupt());
        }
        let start = local + 30 + le16(bytes, local + 26).ok_or_else(corrupt)? + le16(bytes, local + 28).ok_or_else(corrupt)?;
        let data = bytes.get(start..start.checked_add(size).ok_or_else(corrupt)?).ok_or_else(corrupt)?;
        if crc32(data) != crc {
            return Err(corrupt());
        }
        entries.push((name.to_owned(), data));
        at += 46 + name_len + extra_len + comment_len;
    }
    Ok(entries)
}

/// A license or credit file name kept from the archive.
fn note_name(name: &str) -> bool {
    name.len() <= 80 && name.ends_with(".txt") && name.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"-_.".contains(&byte)) && !name.starts_with('.')
}

/// Write the catalog, its sounds and the license notes into `<root>\v1`
/// (built beside it, then renamed); entries the catalog does not list are
/// ignored and names are never used as paths as-is.
fn install_pack(bytes: &[u8], root: &Path) -> Result<(), String> {
    let entries = read_zip(bytes)?;
    let find = |name: &str| entries.iter().find(|(entry, _)| entry == name).map(|(_, data)| *data);
    let catalog_bytes = find(CATALOG_FILE).ok_or_else(|| fail("Kho âm thanh thiếu danh mục"))?;
    let catalog: Value = serde_json::from_slice(catalog_bytes).map_err(|_| fail("Danh mục kho âm thanh không hợp lệ"))?;
    let sounds = catalog["sounds"].as_array().filter(|sounds| catalog["schema_version"] == 1 && !sounds.is_empty() && sounds.len() <= MAX_SOUNDS)
        .ok_or_else(|| fail("Danh mục kho âm thanh không hợp lệ"))?;
    let staging = root.join(format!("{PACK_VERSION}.partial"));
    let _ = fs::remove_dir_all(&staging);
    let write = |relative: &Path, data: &[u8]| -> Result<(), String> {
        let path = staging.join(relative);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|error| fail(format!("Không ghi được kho âm thanh: {error}")))?;
        }
        fs::write(&path, data).map_err(|error| fail(format!("Không ghi được kho âm thanh: {error}")))
    };
    let mut seen = HashSet::new();
    for sound in sounds {
        let id = sound["id"].as_str().filter(|id| valid_sound_id(id) && seen.insert(id.to_string()))
            .ok_or_else(|| fail("Danh mục kho âm thanh có mã không hợp lệ"))?;
        if sound["file"].as_str() != Some(&format!("{id}.ogg")) {
            return Err(fail("Danh mục kho âm thanh có tên file không hợp lệ"));
        }
        let data = find(&format!("sounds/{id}.ogg")).ok_or_else(|| fail("Kho âm thanh thiếu file"))?;
        write(&Path::new("sounds").join(format!("{id}.ogg")), data)?;
    }
    for (name, data) in &entries {
        if name == "CREDITS.txt" {
            write(Path::new("CREDITS.txt"), data)?;
        } else if let Some(note) = name.strip_prefix("licenses/").filter(|note| note_name(note)) {
            write(&Path::new("licenses").join(note), data)?;
        }
    }
    write(Path::new(CATALOG_FILE), catalog_bytes)?;
    write(Path::new(INSTALLED_FILE), &serde_json::to_vec(&json!({"sha256": PACK_SHA256, "count": seen.len(), "url": PACK_URL}))
        .map_err(|error| fail(error.to_string()))?)?;
    let target = root.join(PACK_VERSION);
    if target.exists() {
        fs::remove_dir_all(&target).map_err(|error| fail(format!("Không thay được kho âm thanh cũ: {error}")))?;
    }
    fs::rename(&staging, &target).map_err(|error| fail(format!("Không cài được kho âm thanh: {error}")))
}

/// The installed file of a sound effect.
pub(crate) fn sound_file(id: &str) -> Result<PathBuf, String> {
    let dir = pack_root()?.join(PACK_VERSION);
    if !valid_sound_id(id) || installed_count(&dir).is_none() {
        return Err(fail("Hãy tải kho hiệu ứng âm thanh trước (Cài đặt hoặc chế độ Âm thanh)"));
    }
    let file = dir.join("sounds").join(format!("{id}.ogg"));
    if file.is_file() { Ok(file) } else { Err(fail("Không tìm thấy hiệu ứng âm thanh trong kho")) }
}

/// Where a sound effect is, for preview; None when the pack is not installed.
#[tauri::command]
pub fn sfx_path(id: String) -> Option<String> {
    sound_file(&id).ok().map(|file| display_path(&file))
}

// ---------- The user's audio ----------

/// The canonical path of an existing local audio file.
pub(crate) fn audio_file(value: &str) -> Result<PathBuf, String> {
    let path = Path::new(value);
    if !path.is_absolute() || !has_audio_extension(path) {
        return Err(fail("Chỉ dùng được file âm thanh MP3, WAV, OGG, M4A, AAC hoặc FLAC trên máy"));
    }
    let resolved = path.canonicalize().map_err(|_| fail("Không tìm thấy file âm thanh"))?;
    if !resolved.is_file() || !has_audio_extension(&resolved) {
        return Err(fail("Không tìm thấy file âm thanh"));
    }
    Ok(resolved)
}

/// Remember files the user picked, dropped or saved in a draft: the preview may play them.
pub(crate) fn allow_files(files: &[PathBuf]) {
    if let Ok(mut allowed) = ALLOWED.lock() {
        for file in files.iter().take(MAX_PICKED * 10) {
            allowed.files.insert(file.clone());
        }
    }
}

fn allow_folder(folder: &Path) {
    if let Ok(mut allowed) = ALLOWED.lock() {
        if !allowed.folders.iter().any(|known| known == folder) {
            allowed.folders.push(folder.to_path_buf());
        }
    }
}

/// An audio file the preview may stream: from the pack, a folder the user
/// listed, or one the user picked or dropped.
pub(crate) fn playable_audio(value: &str) -> Result<PathBuf, String> {
    let file = audio_file(value)?;
    let pack = pack_root().ok().and_then(|root| root.join(PACK_VERSION).canonicalize().ok());
    let allowed = ALLOWED.lock().map_err(|_| fail("Hãy mở lại ứng dụng"))?;
    let ok = pack.is_some_and(|pack| file.starts_with(pack)) || allowed.files.contains(&file)
        || allowed.folders.iter().any(|folder| file.starts_with(folder));
    if ok { Ok(file) } else { Err(fail("Chỉ phát được âm thanh trong kho DubFlow, thư mục bạn chọn hoặc file bạn đã thêm")) }
}

fn scan_audio(dir: &Path, depth: usize, found: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(dir) else { return };
    let mut entries: Vec<PathBuf> = entries.filter_map(|entry| entry.ok().map(|entry| entry.path())).collect();
    entries.sort();
    for path in entries {
        if found.len() >= MAX_USER_FILES {
            return;
        }
        let Ok(metadata) = fs::symlink_metadata(&path) else { continue };
        if metadata.is_file() && has_audio_extension(&path) {
            found.push(path);
        } else if metadata.is_dir() && depth < 3 && path.file_name().is_some_and(|name| !name.to_string_lossy().starts_with('.')) {
            scan_audio(&path, depth + 1, found);
        }
    }
}

/// The audio files in the user's sound folder (three levels deep, at most
/// 2000); durations come later from `audio_info`.
#[tauri::command]
pub async fn user_audio_list(folder: String) -> Result<Vec<Value>, String> {
    let path = Path::new(&folder);
    if !path.is_absolute() {
        return Err(fail("Hãy chọn thư mục trên máy"));
    }
    let resolved = path.canonicalize().map_err(|_| fail("Không tìm thấy thư mục"))?;
    if !resolved.is_dir() {
        return Err(fail("Không tìm thấy thư mục"));
    }
    allow_folder(&resolved);
    tauri::async_runtime::spawn_blocking(move || {
        let mut found = Vec::new();
        scan_audio(&resolved, 1, &mut found);
        found.into_iter().map(|file| json!({
            "path": display_path(&file),
            "name": file.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default(),
            "size_bytes": fs::metadata(&file).map_or(0, |metadata| metadata.len()),
        })).collect()
    }).await.map_err(|error| fail(error.to_string()))
}

/// Seconds of audio in FFprobe's JSON; None without an audio stream.
fn parse_audio_probe(text: &str) -> Option<f64> {
    let value: Value = serde_json::from_str(text).ok()?;
    value["streams"].as_array()?.iter().any(|stream| stream["codec_type"] == "audio").then_some(())?;
    let duration = &value["format"]["duration"];
    duration.as_str().and_then(|text| text.parse::<f64>().ok()).or_else(|| duration.as_f64()).filter(|seconds| seconds.is_finite() && *seconds > 0.0)
}

/// Length of an audio file, by FFprobe.
pub(crate) fn audio_seconds(file: &Path) -> Result<f64, String> {
    let mut command = Command::new(crate::edit::media_tool("ffprobe.exe")?);
    command.args(["-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json"]).arg(file)
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let output = command.output().map_err(|error| fail(format!("Không chạy được FFprobe: {error}")))?;
    String::from_utf8(output.stdout).ok().as_deref().and_then(parse_audio_probe).ok_or_else(|| fail("Không đọc được thời lượng âm thanh"))
}

/// Length and title of an audio file.
#[tauri::command]
pub async fn audio_info(path: String) -> Result<Value, String> {
    let file = audio_file(&path)?;
    let probed = file.clone();
    let duration = tauri::async_runtime::spawn_blocking(move || audio_seconds(&probed)).await.map_err(|error| fail(error.to_string()))??;
    let title = file.file_stem().map(|stem| stem.to_string_lossy().into_owned()).unwrap_or_default();
    Ok(json!({"path": path, "title": title, "duration": duration, "size_bytes": fs::metadata(&file).map_or(0, |metadata| metadata.len())}))
}

#[tauri::command]
pub fn pick_audio_folder() -> Option<String> {
    let folder = rfd::FileDialog::new().set_title("Chọn thư mục âm thanh của bạn").pick_folder()?;
    if let Ok(resolved) = folder.canonicalize() {
        allow_folder(&resolved);
    }
    Some(folder.to_string_lossy().into_owned())
}

#[tauri::command]
pub fn pick_audio_files() -> Vec<String> {
    let files = rfd::FileDialog::new().set_title("Chọn file âm thanh").add_filter("Âm thanh", &AUDIO_EXTENSIONS).pick_files().unwrap_or_default();
    let resolved: Vec<PathBuf> = files.iter().take(MAX_PICKED).filter_map(|file| audio_file(&file.to_string_lossy()).ok()).collect();
    allow_files(&resolved);
    files.into_iter().take(MAX_PICKED).filter(|file| has_audio_extension(file)).map(|file| file.to_string_lossy().into_owned()).collect()
}

/// Dropped paths -> existing audio files (folders are not searched), which the preview may then play.
#[tauri::command]
pub fn expand_audio_paths(paths: Vec<String>) -> Vec<String> {
    let mut kept = Vec::new();
    let mut resolved = Vec::new();
    for path in paths.into_iter().take(MAX_PICKED) {
        if let Ok(file) = audio_file(&path) {
            resolved.push(file);
            kept.push(path);
        }
    }
    allow_files(&resolved);
    kept
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A stored zip, as the pack builder writes it.
    fn zip(entries: &[(&str, &[u8])], method: u16) -> Vec<u8> {
        let mut out = Vec::new();
        let mut central = Vec::new();
        for (name, data) in entries {
            let offset = out.len() as u32;
            let crc = crc32(data);
            let header = |sig: u32, central_dir: bool| {
                let mut h = Vec::new();
                h.extend(sig.to_le_bytes());
                if central_dir {
                    h.extend(20u16.to_le_bytes());
                }
                h.extend(20u16.to_le_bytes());
                h.extend(0u16.to_le_bytes());
                h.extend(method.to_le_bytes());
                h.extend([0u8; 4]);
                h.extend(crc.to_le_bytes());
                h.extend((data.len() as u32).to_le_bytes());
                h.extend((data.len() as u32).to_le_bytes());
                h.extend((name.len() as u16).to_le_bytes());
                h.extend(0u16.to_le_bytes());
                if central_dir {
                    // Comment length, disk, internal and external attributes.
                    h.extend([0u8; 10]);
                    h.extend(offset.to_le_bytes());
                }
                h.extend(name.as_bytes());
                h
            };
            let local = header(0x0403_4b50, false);
            central.extend(header(0x0201_4b50, true));
            out.extend(local);
            out.extend(*data);
        }
        let start = out.len() as u32;
        out.extend(&central);
        out.extend(0x0605_4b50u32.to_le_bytes());
        out.extend([0u8; 4]);
        out.extend((entries.len() as u16).to_le_bytes());
        out.extend((entries.len() as u16).to_le_bytes());
        out.extend((central.len() as u32).to_le_bytes());
        out.extend(start.to_le_bytes());
        out.extend(0u16.to_le_bytes());
        out
    }

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("dubflow-sfx-test-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn crc_matches_the_zip_standard() {
        assert_eq!(crc32(b"123456789"), 0xCBF4_3926);
        assert_eq!(crc32(b""), 0);
    }

    #[test]
    fn installs_only_catalog_sounds_and_notes() {
        let catalog = br#"{"schema_version": 1, "pack": "dubflow-sfx-v1", "sounds": [
            {"id": "whoosh-01", "file": "whoosh-01.ogg"}, {"id": "pop-01", "file": "pop-01.ogg"}]}"#;
        let bytes = zip(&[("sfx-catalog.json", catalog), ("CREDITS.txt", b"Kenney CC0"), ("licenses/ui-audio-License.txt", b"CC0"),
            ("sounds/whoosh-01.ogg", b"OggS-1"), ("sounds/pop-01.ogg", b"OggS-2"), ("sounds/extra.ogg", b"nope"),
            ("../evil.txt", b"x"), ("licenses/../../evil.txt", b"x")], 0);
        let root = temp("install");
        install_pack(&bytes, &root).unwrap();
        let dir = root.join(PACK_VERSION);
        assert_eq!(fs::read(dir.join("sounds").join("whoosh-01.ogg")).unwrap(), b"OggS-1");
        assert_eq!(fs::read(dir.join("sounds").join("pop-01.ogg")).unwrap(), b"OggS-2");
        assert!(!dir.join("sounds").join("extra.ogg").exists());
        assert!(dir.join("licenses").join("ui-audio-License.txt").is_file() && dir.join("CREDITS.txt").is_file());
        assert!(!root.join("evil.txt").exists() && !dir.join("evil.txt").exists());
        assert_eq!(installed_count(&dir), Some(2));
        assert!(!root.join(format!("{PACK_VERSION}.partial")).exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn refuses_bad_archives_and_ids() {
        let catalog = br#"{"schema_version": 1, "sounds": [{"id": "../x", "file": "../x.ogg"}]}"#;
        let root = temp("refuse");
        assert!(install_pack(&zip(&[("sfx-catalog.json", catalog)], 0), &root).is_err());
        let missing = br#"{"schema_version": 1, "sounds": [{"id": "pop-01", "file": "pop-01.ogg"}]}"#;
        assert!(install_pack(&zip(&[("sfx-catalog.json", missing)], 0), &root).is_err(), "a listed sound must be in the archive");
        assert!(read_zip(&zip(&[("a.txt", b"x")], 8)).is_err(), "deflated entries are refused");
        let mut corrupt = zip(&[("a.txt", b"hello")], 0);
        let at = corrupt.windows(5).position(|window| window == b"hello").unwrap();
        corrupt[at] = b'j';
        assert!(read_zip(&corrupt).is_err(), "CRC mismatch");
        assert!(read_zip(b"not a zip").is_err());
        assert!(!root.join(PACK_VERSION).exists());
        fs::remove_dir_all(root).unwrap();
        assert!(valid_sound_id("whoosh-01") && !valid_sound_id("Whoosh") && !valid_sound_id("a/b") && !valid_sound_id("") && !valid_sound_id("-a"));
        assert!(check_pack(b"tiny").is_err());
    }

    #[test]
    fn reads_audio_files_and_their_length() {
        assert_eq!(parse_audio_probe(r#"{"streams":[{"codec_type":"audio"}],"format":{"duration":"1.250000"}}"#), Some(1.25));
        assert_eq!(parse_audio_probe(r#"{"streams":[{"codec_type":"video"}],"format":{"duration":"3"}}"#), None);
        assert_eq!(parse_audio_probe(r#"{"streams":[{"codec_type":"audio"}],"format":{}}"#), None);
        let dir = temp("files");
        let sound = dir.join("tiếng cười.MP3");
        fs::write(&sound, b"x").unwrap();
        fs::write(dir.join("clip.mp4"), b"x").unwrap();
        fs::create_dir_all(dir.join("Nhạc").join("Vui")).unwrap();
        fs::write(dir.join("Nhạc").join("Vui").join("a.ogg"), b"x").unwrap();
        assert!(audio_file(&sound.to_string_lossy()).is_ok());
        assert!(audio_file(&dir.join("clip.mp4").to_string_lossy()).is_err());
        assert!(audio_file("relative.mp3").is_err());
        let mut found = Vec::new();
        scan_audio(&dir, 1, &mut found);
        assert_eq!(found.len(), 2);
        let file = sound.canonicalize().unwrap();
        assert!(playable_audio(&sound.to_string_lossy()).is_err(), "not chosen yet");
        allow_files(&[file]);
        assert!(playable_audio(&sound.to_string_lossy()).is_ok());
        let other = dir.join("Nhạc").join("Vui").join("a.ogg");
        assert!(playable_audio(&other.to_string_lossy()).is_err());
        allow_folder(&dir.canonicalize().unwrap());
        assert!(playable_audio(&other.to_string_lossy()).is_ok());
        assert_eq!(expand_audio_paths(vec![sound.to_string_lossy().into_owned(), dir.to_string_lossy().into_owned(), "x.mp3".into()]).len(), 1);
        fs::remove_dir_all(dir).unwrap();
    }
}
