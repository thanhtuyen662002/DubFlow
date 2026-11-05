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
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;

#[derive(Clone, Debug, Serialize)]
pub struct FetchStatus {
    pub scan_id: String,
    pub url: String,
    pub provider: String,
    /// The video's title when the caller knows it (Khám phá).
    pub title: Option<String>,
    /// queued | preparing | downloading | done | failed | cancelled
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
    /// The service's failure code, e.g. AUTH_REQUIRED.
    pub error_code: Option<String>,
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
    /// One session save at a time: the service runs one and refuses another,
    /// and a receipt names no caller.
    session_gate: Mutex<()>,
    /// A session save is waiting for its receipt; a rejection naming no scan
    /// then belongs to it.
    session_waiting: AtomicBool,
    /// Automatic resumes per link after a dropped connection.
    resumes: Mutex<HashMap<String, Resumes>>,
    /// A cancelled running link whose stop request is not answered yet; a
    /// rejection naming no scan belongs to it, not to the next link.
    cancelling: Mutex<Option<String>>,
    /// A start waits for a session save to finish (the service refuses
    /// every request while it saves); one waiter at a time.
    start_deferred: AtomicBool,
}

/// Longest wait for a session save before the next link starts anyway.
const SESSION_SAVE_WAIT: std::time::Duration = std::time::Duration::from_secs(250);

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
    matches!(state, "done" | "failed" | "cancelled")
}

/// What asks the service to stop a running link: preparation and download
/// are cancelled by different requests.
fn stop_command(state: &str) -> Option<&'static str> {
    match state {
        "preparing" => Some("cancel"),
        "downloading" => Some("cancel_download"),
        _ => None,
    }
}

/// Longest title kept for a link, in characters.
const MAX_TITLE_CHARS: usize = 200;

fn clean_title(title: Option<String>) -> Option<String> {
    let title: String = title?.chars().filter(|c| !c.is_control()).collect();
    let title: String = title.trim().chars().take(MAX_TITLE_CHARS).collect();
    let title = title.trim_end();
    (!title.is_empty()).then(|| title.to_owned())
}

/// Cookies yt-dlp's Douyin extractor needs first; the service accepts one
/// Cookie header of at most 2048 bytes and 64 cookies.
pub const DOUYIN_PRIORITY: [&str; 12] = ["s_v_web_id", "ttwid", "msToken", "__ac_nonce", "__ac_signature", "sessionid",
    "sessionid_ss", "sid_tt", "sid_guard", "uid_tt", "odin_tt", "passport_csrf_token"];
const MAX_COOKIE_HEADER: usize = 2000;
/// The protected session store keeps a session for at most 24 hours.
pub const SESSION_SECONDS: u64 = 23 * 60 * 60;
/// A session this app saved is reused this long before it is refreshed.
pub const SESSION_REUSE_SECONDS: u64 = 20 * 60 * 60;

/// Whether a session saved at `saved_at` (Unix seconds) can still be used.
pub fn session_fresh(saved_at: u64, now: u64) -> bool {
    saved_at <= now && now - saved_at < SESSION_REUSE_SECONDS
}

/// The aweme id of a douyin.com video page URL (`/video/<id>`, `/note/<id>`
/// or a `modal_id=<id>` overlay), as the page itself reports it.
pub fn douyin_video_id(url: &str) -> Option<String> {
    let rest = url.strip_prefix("https://")?;
    let (host, path) = rest.split_once('/').unwrap_or((rest, ""));
    if host != "www.douyin.com" && host != "douyin.com" {
        return None;
    }
    let digits = |text: &str| -> Option<String> {
        let id: String = text.chars().take_while(|c| c.is_ascii_digit()).collect();
        (6..=32).contains(&id.len()).then_some(id)
    };
    let (path_only, query) = path.split_once('?').unwrap_or((path, ""));
    let path_only = path_only.split('#').next().unwrap_or("");
    for prefix in ["video/", "note/"] {
        if let Some(tail) = path_only.strip_prefix(prefix) {
            if let Some(id) = digits(tail).filter(|id| tail.len() == id.len() || tail[id.len()..].starts_with('/')) {
                return Some(id);
            }
        }
    }
    query.split(['&', '#']).find_map(|pair| pair.strip_prefix("modal_id=").and_then(digits))
}

fn douyin_media_url(value: &Value) -> Option<String> {
    let text = value.as_str()?;
    (text.len() <= 4096 && text.starts_with("https://") && !text.chars().any(|c| c.is_control() || c.is_whitespace()))
        .then(|| text.to_owned())
}

/// Reduce the Douyin web page's own video detail response to what the link
/// service needs: id, title, duration and the best rendition's media URLs.
/// The response comes from a web page, so everything is checked.
pub fn douyin_page_detail(text: &str, id: &str) -> Result<Value, String> {
    if text.trim().is_empty() {
        return Err("Douyin trả về trang trống".into());
    }
    let value: Value = serde_json::from_str(text).map_err(|_| fail("Douyin trả về dữ liệu không đọc được"))?;
    if value["status_code"].as_i64() != Some(0) {
        return Err(fail("Douyin từ chối yêu cầu thông tin video"));
    }
    let detail = &value["aweme_detail"];
    if !detail.is_object() {
        return Err(fail("Không tìm thấy video (có thể đã bị xóa hoặc là video riêng tư)"));
    }
    if detail["aweme_id"].as_str() != Some(id) {
        return Err(fail("Douyin trả về video khác với link"));
    }
    let video = &detail["video"];
    // The rendition with the most pixels, then the highest bit rate; the
    // plain play address when the page lists no renditions.
    let mut best: Option<(u64, u64, &Value)> = None;
    for rendition in video["bit_rate"].as_array().into_iter().flatten().take(64) {
        let address = &rendition["play_addr"];
        let area = address["width"].as_u64().unwrap_or(0) * address["height"].as_u64().unwrap_or(0);
        let rate = rendition["bit_rate"].as_u64().unwrap_or(0);
        if address["url_list"].as_array().is_some_and(|urls| !urls.is_empty())
            && best.map_or(true, |(best_area, best_rate, _)| (area, rate) > (best_area, best_rate))
        {
            best = Some((area, rate, address));
        }
    }
    let address = best.map(|(_, _, address)| address).unwrap_or(&video["play_addr"]);
    let all: Vec<String> = address["url_list"].as_array().into_iter().flatten().take(16).filter_map(douyin_media_url).collect();
    // Media CDN addresses first; douyin.com's own play redirect only when
    // there is nothing else (the link service picks one candidate).
    let mut urls: Vec<String> = Vec::new();
    for url in all.iter().filter(|url| !url.starts_with("https://www.douyin.com/")).chain(all.iter()) {
        if !urls.contains(url) && (urls.is_empty() || !url.starts_with("https://www.douyin.com/")) {
            urls.push(url.clone());
        }
    }
    urls.truncate(4);
    if urls.is_empty() {
        return Err(fail("Video Douyin này không có bản tải được (có thể là bài ảnh)"));
    }
    let title: String = detail["desc"].as_str().unwrap_or("").chars().filter(|c| !c.is_control()).take(1024).collect();
    let mut compact = json!({
        "aweme_id": id,
        "desc": title.trim(),
        "video": {"play_addr": {"url_list": urls}},
    });
    if let Some(duration) = detail["duration"].as_u64().or_else(|| video["duration"].as_u64()) {
        compact["duration"] = json!(duration);
    }
    for key in ["width", "height"] {
        if let Some(size) = address[key].as_u64().or_else(|| video[key].as_u64()) {
            compact["video"][key] = json!(size);
        }
    }
    Ok(compact)
}

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
        "AUTH_REQUIRED" | "PRIVATE" if provider == "douyin" => "Douyin yêu cầu đăng nhập để tải video này. Bấm “Đăng nhập Douyin”, đăng nhập rồi thử lại.".into(),
        "AUTH_REQUIRED" | "PRIVATE" => "Video yêu cầu đăng nhập hoặc là video riêng tư.".into(),
        "RATE_LIMITED" => "Trang nguồn đang giới hạn truy cập. Hãy thử lại sau ít phút.".into(),
        "NOT_FOUND" => "Không tìm thấy video (có thể đã bị xóa).".into(),
        "NETWORK" => "Không tải được video từ link này (lỗi mạng hoặc trang không hỗ trợ).".into(),
        "UNSUPPORTED" => "Link này chưa được hỗ trợ.".into(),
        "INVALID_INPUT" => "Link không hợp lệ.".into(),
        "SOURCE_REQUEST_REJECTED" => "Dịch vụ tải không nhận link này. Hãy thử lại.".into(),
        "SOURCE_CHANGED" => "Trang nguồn vừa thay đổi nên chưa tải được. Hãy cập nhật DubFlow lên bản mới nhất rồi thử lại.".into(),
        "SOURCE_WORKER_FAILED" => "Trình tải gặp lỗi bất ngờ. Bấm “Thử lại”; nếu vẫn lỗi, bấm “Gửi báo cáo lỗi” rồi gửi file zip trên Desktop cho nhà phát triển.".into(),
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
        // A rejected request names no scan. While a session save waits and no
        // link is starting or being stopped, the rejection is the save's.
        let link_preparing = || self.queue.lock().ok().and_then(|queue| queue.current.clone())
            .is_some_and(|scan| self.status(&scan).is_some_and(|s| s.state == "preparing"));
        let session_rejected = event["event"] == "source_error" && event["scan_id"].is_null()
            && self.session_waiting.load(Ordering::SeqCst) && !link_preparing()
            && self.cancelling.lock().map(|cancelling| cancelling.is_none()).unwrap_or(false);
        if session_rejected || matches!(event["event"].as_str(), Some("source_session" | "source_session_error")) {
            if let Ok(mut slot) = self.session.lock() {
                *slot = Some(event.clone());
            }
            return;
        }
        // The answer to a stop request, or a rejection of it, ends the wait.
        let stopped = self.cancelling.lock().ok().and_then(|mut cancelling| match event["scan_id"].as_str() {
            Some(scan) if cancelling.as_deref() == Some(scan) => cancelling.take(),
            None if event["event"] == "source_error" => cancelling.take(),
            _ => None,
        });
        if stopped.is_some() && event["scan_id"].is_null() {
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
        // A cancelled link is gone for the user; late events change nothing.
        if self.status(scan).is_none_or(|s| s.state == "cancelled") {
            return;
        }
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
                let found = clean_title(event["items"][0]["title"].as_str().map(str::to_owned));
                let mut started = false;
                self.update(scan, |s| if s.state == "preparing" {
                    s.state = "downloading".into();
                    s.detail = None;
                    // A pasted link learns its title from the service.
                    if s.title.is_none() {
                        s.title = found.clone();
                    }
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
                self.update(scan, |s| if s.state == "failed" && s.error_code.is_none() {
                    s.error_code = Some(code.chars().take(64).collect());
                });
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
            // Cancelled (or otherwise ended) while waiting.
            if this.status(&scan).is_none_or(|s| finished(&s.state)) {
                return;
            }
            if let Err(error) = this.send(json!({"command":"retry_download", "scan_id":scan, "identity_key":identity})) {
                this.fail_link(&scan, error);
                this.pump();
            }
        });
        true
    }

    /// Start the next waiting link once the running one has finished.
    fn pump(self: &Arc<Self>) {
        // The service refuses a start while it saves a session, so the next
        // link waits for the save (a Douyin link starts one alongside).
        if self.session_waiting.load(Ordering::SeqCst) {
            if !self.start_deferred.swap(true, Ordering::SeqCst) {
                let sources = Arc::clone(self);
                thread::spawn(move || {
                    let deadline = std::time::Instant::now() + SESSION_SAVE_WAIT;
                    while sources.session_waiting.load(Ordering::SeqCst) && std::time::Instant::now() < deadline {
                        thread::sleep(std::time::Duration::from_millis(200));
                    }
                    sources.start_deferred.store(false, Ordering::SeqCst);
                    sources.pump();
                });
            }
            return;
        }
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
    pub fn fetch(self: &Arc<Self>, url: String, title: Option<String>, supervisor: &Path, root: &Path, data: &Path) -> Result<FetchStatus, String> {
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
            fetches.insert(scan.clone(), FetchStatus { scan_id: scan.clone(), url, provider: provider.into(), title: clean_title(title),
                state: "queued".into(), detail: None, queue_position: Some(queue.waiting.len()), started_at_ms: None,
                downloaded_bytes: 0, total_bytes: None, media_path: None, error: None, error_code: None });
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

    /// Cancel a link that has not finished. A waiting link is withdrawn; the
    /// running one is marked cancelled at once, the service is asked to stop
    /// it (without waiting for its answer) and the next link starts.
    pub fn cancel(self: &Arc<Self>, scan: &str) -> bool {
        if self.withdraw(scan) {
            return true;
        }
        let command = {
            let (Ok(mut fetches), Ok(queue)) = (self.fetches.lock(), self.queue.lock()) else { return false };
            let Some(status) = fetches.get_mut(scan) else { return false };
            if finished(&status.state) {
                return false;
            }
            let command = if queue.current.as_deref() == Some(scan) { stop_command(&status.state) } else { None };
            status.state = "cancelled".into();
            status.detail = None;
            status.queue_position = None;
            command
        };
        if let Ok(mut resumes) = self.resumes.lock() {
            resumes.remove(scan);
        }
        if let Some(command) = command {
            if let Ok(mut cancelling) = self.cancelling.lock() {
                *cancelling = Some(scan.to_owned());
            }
            let _ = self.send(json!({"command":command, "scan_id":scan}));
        }
        self.pump();
        true
    }

    /// Start the service now, without waiting for it: its first start checks
    /// the whole install, which can take a minute on a cold disk.
    pub fn warm_up(self: &Arc<Self>, supervisor: &Path, root: &Path, data: &Path) -> Result<(), String> {
        self.ensure_started(supervisor, root, data)
    }

    /// A link the service is running right now.
    fn link_running(&self) -> bool {
        self.queue.lock().ok().and_then(|queue| queue.current.clone())
            .is_some_and(|scan| self.status(&scan).is_some_and(|s| !finished(&s.state)))
    }

    /// Store a provider session in the service's protected (DPAPI) store and
    /// wait for its receipt. Blocking: call from a worker thread.
    pub fn save_session(self: &Arc<Self>, provider: &str, cookie: String, expires_at: u64,
                        supervisor: &Path, root: &Path, data: &Path) -> Result<(), String> {
        let _one_at_a_time = self.session_gate.lock().map_err(|_| fail("session gate poisoned"))?;
        // The service refuses session work while a link runs.
        if self.link_running() {
            return Err(fail("Đang tải một link nên chưa lưu được phiên. Hãy đợi link tải xong rồi bấm lại."));
        }
        self.ensure_started(supervisor, root, data)?;
        *self.session.lock().map_err(|_| fail("session lock poisoned"))? = None;
        self.session_waiting.store(true, Ordering::SeqCst);
        let result = self.send(json!({"command":"session_save", "provider_id":provider, "headers":{"Cookie":cookie}, "expires_at":expires_at}))
            .and_then(|_| self.session_receipt(provider));
        self.session_waiting.store(false, Ordering::SeqCst);
        result
    }

    fn session_receipt(&self, provider: &str) -> Result<(), String> {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(240);
        while std::time::Instant::now() < deadline {
            if let Some(event) = self.session.lock().map_err(|_| fail("session lock poisoned"))?.take() {
                return session_result(provider, &event);
            }
            let stopped = self.service.lock().map(|mut service| service.as_mut()
                .is_none_or(|service| !matches!(service.child.try_wait(), Ok(None)))).unwrap_or(true);
            if stopped {
                return Err(fail("Dịch vụ tải link đã dừng; hãy thử lại."));
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

/// What a session save's answer from the service means for the user.
fn session_result(provider: &str, event: &Value) -> Result<(), String> {
    if event["event"] == "source_session" && event["state"] == "ready" {
        return Ok(());
    }
    if event["event"] == "source_error" {
        return Err(fail("Dịch vụ tải đang bận nên chưa lưu được phiên. Hãy đợi vài giây rồi bấm lại."));
    }
    Err(fail(format!("Không lưu được phiên {provider} ({}).", event["code"].as_str().unwrap_or("SESSION_FAILED"))))
}

/// `Videos\DubFlow Output\<name> - vi\<scan>` so finished link jobs are easy
/// to find; the name is the video's title when known, else the link's tail.
pub fn link_output_dir(url: &str, title: Option<&str>, scan: &str) -> Result<PathBuf, String> {
    let profile = std::env::var_os("USERPROFILE").ok_or_else(|| fail("USERPROFILE is unavailable"))?;
    Ok(PathBuf::from(profile).join("Videos").join("DubFlow Output").join(format!("{} - vi", link_folder_name(url, title))).join(scan))
}

/// The results folder of a link that only learned its title while it
/// downloaded: its untitled folder (still empty) moves to the titled name.
/// Any other folder (already titled, chosen elsewhere, or with files in it)
/// stays where it is.
pub fn retitled_output_dir(current: &Path, url: &str, title: &str, scan: &str) -> Result<PathBuf, String> {
    let profile = std::env::var_os("USERPROFILE").ok_or_else(|| fail("USERPROFILE is unavailable"))?;
    retitled_output_in(&PathBuf::from(profile).join("Videos").join("DubFlow Output"), current, url, title, scan)
}

fn retitled_output_in(output: &Path, current: &Path, url: &str, title: &str, scan: &str) -> Result<PathBuf, String> {
    let named = |title: Option<&str>| output.join(format!("{} - vi", link_folder_name(url, title))).join(scan);
    let (untitled, titled) = (named(None), named(Some(title)));
    if current != untitled || titled == untitled || titled.exists() {
        return Ok(current.to_path_buf());
    }
    let empty = fs::read_dir(current).map(|mut entries| entries.next().is_none()).unwrap_or(true);
    if !empty {
        return Ok(current.to_path_buf());
    }
    let parent = titled.parent().ok_or_else(|| fail("results folder has no parent"))?;
    fs::create_dir_all(parent).map_err(|error| fail(format!("Không tạo được thư mục kết quả: {error}")))?;
    if current.exists() {
        fs::rename(current, &titled).map_err(|error| fail(format!("Không đổi tên được thư mục kết quả: {error}")))?;
        // The untitled `<id> - vi` folder goes too once nothing else is in it.
        if let Some(old) = current.parent() {
            let _ = fs::remove_dir(old);
        }
    } else {
        fs::create_dir_all(&titled).map_err(|error| fail(format!("Không tạo được thư mục kết quả: {error}")))?;
    }
    Ok(titled)
}

fn link_folder_name(url: &str, title: Option<&str>) -> String {
    let tail = url.split(['?', '#']).next().unwrap_or(url).trim_end_matches('/').rsplit('/').next().unwrap_or("video");
    let stem = tail.rsplit_once('.').map(|(stem, _)| stem).unwrap_or(tail);
    let source = title.map(str::trim).filter(|title| !title.is_empty()).unwrap_or(stem);
    // Letters and digits of any script stay; anything else (spaces too) becomes one `-`.
    let mut name = String::new();
    for c in source.chars() {
        let c = if c.is_alphanumeric() || c == '_' { c } else { '-' };
        if c != '-' || !name.ends_with('-') {
            name.push(c);
        }
    }
    let name: String = name.trim_matches('-').chars().take(60).collect();
    let name = name.trim_end_matches('-');
    if name.is_empty() { "video".to_owned() } else { name.to_owned() }
}

/// Characters Windows refuses in a file name.
const FORBIDDEN_NAME_CHARS: [char; 9] = ['<', '>', ':', '"', '/', '\\', '|', '?', '*'];
const RESERVED_NAMES: [&str; 22] = ["CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"];

/// A video title as a Windows file name (without extension): forbidden and
/// control characters removed, no leading or trailing dots and spaces, at
/// most 120 characters, "video" when nothing is left.
pub fn safe_file_name(title: &str) -> String {
    // A forbidden character becomes a space so words stay apart; runs collapse.
    let spaced: String = title.chars().map(|c| if c.is_control() || FORBIDDEN_NAME_CHARS.contains(&c) { ' ' } else { c }).collect();
    let kept = spaced.split_whitespace().collect::<Vec<_>>().join(" ");
    let edge = |c: char| c == '.' || c.is_whitespace();
    let trimmed: String = kept.trim_matches(edge).chars().take(120).collect();
    let name = trimmed.trim_end_matches(edge);
    if name.is_empty() {
        "video".to_owned()
    } else if RESERVED_NAMES.iter().any(|reserved| name.split('.').next().is_some_and(|stem| stem.trim_end().eq_ignore_ascii_case(reserved))) {
        format!("{name}_")
    } else {
        name.to_owned()
    }
}

/// `<name>.<ext>`, then `<name> (2).<ext>`, `<name> (3).<ext>`…
pub fn numbered_file_name(name: &str, extension: &str, number: u32) -> String {
    let suffix = if number > 1 { format!(" ({number})") } else { String::new() };
    if extension.is_empty() { format!("{name}{suffix}") } else { format!("{name}{suffix}.{extension}") }
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

    #[test]
    fn a_rejected_session_save_fails_at_once_instead_of_waiting() {
        let sources = Arc::new(Sources::default());
        sources.session_waiting.store(true, Ordering::SeqCst);
        sources.on_event(&json!({"event":"source_error", "code":"SOURCE_REQUEST_REJECTED", "retryable":false}));
        let event = sources.session.lock().unwrap().take().expect("rejection reaches the waiting save");
        let error = session_result("douyin", &event).unwrap_err();
        assert!(error.contains("bận"), "{error}");
        // Nobody waiting: a stray rejection is not kept for a later save.
        sources.session_waiting.store(false, Ordering::SeqCst);
        sources.on_event(&json!({"event":"source_error", "code":"SOURCE_REQUEST_REJECTED", "retryable":false}));
        assert!(sources.session.lock().unwrap().is_none());
        assert!(session_result("douyin", &json!({"event":"source_session", "state":"ready"})).is_ok());
    }

    #[test]
    fn a_session_save_is_refused_while_a_link_runs() {
        let sources = Arc::new(Sources::default());
        sources.fetches.lock().unwrap().insert("link-1".into(), FetchStatus { scan_id: "link-1".into(), url: "https://v.douyin.com/x/".into(),
            provider: "douyin".into(), title: None, state: "downloading".into(), detail: None, queue_position: None, started_at_ms: None,
            downloaded_bytes: 0, total_bytes: None, media_path: None, error: None, error_code: None });
        sources.queue.lock().unwrap().current = Some("link-1".into());
        let error = sources.save_session("douyin", "ttwid=1".into(), 1, Path::new("missing"), Path::new("missing"), Path::new("missing")).unwrap_err();
        assert!(error.contains("link"), "{error}");
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
        let started = sources.fetch(url, None, &root.join("app/bin/dubflow-supervisor.exe"), &root, Path::new(&data)).unwrap();
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
    fn douyin_video_id_reads_video_pages_only() {
        assert_eq!(douyin_video_id("https://www.douyin.com/video/7699053575086918966").as_deref(), Some("7699053575086918966"));
        assert_eq!(douyin_video_id("https://www.douyin.com/video/7699053575086918966?previous_page=x").as_deref(), Some("7699053575086918966"));
        assert_eq!(douyin_video_id("https://www.douyin.com/note/7699053575086918966").as_deref(), Some("7699053575086918966"));
        assert_eq!(douyin_video_id("https://www.douyin.com/search/abc?modal_id=7699053575086918966&type=video").as_deref(), Some("7699053575086918966"));
        assert_eq!(douyin_video_id("https://www.douyin.com/user/MS4w"), None);
        assert_eq!(douyin_video_id("https://www.douyin.com/video/12345x"), None);
        assert_eq!(douyin_video_id("https://evil.example/video/7699053575086918966"), None);
        assert_eq!(douyin_video_id("http://www.douyin.com/video/7699053575086918966"), None);
    }

    #[test]
    fn douyin_page_detail_keeps_the_best_rendition() {
        let raw = json!({"status_code": 0, "aweme_detail": {"aweme_id": "7699053575086918966", "desc": "Phim\nhay", "duration": 143489,
            "video": {"play_addr": {"url_list": ["https://v3-web.douyinvod.com/low.mp4"], "width": 540, "height": 960},
                "bit_rate": [
                    {"bit_rate": 900, "play_addr": {"width": 576, "height": 1024, "url_list": ["https://v3-web.douyinvod.com/540.mp4"]}},
                    {"bit_rate": 2300, "play_addr": {"width": 720, "height": 1280, "url_list": [
                        "https://www.douyin.com/aweme/v1/play/?video_id=v1", "https://v3-web.douyinvod.com/720.mp4",
                        "https://v26-web.douyinvod.com/720.mp4", "https://v3-web.douyinvod.com/720.mp4", "javascript:alert(1)"]}},
                    {"bit_rate": 1500, "play_addr": {"width": 720, "height": 1280, "url_list": ["https://v3-web.douyinvod.com/720-low.mp4"]}}]}}});
        let detail = douyin_page_detail(&raw.to_string(), "7699053575086918966").unwrap();
        assert_eq!(detail["video"]["play_addr"]["url_list"], json!(["https://v3-web.douyinvod.com/720.mp4", "https://v26-web.douyinvod.com/720.mp4"]));
        assert_eq!((detail["video"]["width"].as_u64(), detail["video"]["height"].as_u64()), (Some(720), Some(1280)));
        assert_eq!(detail["desc"], "Phimhay");
        assert_eq!(detail["duration"], 143489);
        // Only douyin.com's play redirect: kept as the one candidate.
        let only_play = json!({"status_code": 0, "aweme_detail": {"aweme_id": "7699053575086918966", "desc": "",
            "video": {"play_addr": {"url_list": ["https://www.douyin.com/aweme/v1/play/?video_id=v1"]}}}});
        assert_eq!(douyin_page_detail(&only_play.to_string(), "7699053575086918966").unwrap()["video"]["play_addr"]["url_list"],
            json!(["https://www.douyin.com/aweme/v1/play/?video_id=v1"]));
    }

    #[test]
    fn douyin_page_detail_refuses_unusable_answers() {
        let id = "7699053575086918966";
        assert!(douyin_page_detail("", id).is_err());
        assert!(douyin_page_detail("<html>", id).is_err());
        assert!(douyin_page_detail(&json!({"status_code": 8}).to_string(), id).is_err());
        assert!(douyin_page_detail(&json!({"status_code": 0, "aweme_detail": null}).to_string(), id).is_err());
        assert!(douyin_page_detail(&json!({"status_code": 0, "aweme_detail": {"aweme_id": "7699053575086918967", "video": {}}}).to_string(), id).is_err());
        assert!(douyin_page_detail(&json!({"status_code": 0, "aweme_detail": {"aweme_id": id, "video": {"play_addr": {"url_list": []}}}}).to_string(), id).is_err());
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
            provider: "bilibili".into(), title: None, state: state.into(), detail: None, queue_position: None, started_at_ms: None,
            downloaded_bytes: 0, total_bytes: None, media_path: None, error: None, error_code: None });
    }

    #[test]
    fn cancelling_a_waiting_link_withdraws_it() {
        let sources = Arc::new(Sources::default());
        for scan in ["a", "b"] {
            queued(&sources, scan, "queued");
            sources.queue.lock().unwrap().waiting.push_back(scan.into());
        }
        assert!(sources.cancel("a"));
        assert!(sources.status("a").is_none());
        assert_eq!(sources.status("b").unwrap().queue_position, Some(1));
        assert!(!sources.cancel("a"));
    }

    #[test]
    fn a_running_link_is_stopped_with_the_request_for_its_step() {
        assert_eq!(stop_command("preparing"), Some("cancel"));
        assert_eq!(stop_command("downloading"), Some("cancel_download"));
        for state in ["queued", "done", "failed", "cancelled"] {
            assert_eq!(stop_command(state), None);
        }
    }

    #[test]
    fn a_cancelled_link_frees_the_queue_and_ignores_late_events() {
        let sources = Arc::new(Sources::default());
        queued(&sources, "running", "downloading");
        queued(&sources, "next", "queued");
        {
            let mut queue = sources.queue.lock().unwrap();
            queue.current = Some("running".into());
            queue.waiting.push_back("next".into());
        }
        sources.resumes.lock().unwrap().insert("running".into(), Resumes::default());
        assert!(sources.cancel("running"));
        assert_eq!(sources.status("running").unwrap().state, "cancelled");
        assert!(!sources.resumes.lock().unwrap().contains_key("running"));
        assert_eq!(sources.cancelling.lock().unwrap().as_deref(), Some("running"));
        // The next link left the queue at once (and fails to start without a service).
        assert!(sources.queue.lock().unwrap().waiting.is_empty());
        assert!(sources.status("next").unwrap().started_at_ms.is_some());
        // Late events for the cancelled link change nothing.
        sources.on_event(&json!({"event":"source_downloaded", "scan_id":"running", "media_path":"x.mp4", "size_bytes":1}));
        sources.on_event(&json!({"event":"source_download_cancelled", "scan_id":"running"}));
        assert_eq!(sources.status("running").unwrap().state, "cancelled");
        assert_eq!(*sources.cancelling.lock().unwrap(), None);
        assert!(!sources.cancel("running"), "a cancelled link stays cancelled");
        // A rejected stop request names no scan; it never fails the link now running.
        queued(&sources, "third", "preparing");
        sources.queue.lock().unwrap().current = Some("third".into());
        *sources.cancelling.lock().unwrap() = Some("running".into());
        sources.on_event(&json!({"event":"source_error", "code":"SOURCE_REQUEST_REJECTED"}));
        assert_eq!(sources.status("third").unwrap().state, "preparing");
    }

    #[test]
    fn a_link_waits_for_a_session_save_before_it_starts() {
        let sources = Arc::new(Sources::default());
        queued(&sources, "next", "queued");
        sources.queue.lock().unwrap().waiting.push_back("next".into());
        sources.session_waiting.store(true, Ordering::SeqCst);
        sources.pump();
        sources.pump();
        assert_eq!(sources.status("next").unwrap().state, "queued", "nothing starts while the session is saved");
        assert!(sources.start_deferred.load(Ordering::SeqCst));
        sources.session_waiting.store(false, Ordering::SeqCst);
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while sources.status("next").unwrap().started_at_ms.is_none() && std::time::Instant::now() < deadline {
            thread::sleep(std::time::Duration::from_millis(50));
        }
        // Started once the save ended (and fails here: no service runs in a test).
        assert!(sources.status("next").unwrap().started_at_ms.is_some());
        assert!(!sources.start_deferred.load(Ordering::SeqCst));
    }

    #[test]
    fn a_pasted_link_takes_the_title_the_service_found() {
        let sources = Arc::new(Sources::default());
        queued(&sources, "pasted", "preparing");
        queued(&sources, "named", "preparing");
        sources.update("named", |s| s.title = Some("Tên từ Khám phá".into()));
        for scan in ["pasted", "named"] {
            sources.on_event(&json!({"event":"source_items", "scan_id":scan, "items":[{"identity_key":"bilibili:x", "title":"  【4K】Video hay  "}]}));
        }
        assert_eq!(sources.status("pasted").unwrap().title.as_deref(), Some("【4K】Video hay"));
        assert_eq!(sources.status("named").unwrap().title.as_deref(), Some("Tên từ Khám phá"), "a known title is kept");
        queued(&sources, "untitled", "preparing");
        sources.on_event(&json!({"event":"source_items", "scan_id":"untitled", "items":[{"identity_key":"bilibili:y", "title":null}]}));
        let status = sources.status("untitled").unwrap();
        assert_eq!((status.state.as_str(), status.title), ("downloading", None));
    }

    #[test]
    fn an_untitled_results_folder_takes_the_title_once_known() {
        let output = std::env::temp_dir().join(format!("dubflow-retitle-{}", std::process::id()));
        let _ = fs::remove_dir_all(&output);
        let url = "https://www.bilibili.com/video/BV1LHxQ6gELU";
        let untitled = output.join("BV1LHxQ6gELU - vi").join("link-1");
        fs::create_dir_all(&untitled).unwrap();
        let moved = retitled_output_in(&output, &untitled, url, "Video hay: tập 1", "link-1").unwrap();
        assert_eq!(moved, output.join("Video-hay-tập-1 - vi").join("link-1"));
        assert!(moved.is_dir() && !untitled.exists());
        assert!(!output.join("BV1LHxQ6gELU - vi").exists(), "the empty untitled folder is gone");
        // Already titled: nothing moves.
        assert_eq!(retitled_output_in(&output, &moved, url, "Tên khác", "link-1").unwrap(), moved);
        // An untitled folder that already holds files stays, and so do its siblings.
        let busy = output.join("BV1LHxQ6gELU - vi").join("link-2");
        fs::create_dir_all(&busy).unwrap();
        fs::write(busy.join("ket-qua.mp4"), "x").unwrap();
        fs::create_dir_all(output.join("BV1LHxQ6gELU - vi").join("link-3")).unwrap();
        assert_eq!(retitled_output_in(&output, &busy, url, "Video hay", "link-2").unwrap(), busy);
        let third = output.join("BV1LHxQ6gELU - vi").join("link-3");
        let moved3 = retitled_output_in(&output, &third, url, "Video hay", "link-3").unwrap();
        assert_eq!(moved3, output.join("Video-hay - vi").join("link-3"));
        assert!(busy.join("ket-qua.mp4").is_file(), "the shared untitled folder keeps other links");
        // A folder that is missing is created under the titled name.
        let gone = output.join("BV1LHxQ6gELU - vi").join("link-4");
        let made = retitled_output_in(&output, &gone, url, "Video hay", "link-4").unwrap();
        assert!(made.is_dir() && made.ends_with(Path::new("Video-hay - vi").join("link-4")));
        // A title that names the folder the same way changes nothing.
        let same = output.join("BV1LHxQ6gELU - vi").join("link-5");
        assert_eq!(retitled_output_in(&output, &same, url, "  ", "link-5").unwrap(), same);
        fs::remove_dir_all(&output).unwrap();
    }

    #[test]
    fn a_failed_link_keeps_the_service_code() {
        let sources = Arc::new(Sources::default());
        queued(&sources, "d", "preparing");
        sources.on_event(&json!({"event":"source_error", "scan_id":"d", "code":"AUTH_REQUIRED"}));
        let status = sources.status("d").unwrap();
        assert_eq!((status.state.as_str(), status.error_code.as_deref()), ("failed", Some("AUTH_REQUIRED")));
    }

    #[test]
    fn titles_are_trimmed_and_bounded() {
        assert_eq!(clean_title(Some("  Phim hay \n".into())), Some("Phim hay".into()));
        assert_eq!(clean_title(Some("   ".into())), None);
        assert_eq!(clean_title(None), None);
        assert_eq!(clean_title(Some("长".repeat(300))).unwrap().chars().count(), 200);
    }

    #[test]
    fn saved_sessions_are_reused_for_twenty_hours() {
        let now = 1_000_000;
        assert!(session_fresh(now - 60, now));
        assert!(!session_fresh(now - SESSION_REUSE_SECONDS, now));
        assert!(!session_fresh(now + 60, now), "a save time in the future is not trusted");
    }

    #[test]
    fn download_file_names_are_safe_on_windows() {
        assert_eq!(safe_file_name("Tập 1: Ai là \"người\" thắng?"), "Tập 1 Ai là người thắng");
        assert_eq!(safe_file_name(" ..a/b\\c|d*e<f>g. . "), "a b c d e f g");
        assert_eq!(safe_file_name("Phim: thử/chép?"), "Phim thử chép");
        assert_eq!(safe_file_name("a :: b\tc"), "a b c");
        assert_eq!(safe_file_name("...  "), "video");
        assert_eq!(safe_file_name("\u{7}\t"), "video");
        assert_eq!(safe_file_name(&"长".repeat(150)).chars().count(), 120);
        assert_eq!(safe_file_name(&format!("{}. x", "a".repeat(119))), "a".repeat(119));
        assert_eq!(safe_file_name("con"), "con_");
        assert_eq!(safe_file_name("仙侠名场面 #仙侠"), "仙侠名场面 #仙侠");
        assert_eq!(numbered_file_name("Clip", "mp4", 1), "Clip.mp4");
        assert_eq!(numbered_file_name("Clip", "mp4", 3), "Clip (3).mp4");
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
        let dir = link_output_dir("https://archive.org/download/ElephantsDream/ed_1024_512kb.mp4?x=1", None, "link-1").unwrap();
        assert!(dir.ends_with(Path::new("DubFlow Output").join("ed_1024_512kb - vi").join("link-1")));
        let odd = link_output_dir("https://www.bilibili.com/video/", None, "link-2").unwrap();
        assert!(odd.to_string_lossy().contains("video - vi"));
        assert!(!link_output_dir("https://x.test/..%2f..", None, "s").unwrap().to_string_lossy().contains(".."));
        let titled = link_output_dir("https://v.douyin.com/abc/", Some("  Tiên hiệp: Linh kiếm  thức tỉnh! "), "link-3").unwrap();
        assert!(titled.ends_with(Path::new("DubFlow Output").join("Tiên-hiệp-Linh-kiếm-thức-tỉnh - vi").join("link-3")));
        assert_eq!(link_folder_name("https://x.test/a", Some(&"长".repeat(80))), "长".repeat(60));
        assert_eq!(link_folder_name("https://x.test/clip.mp4", Some(" ?! ")), "video");
    }
}
