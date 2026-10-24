//! Video links: drive the supervisor's `source-serve` (scan -> items ->
//! download) and hand back a verified local media file for the queue.
//!
//! One long-lived service child is the single writer of the source store.
//! A reader thread advances each link through its steps; the webview polls
//! `fetch_status` and adds the finished file as an ordinary local job.

use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;

#[derive(Clone, Debug, Serialize)]
pub struct FetchStatus {
    pub scan_id: String,
    pub url: String,
    pub provider: String,
    /// preparing | downloading | done | failed
    pub state: String,
    pub downloaded_bytes: u64,
    pub total_bytes: Option<u64>,
    pub media_path: Option<String>,
    pub error: Option<String>,
}

struct Service {
    child: Child,
    stdin: ChildStdin,
}

#[derive(Default)]
pub struct Sources {
    service: Mutex<Option<Service>>,
    fetches: Arc<Mutex<HashMap<String, FetchStatus>>>,
}

fn fail(message: impl Into<String>) -> String {
    message.into().chars().take(1024).collect()
}

/// Provider by host; anything else is a direct public media URL.
pub fn provider_for(url: &str) -> Result<&'static str, String> {
    let rest = url.strip_prefix("https://").or_else(|| url.strip_prefix("http://"))
        .ok_or_else(|| fail("Link phải bắt đầu bằng https://"))?;
    if url.len() > 2048 || url.chars().any(|c| c.is_control() || c.is_whitespace()) {
        return Err(fail("Link không hợp lệ"));
    }
    let host = rest.split(['/', '?', '#']).next().unwrap_or_default().rsplit('@').next().unwrap_or_default()
        .split(':').next().unwrap_or_default().to_ascii_lowercase();
    let on = |domain: &str| host == domain || host.ends_with(&format!(".{domain}"));
    Ok(if on("bilibili.com") || on("b23.tv") { "bilibili" } else if on("douyin.com") || on("iesdouyin.com") { "douyin" } else { "generic" })
}

fn scan_id(url: &str) -> String {
    let digest = Sha256::digest(format!("{url}\n{}", std::process::id()).as_bytes());
    let mut id = String::from("link-");
    for byte in &digest[..12] {
        id.push_str(&format!("{byte:02x}"));
    }
    id
}

fn human_error(code: &str, provider: &str) -> String {
    match code {
        "AUTH_REQUIRED" | "PRIVATE" if provider == "douyin" => "Douyin yêu cầu đăng nhập/cookie; hiện chưa hỗ trợ. Hãy tải video về máy rồi thêm file.".into(),
        "AUTH_REQUIRED" | "PRIVATE" => "Video yêu cầu đăng nhập hoặc là video riêng tư.".into(),
        "RATE_LIMITED" => "Trang nguồn đang giới hạn truy cập. Hãy thử lại sau ít phút.".into(),
        "NOT_FOUND" => "Không tìm thấy video (có thể đã bị xóa).".into(),
        "NETWORK" => "Không tải được video từ link này (lỗi mạng hoặc trang không hỗ trợ).".into(),
        "UNSUPPORTED" => "Link này chưa được hỗ trợ.".into(),
        "INVALID_INPUT" => "Link không hợp lệ.".into(),
        other => format!("Tải link thất bại ({other})."),
    }
}

fn manifest_sha256(root: &Path) -> Result<String, String> {
    let raw = fs::read(root.join("release-manifest.json"))
        .map_err(|_| fail("Bản cài đặt thiếu release-manifest.json; không thể tải link"))?;
    Ok(Sha256::digest(&raw).iter().map(|byte| format!("{byte:02x}")).collect())
}

impl Sources {
    fn send(&self, value: Value) -> Result<(), String> {
        let mut guard = self.service.lock().map_err(|_| fail("source service lock poisoned"))?;
        let service = guard.as_mut().ok_or_else(|| fail("Dịch vụ tải link chưa chạy"))?;
        writeln!(service.stdin, "{value}").and_then(|_| service.stdin.flush())
            .map_err(|error| fail(format!("Dịch vụ tải link đã dừng: {error}")))
    }

    fn ensure_started(self: &Arc<Self>, supervisor: &Path, root: &Path, data: &Path) -> Result<(), String> {
        let mut guard = self.service.lock().map_err(|_| fail("source service lock poisoned"))?;
        if let Some(service) = guard.as_mut() {
            if matches!(service.child.try_wait(), Ok(None)) {
                return Ok(());
            }
        }
        let manifest = manifest_sha256(root)?;
        let mut command = Command::new(supervisor);
        command.args(["source-serve", "--root"]).arg(root).arg("--data-root").arg(data)
            .args(["--manifest-sha256", &manifest])
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command.spawn().map_err(|error| fail(format!("Không khởi động được dịch vụ tải link: {error}")))?;
        let stdin = child.stdin.take().ok_or_else(|| fail("source service has no stdin"))?;
        let stdout = child.stdout.take().ok_or_else(|| fail("source service has no stdout"))?;
        *guard = Some(Service { child, stdin });
        drop(guard);
        let this = Arc::clone(self);
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                if let Ok(event) = serde_json::from_str::<Value>(&line) {
                    this.on_event(&event);
                }
            }
            // Service exited: anything still in flight cannot finish.
            if let Ok(mut fetches) = this.fetches.lock() {
                for status in fetches.values_mut().filter(|s| s.state == "preparing" || s.state == "downloading") {
                    status.state = "failed".into();
                    status.error = Some("Dịch vụ tải link đã dừng giữa chừng. Hãy thử lại.".into());
                }
            }
        });
        Ok(())
    }

    fn update(&self, scan: &str, apply: impl FnOnce(&mut FetchStatus)) -> Option<FetchStatus> {
        let mut fetches = self.fetches.lock().ok()?;
        let status = fetches.get_mut(scan)?;
        apply(status);
        Some(status.clone())
    }

    fn on_event(&self, event: &Value) {
        let Some(scan) = event["scan_id"].as_str() else { return };
        match event["event"].as_str().unwrap_or_default() {
            "source_status" if event["status"] == "completed" && event["discovered_count"].as_u64().unwrap_or(0) > 0 => {
                if self.update(scan, |_| ()).is_some_and(|s| s.state == "preparing") {
                    let _ = self.send(json!({"command":"items", "scan_id":scan, "offset":0, "limit":1}));
                }
            }
            "source_status" if event["status"] == "completed" => {
                self.update(scan, |s| { s.state = "failed".into(); s.error = Some("Link không chứa video nào.".into()); });
            }
            "source_items" => {
                let identity = event["items"][0]["identity_key"].as_str().map(str::to_owned);
                let started = self.update(scan, |s| if s.state == "preparing" { s.state = "downloading".into(); });
                if let (Some(identity), Some(_)) = (identity, started) {
                    let _ = self.send(json!({"command":"download", "scan_id":scan, "identity_key":identity, "resume":false}));
                }
            }
            "source_download_progress" => {
                self.update(scan, |s| {
                    s.downloaded_bytes = event["downloaded_bytes"].as_u64().unwrap_or(s.downloaded_bytes);
                    s.total_bytes = event["total_bytes"].as_u64().or(s.total_bytes);
                });
            }
            "source_downloaded" => {
                self.update(scan, |s| {
                    s.state = "done".into();
                    s.media_path = event["media_path"].as_str().map(str::to_owned);
                    s.downloaded_bytes = event["size_bytes"].as_u64().unwrap_or(s.downloaded_bytes);
                    s.total_bytes = Some(s.downloaded_bytes);
                });
            }
            "source_error" | "source_download_failed" | "source_download_cancelled" => {
                let code = event["code"].as_str().unwrap_or("SOURCE_FAILED").to_owned();
                self.update(scan, |s| { s.state = "failed".into(); s.error = Some(human_error(&code, &s.provider)); });
            }
            _ => {}
        }
    }

    pub fn fetch(self: &Arc<Self>, url: String, supervisor: &Path, root: &Path, data: &Path) -> Result<FetchStatus, String> {
        let provider = provider_for(&url)?;
        self.ensure_started(supervisor, root, data)?;
        let scan = scan_id(&url);
        let status = FetchStatus { scan_id: scan.clone(), url: url.clone(), provider: provider.into(), state: "preparing".into(),
            downloaded_bytes: 0, total_bytes: None, media_path: None, error: None };
        self.fetches.lock().map_err(|_| fail("fetch table lock poisoned"))?.insert(scan.clone(), status.clone());
        self.send(json!({"command":"start_video", "scan_id":scan, "provider_id":provider, "source_ref":url}))?;
        Ok(status)
    }

    pub fn status(&self, scan: &str) -> Option<FetchStatus> {
        self.fetches.lock().ok()?.get(scan).cloned()
    }

    pub fn shutdown(&self) {
        if let Ok(mut guard) = self.service.lock() {
            if let Some(mut service) = guard.take() {
                let _ = writeln!(service.stdin, "{}", json!({"command":"shutdown"}));
                let _ = service.stdin.flush();
                drop(service.stdin);
                let _ = service.child.wait();
            }
        }
    }
}

/// `Videos\DubFlow Output\<name> - vi\<scan>` so finished link jobs are easy to find.
pub fn link_output_dir(url: &str, scan: &str) -> Result<PathBuf, String> {
    let profile = std::env::var_os("USERPROFILE").ok_or_else(|| fail("USERPROFILE is unavailable"))?;
    let tail = url.split(['?', '#']).next().unwrap_or(url).trim_end_matches('/').rsplit('/').next().unwrap_or("video");
    let stem = tail.rsplit_once('.').map(|(stem, _)| stem).unwrap_or(tail);
    let name: String = stem.chars().map(|c| if c.is_alphanumeric() || c == '-' || c == '_' { c } else { '-' }).take(60).collect();
    let name = if name.trim_matches('-').is_empty() { "video".to_owned() } else { name };
    Ok(PathBuf::from(profile).join("Videos").join("DubFlow Output").join(format!("{name} - vi")).join(scan))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn providers_follow_the_host_not_the_path() {
        assert_eq!(provider_for("https://www.bilibili.com/video/BV1U3n46GEzY").unwrap(), "bilibili");
        assert_eq!(provider_for("https://b23.tv/abc").unwrap(), "bilibili");
        assert_eq!(provider_for("https://v.douyin.com/abc/").unwrap(), "douyin");
        assert_eq!(provider_for("https://archive.org/download/x/bilibili.com.mp4").unwrap(), "generic");
        assert_eq!(provider_for("https://evilbilibili.com/video/x").unwrap(), "generic");
        assert_eq!(provider_for("https://user@www.bilibili.com:443/video/x").unwrap(), "bilibili");
        assert!(provider_for("ftp://example.com/a.mp4").is_err());
        assert!(provider_for("https://example.com/a b.mp4").is_err());
    }

    /// Real service run against a release-shaped root:
    /// `DUBFLOW_DEV_ROOT=<root> DUBFLOW_DEV_DATA=<data> cargo test -- --ignored`
    #[test]
    #[ignore]
    fn real_link_downloads_through_the_supervisor_service() {
        let (Some(root), Some(data)) = (std::env::var_os("DUBFLOW_DEV_ROOT"), std::env::var_os("DUBFLOW_DEV_DATA")) else { return };
        let root = PathBuf::from(root);
        let sources = Arc::new(Sources::default());
        let url = std::env::var("DUBFLOW_DEV_URL").unwrap_or_else(|_| "https://archive.org/download/ElephantsDream/ed_1024_512kb.mp4".into());
        let started = sources.fetch(url, &root.join("app/bin/dubflow-supervisor.exe"), &root, Path::new(&data)).unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(900);
        let status = loop {
            let status = sources.status(&started.scan_id).unwrap();
            if status.state == "done" || status.state == "failed" || std::time::Instant::now() > deadline {
                break status;
            }
            thread::sleep(std::time::Duration::from_secs(2));
        };
        sources.shutdown();
        assert_eq!(status.state, "done", "{status:?}");
        assert!(Path::new(status.media_path.as_deref().unwrap()).is_file());
    }

    #[test]
    fn link_output_lives_under_dubflow_output_with_a_safe_name() {
        let dir = link_output_dir("https://archive.org/download/ElephantsDream/ed_1024_512kb.mp4?x=1", "link-1").unwrap();
        assert!(dir.ends_with(Path::new("DubFlow Output").join("ed_1024_512kb - vi").join("link-1")));
        let odd = link_output_dir("https://www.bilibili.com/video/", "link-2").unwrap();
        assert!(odd.to_string_lossy().contains("video - vi"));
        assert!(!link_output_dir("https://x.test/..%2f..", "s").unwrap().to_string_lossy().contains(".."));
    }
}
