//! Video links: drive the supervisor's `source-serve` (scan -> items ->
//! download) and hand back a verified local media file for the queue.
//!
//! One long-lived service child is the single writer of the source store and
//! runs one link at a time, so links wait in a queue here and start one after
//! another. A reader thread advances each link through its steps; the webview
//! polls `fetch_status` and adds the finished file as an ordinary local job.

use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, VecDeque};
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;

#[derive(Clone, Debug, Serialize)]
pub struct FetchStatus {
    pub scan_id: String,
    pub url: String,
    pub provider: String,
    /// queued | preparing | downloading | done | failed
    pub state: String,
    /// What a preparing link is doing right now, for the user.
    pub detail: Option<String>,
    /// 1-based place among the links still waiting, while queued.
    pub queue_position: Option<usize>,
    /// When this link left the queue (ms since the Unix epoch).
    pub started_at_ms: Option<u64>,
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
    /// Links waiting for the service, oldest first, and the one it is running.
    queue: Mutex<LinkQueue>,
    /// Supervisor, release root and data root to (re)start the service with.
    launch: Mutex<Option<(PathBuf, PathBuf, PathBuf)>>,
    /// Latest `source_session` / `source_session_error` event.
    session: Mutex<Option<Value>>,
    /// Automatic resumes per link after a dropped connection.
    resumes: Mutex<HashMap<String, Resumes>>,
}

/// The service allows two retries in a row that bring no new bytes; each
/// resumes the part already downloaded.
const MAX_RESUMES: u8 = 2;
/// A drop after at least this much more was downloaded starts a fresh budget,
/// as it does in the service.
const PROGRESS_BYTES: u64 = 1024 * 1024;

#[derive(Default)]
struct Resumes {
    /// Resumes since the download last made progress.
    in_a_row: u8,
    total: u32,
    /// Bytes downloaded when the last resume started.
    from_bytes: u64,
}

#[derive(Default)]
struct LinkQueue {
    waiting: VecDeque<String>,
    current: Option<String>,
}

fn now_ms() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

fn finished(state: &str) -> bool {
    state == "done" || state == "failed"
}

/// Cookies yt-dlp's Douyin extractor needs first; the service accepts one
/// Cookie header of at most 2048 bytes and 64 cookies.
pub const DOUYIN_PRIORITY: [&str; 12] = ["s_v_web_id", "ttwid", "msToken", "__ac_nonce", "__ac_signature", "sessionid",
    "sessionid_ss", "sid_tt", "sid_guard", "uid_tt", "odin_tt", "passport_csrf_token"];
const MAX_COOKIE_HEADER: usize = 2000;
/// The protected session store keeps a session for at most 24 hours.
pub const SESSION_SECONDS: u64 = 23 * 60 * 60;

fn cookie_safe(value: &str) -> bool {
    !value.is_empty() && value.len() <= 1024
        && value.bytes().all(|b| b.is_ascii_graphic() && !matches!(b, b'"' | b',' | b';' | b'\\'))
}

/// Build a bounded `Cookie` header, essential cookies first.
pub fn cookie_header(cookies: &[(String, String)], priority: &[&str]) -> Option<String> {
    let mut ordered: Vec<&(String, String)> = cookies.iter().filter(|(name, value)| cookie_safe(name) && !name.contains('=') && cookie_safe(value)).collect();
    ordered.sort_by_key(|(name, _)| priority.iter().position(|p| p == name).unwrap_or(priority.len()));
    let mut seen = std::collections::HashSet::new();
    let mut header = String::new();
    for (name, value) in ordered {
        if !seen.insert(name.as_str()) || seen.len() > 64 {
            continue;
        }
        let pair = format!("{}{name}={value}", if header.is_empty() { "" } else { "; " });
        if header.len() + pair.len() <= MAX_COOKIE_HEADER {
            header.push_str(&pair);
        }
    }
    (!header.is_empty()).then_some(header)
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

/// A new id for every fetch, so a link that failed can be fetched again in
/// the same session: the service never starts a scan id twice.
fn scan_id(url: &str) -> String {
    static FETCHES: AtomicU64 = AtomicU64::new(0);
    let digest = Sha256::digest(format!("{url}\n{}\n{}", std::process::id(), FETCHES.fetch_add(1, Ordering::Relaxed)).as_bytes());
    let mut id = String::from("link-");
    for byte in &digest[..12] {
        id.push_str(&format!("{byte:02x}"));
    }
    id
}

fn human_error(code: &str, provider: &str) -> String {
    match code {
        "AUTH_REQUIRED" | "PRIVATE" if provider == "douyin" => "Douyin cần phiên truy cập: bấm “Mở Douyin”, đợi trang hiện video, rồi bấm “Lưu phiên & thử lại”.".into(),
        "AUTH_REQUIRED" | "PRIVATE" => "Video yêu cầu đăng nhập hoặc là video riêng tư.".into(),
        "RATE_LIMITED" => "Trang nguồn đang giới hạn truy cập. Hãy thử lại sau ít phút.".into(),
        "NOT_FOUND" => "Không tìm thấy video (có thể đã bị xóa).".into(),
        "NETWORK" => "Không tải được video từ link này (lỗi mạng hoặc trang không hỗ trợ).".into(),
        "UNSUPPORTED" => "Link này chưa được hỗ trợ.".into(),
        "INVALID_INPUT" => "Link không hợp lệ.".into(),
        "SOURCE_REQUEST_REJECTED" => "Dịch vụ tải không nhận link này. Hãy thử lại.".into(),
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
        *self.launch.lock().map_err(|_| fail("source launch lock poisoned"))? =
            Some((supervisor.to_path_buf(), root.to_path_buf(), data.to_path_buf()));
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
            // Service exited: the running link cannot finish; the next one
            // restarts the service.
            if let Ok(mut fetches) = this.fetches.lock() {
                for status in fetches.values_mut().filter(|s| s.state == "preparing" || s.state == "downloading") {
                    status.state = "failed".into();
                    status.detail = None;
                    status.error = Some("Dịch vụ tải link đã dừng giữa chừng. Hãy thử lại.".into());
                }
            }
            this.pump();
        });
        Ok(())
    }

    fn update(&self, scan: &str, apply: impl FnOnce(&mut FetchStatus)) -> Option<FetchStatus> {
        let mut fetches = self.fetches.lock().ok()?;
        let status = fetches.get_mut(scan)?;
        apply(status);
        Some(status.clone())
    }

    fn fail_link(&self, scan: &str, error: String) {
        self.update(scan, |s| if !finished(&s.state) {
            s.state = "failed".into();
            s.detail = None;
            s.error = Some(error);
        });
    }

    fn on_event(self: &Arc<Self>, event: &Value) {
        if matches!(event["event"].as_str(), Some("source_session" | "source_session_error")) {
            if let Ok(mut slot) = self.session.lock() {
                *slot = Some(event.clone());
            }
            return;
        }
        let current = self.queue.lock().ok().and_then(|queue| queue.current.clone())
            .filter(|scan| self.status(scan).is_some_and(|s| s.state == "preparing"
                || s.detail.as_deref().is_some_and(|d| d.starts_with("Mất kết nối"))));
        // A rejected request names no scan. Only the running link sends
        // requests, and only a rejected start or resume leaves it waiting.
        let Some(scan) = event["scan_id"].as_str().map(str::to_owned)
            .or_else(|| if event["event"] == "source_error" { current } else { None }) else { return };
        let scan = scan.as_str();
        match event["event"].as_str().unwrap_or_default() {
            "source_preparing" => {
                self.update(scan, |s| s.detail = Some("Đang mở trang video…".into()));
            }
            "source_status" if event["status"] == "running" => {
                self.update(scan, |s| s.detail = Some("Đang đọc thông tin video…".into()));
            }
            "source_status" if event["status"] == "completed" && event["discovered_count"].as_u64().unwrap_or(0) > 0 => {
                // The service may report completion more than once; ask for the item once.
                const FOUND: &str = "Đã tìm thấy video, chuẩn bị tải…";
                let mut first = false;
                self.update(scan, |s| if s.state == "preparing" && s.detail.as_deref() != Some(FOUND) {
                    s.detail = Some(FOUND.into());
                    first = true;
                });
                if first {
                    let _ = self.send(json!({"command":"items", "scan_id":scan, "offset":0, "limit":1}));
                }
            }
            "source_status" if event["status"] == "completed" => {
                self.fail_link(scan, "Link không chứa video nào.".into());
            }
            "source_items" => {
                let identity = event["items"][0]["identity_key"].as_str().map(str::to_owned);
                let mut started = false;
                self.update(scan, |s| if s.state == "preparing" {
                    s.state = "downloading".into();
                    s.detail = None;
                    started = true;
                });
                if let (Some(identity), true) = (identity, started) {
                    let _ = self.send(json!({"command":"download", "scan_id":scan, "identity_key":identity, "resume":false}));
                }
            }
            "source_download_failed" if self.resume_after_drop(scan, event) => {}
            "source_download_progress" => {
                self.update(scan, |s| {
                    s.detail = None;
                    s.downloaded_bytes = event["downloaded_bytes"].as_u64().unwrap_or(s.downloaded_bytes);
                    s.total_bytes = event["total_bytes"].as_u64().or(s.total_bytes);
                });
            }
            "source_downloaded" => {
                self.update(scan, |s| {
                    s.state = "done".into();
                    s.detail = None;
                    s.media_path = event["media_path"].as_str().map(str::to_owned);
                    s.downloaded_bytes = event["size_bytes"].as_u64().unwrap_or(s.downloaded_bytes);
                    s.total_bytes = Some(s.downloaded_bytes);
                });
            }
            "source_error" | "source_download_failed" | "source_download_cancelled" | "source_retry_refused" => {
                let code = event["code"].as_str().unwrap_or("SOURCE_FAILED").to_owned();
                let provider = self.status(scan).map(|s| s.provider).unwrap_or_default();
                self.fail_link(scan, human_error(&code, &provider));
            }
            _ => {}
        }
        if self.status(scan).is_some_and(|s| finished(&s.state)) {
            self.pump();
        }
    }

    /// A download that lost its connection resumes from the part already on
    /// disk: as long as it keeps making progress, and otherwise up to
    /// `MAX_RESUMES` times in a row. Returns whether a resume was started.
    fn resume_after_drop(self: &Arc<Self>, scan: &str, event: &Value) -> bool {
        let code = event["code"].as_str().unwrap_or_default();
        let Some(identity) = event["identity_key"].as_str().map(str::to_owned) else { return false };
        if event["retryable"] != true || !matches!(code, "NETWORK" | "RATE_LIMITED") {
            return false;
        }
        let Some(bytes) = self.status(scan).map(|s| s.downloaded_bytes) else { return false };
        let (total, in_a_row) = {
            let Ok(mut resumes) = self.resumes.lock() else { return false };
            let resumes = resumes.entry(scan.to_owned()).or_default();
            if bytes >= resumes.from_bytes.saturating_add(PROGRESS_BYTES) {
                resumes.in_a_row = 0;
            }
            if resumes.in_a_row >= MAX_RESUMES {
                return false;
            }
            resumes.in_a_row += 1;
            resumes.total += 1;
            resumes.from_bytes = bytes;
            (resumes.total, resumes.in_a_row)
        };
        self.update(scan, |s| s.detail = Some(format!("Mất kết nối, đang tải tiếp (lần {total})…")));
        let this = Arc::clone(self);
        let scan = scan.to_owned();
        thread::spawn(move || {
            // Give the source a moment before asking it again, longer when
            // the last resume brought nothing.
            thread::sleep(std::time::Duration::from_secs(if in_a_row > 1 { 30 } else { 5 }));
            if let Err(error) = this.send(json!({"command":"retry_download", "scan_id":scan, "identity_key":identity})) {
                this.fail_link(&scan, error);
                this.pump();
            }
        });
        true
    }

    /// Start the next waiting link once the running one has finished.
    fn pump(self: &Arc<Self>) {
        loop {
            // Lock order everywhere: fetches, then queue.
            let (next, status) = {
                let Ok(mut fetches) = self.fetches.lock() else { return };
                let Ok(mut queue) = self.queue.lock() else { return };
                if queue.current.as_ref().and_then(|scan| fetches.get(scan)).is_some_and(|s| !finished(&s.state)) {
                    return;
                }
                queue.current = queue.waiting.pop_front();
                let Some(next) = queue.current.clone() else { return };
                for (place, scan) in queue.waiting.iter().enumerate() {
                    if let Some(status) = fetches.get_mut(scan) {
                        status.queue_position = Some(place + 1);
                    }
                }
                let Some(status) = fetches.get_mut(&next) else { continue };
                status.state = "preparing".into();
                status.queue_position = None;
                status.detail = Some("Đang khởi động dịch vụ tải…".into());
                status.started_at_ms = Some(now_ms());
                (next, status.clone())
            };
            let launch = self.launch.lock().ok().and_then(|launch| launch.clone());
            let started = match launch {
                Some((supervisor, root, data)) => self.ensure_started(&supervisor, &root, &data),
                None => Err(fail("Dịch vụ tải link chưa chạy")),
            }
            .and_then(|_| self.send(json!({"command":"start_video", "scan_id":next, "provider_id":status.provider, "source_ref":status.url})));
            match started {
                Ok(()) => return,
                Err(error) => self.fail_link(&next, error),
            }
        }
    }

    /// Queue a link; it starts once every earlier link has finished.
    pub fn fetch(self: &Arc<Self>, url: String, supervisor: &Path, root: &Path, data: &Path) -> Result<FetchStatus, String> {
        let provider = provider_for(&url)?;
        self.ensure_started(supervisor, root, data)?;
        let scan = scan_id(&url);
        {
            let mut fetches = self.fetches.lock().map_err(|_| fail("fetch table lock poisoned"))?;
            // The same link again while it is waiting or running is the same
            // fetch; once it has finished, it is fetched afresh.
            if let Some(existing) = fetches.values().find(|s| s.url == url && !finished(&s.state)) {
                return Ok(existing.clone());
            }
            let mut queue = self.queue.lock().map_err(|_| fail("link queue lock poisoned"))?;
            queue.waiting.push_back(scan.clone());
            fetches.insert(scan.clone(), FetchStatus { scan_id: scan.clone(), url, provider: provider.into(), state: "queued".into(),
                detail: None, queue_position: Some(queue.waiting.len()), started_at_ms: None,
                downloaded_bytes: 0, total_bytes: None, media_path: None, error: None });
        }
        self.pump();
        self.status(&scan).ok_or_else(|| fail("fetch disappeared"))
    }

    /// Drop a link that is still waiting; a running link cannot be withdrawn here.
    pub fn withdraw(&self, scan: &str) -> bool {
        let (Ok(mut fetches), Ok(mut queue)) = (self.fetches.lock(), self.queue.lock()) else { return false };
        let Some(place) = queue.waiting.iter().position(|waiting| waiting == scan) else { return false };
        queue.waiting.remove(place);
        fetches.remove(scan);
        for (place, waiting) in queue.waiting.iter().enumerate() {
            if let Some(status) = fetches.get_mut(waiting) {
                status.queue_position = Some(place + 1);
            }
        }
        true
    }

    /// Store a provider session in the service's protected (DPAPI) store and
    /// wait for its receipt. Blocking: call from a worker thread.
    pub fn save_session(self: &Arc<Self>, provider: &str, cookie: String, expires_at: u64,
                        supervisor: &Path, root: &Path, data: &Path) -> Result<(), String> {
        self.ensure_started(supervisor, root, data)?;
        *self.session.lock().map_err(|_| fail("session lock poisoned"))? = None;
        self.send(json!({"command":"session_save", "provider_id":provider, "headers":{"Cookie":cookie}, "expires_at":expires_at}))?;
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(240);
        while std::time::Instant::now() < deadline {
            if let Some(event) = self.session.lock().map_err(|_| fail("session lock poisoned"))?.take() {
                return if event["event"] == "source_session" && event["state"] == "ready" {
                    Ok(())
                } else {
                    Err(fail(format!("Không lưu được phiên {provider} ({}).", event["code"].as_str().unwrap_or("SESSION_FAILED"))))
                };
            }
            thread::sleep(std::time::Duration::from_millis(300));
        }
        Err(fail("Lưu phiên quá lâu; hãy thử lại."))
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

    /// Real protected-store round trip; same env vars as the download test.
    #[test]
    #[ignore]
    fn real_session_save_reaches_the_protected_store() {
        let (Some(root), Some(data)) = (std::env::var_os("DUBFLOW_DEV_ROOT"), std::env::var_os("DUBFLOW_DEV_DATA")) else { return };
        let root = PathBuf::from(root);
        let sources = Arc::new(Sources::default());
        let expires = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs() + 3600;
        let result = sources.save_session("douyin", "s_v_web_id=verify_fixture; ttwid=1%7Cfixture".into(), expires,
            &root.join("app/bin/dubflow-supervisor.exe"), &root, Path::new(&data));
        sources.shutdown();
        result.unwrap();
    }

    #[test]
    fn cookie_header_puts_essential_cookies_first_and_stays_bounded() {
        let mut cookies: Vec<(String, String)> = (0..200).map(|i| (format!("junk{i}"), "x".repeat(40))).collect();
        cookies.push(("s_v_web_id".into(), "verify_abc".into()));
        cookies.push(("ttwid".into(), "1%7Cabc".into()));
        cookies.push(("bad".into(), "has space".into()));
        cookies.push(("quoted".into(), "\"x\"".into()));
        let header = cookie_header(&cookies, &DOUYIN_PRIORITY).unwrap();
        assert!(header.starts_with("s_v_web_id=verify_abc; ttwid=1%7Cabc; "));
        assert!(header.len() <= MAX_COOKIE_HEADER);
        assert!(header.split("; ").count() <= 64);
        assert!(!header.contains("bad=") && !header.contains("quoted="));
        assert_eq!(cookie_header(&[("a".into(), "".into())], &DOUYIN_PRIORITY), None);
    }

    fn queued(sources: &Sources, scan: &str, state: &str) {
        sources.fetches.lock().unwrap().insert(scan.into(), FetchStatus { scan_id: scan.into(), url: format!("https://www.bilibili.com/video/{scan}/"),
            provider: "bilibili".into(), state: state.into(), detail: None, queue_position: None, started_at_ms: None,
            downloaded_bytes: 0, total_bytes: None, media_path: None, error: None });
    }

    #[test]
    fn withdrawing_a_waiting_link_renumbers_the_rest() {
        let sources = Sources::default();
        for scan in ["a", "b", "c"] {
            queued(&sources, scan, "queued");
            sources.queue.lock().unwrap().waiting.push_back(scan.into());
        }
        assert!(sources.withdraw("b"));
        assert!(!sources.withdraw("b"));
        assert!(sources.status("b").is_none());
        assert_eq!(sources.status("c").unwrap().queue_position, Some(2));
        assert_eq!(sources.queue.lock().unwrap().waiting, ["a", "c"]);
    }

    #[test]
    fn a_rejection_without_a_scan_fails_the_running_link_and_moves_the_queue_on() {
        let sources = Arc::new(Sources::default());
        queued(&sources, "running", "preparing");
        queued(&sources, "next", "queued");
        {
            let mut queue = sources.queue.lock().unwrap();
            queue.current = Some("running".into());
            queue.waiting.push_back("next".into());
        }
        sources.on_event(&json!({"event":"source_error", "code":"SOURCE_REQUEST_REJECTED", "retryable":false}));
        let running = sources.status("running").unwrap();
        assert_eq!((running.state.as_str(), running.error.as_deref()), ("failed", Some("Dịch vụ tải không nhận link này. Hãy thử lại.")));
        // The next link left the queue; with no service in this test it fails
        // to start, and the queue ends empty instead of waiting forever.
        assert!(sources.queue.lock().unwrap().waiting.is_empty());
        assert_eq!(sources.queue.lock().unwrap().current, None);
        let next = sources.status("next").unwrap();
        assert_eq!(next.state, "failed");
        assert!(next.started_at_ms.is_some());
        // A late event for a finished link changes nothing.
        sources.on_event(&json!({"event":"source_error", "scan_id":"running", "code":"NETWORK"}));
        assert_eq!(sources.status("running").unwrap().error.as_deref(), Some("Dịch vụ tải không nhận link này. Hãy thử lại."));
    }

    #[test]
    fn a_dropped_download_resumes_while_it_makes_progress_then_twice_more() {
        let sources = Arc::new(Sources::default());
        queued(&sources, "big", "downloading");
        sources.queue.lock().unwrap().current = Some("big".into());
        let dropped = json!({"event":"source_download_failed", "scan_id":"big", "identity_key":"bilibili:BV1",
            "code":"NETWORK", "retryable":true});
        let progress = |mb: u64| json!({"event":"source_download_progress", "scan_id":"big", "identity_key":"bilibili:BV1",
            "downloaded_bytes":mb << 20, "total_bytes":600u64 << 20});
        // Every drop after new bytes resumes, however many there are.
        for attempt in 1..=5 {
            sources.on_event(&progress(attempt * 100));
            sources.on_event(&dropped);
            let status = sources.status("big").unwrap();
            assert_eq!(status.state, "downloading");
            assert_eq!(status.detail, Some(format!("Mất kết nối, đang tải tiếp (lần {attempt})…")));
        }
        // A stalled download gets `MAX_RESUMES` resumes since its last
        // progress, the one above included, and then the link fails.
        for _ in 1..MAX_RESUMES {
            sources.on_event(&dropped);
            assert_eq!(sources.status("big").unwrap().state, "downloading");
        }
        sources.on_event(&dropped);
        assert_eq!(sources.status("big").unwrap().state, "failed");
        // A failure that a retry cannot fix fails at once.
        queued(&sources, "gone", "downloading");
        sources.on_event(&json!({"event":"source_download_failed", "scan_id":"gone", "identity_key":"bilibili:BV2",
            "code":"NOT_FOUND", "retryable":false}));
        assert_eq!(sources.status("gone").unwrap().state, "failed");
    }

    #[test]
    fn every_fetch_of_a_link_gets_its_own_scan() {
        let url = "https://www.bilibili.com/video/BV1Lha9zCEJ4/";
        assert_ne!(scan_id(url), scan_id(url));
        assert!(scan_id(url).starts_with("link-") && scan_id(url).len() == 29);
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
