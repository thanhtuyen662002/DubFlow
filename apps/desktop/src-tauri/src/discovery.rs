//! Video discovery: show a platform's search or creator page inside the app
//! (a child webview over Khám phá's browser panel), then read the video links
//! the real page has rendered.
//!
//! Search and creator lists on Douyin and Kuaishou need browser signatures
//! that change often. Letting the site's own page fetch them, and only reading
//! the resulting DOM, avoids re-implementing those signatures.

use serde::{Deserialize, Serialize};

/// The discovery page: a child webview of the main window.
pub const VIEW: &str = "discover";
/// Stands in for `VIEW` when child webviews cannot be created: a frameless
/// window owned by the main window, kept over the panel.
pub const FALLBACK_WINDOW: &str = "discover-window";
const MAX_ITEMS: usize = 300;
const MAX_TEXT: usize = 200;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Platform {
    Douyin,
    Bilibili,
    Kuaishou,
}

impl Platform {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "douyin" => Ok(Self::Douyin),
            "bilibili" => Ok(Self::Bilibili),
            "kuaishou" => Ok(Self::Kuaishou),
            _ => Err("Nền tảng không được hỗ trợ".into()),
        }
    }

    fn site(self) -> &'static str {
        match self {
            Self::Douyin => "douyin.com",
            Self::Bilibili => "bilibili.com",
            Self::Kuaishou => "kuaishou.com",
        }
    }
}

/// True when `host` is `domain` or one of its subdomains.
fn on(host: &str, domain: &str) -> bool {
    host == domain || host.ends_with(&format!(".{domain}"))
}

/// Percent-encode everything but RFC 3986 unreserved characters.
fn encode(text: &str) -> String {
    text.bytes().map(|byte| match byte {
        b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => (byte as char).to_string(),
        _ => format!("%{byte:02X}"),
    }).collect()
}

/// The page to open: a keyword search, or a creator/channel page given as a
/// profile URL on the same platform.
pub fn page_url(platform: Platform, kind: &str, query: &str) -> Result<tauri::Url, String> {
    let query = query.trim();
    if query.is_empty() || query.chars().count() > 300 || query.chars().any(char::is_control) {
        return Err("Hãy nhập từ khóa hoặc link kênh hợp lệ".into());
    }
    let text = match kind {
        "search" => {
            let keyword = encode(query);
            match platform {
                Platform::Douyin => format!("https://www.douyin.com/search/{keyword}?type=video"),
                Platform::Bilibili => format!("https://search.bilibili.com/video?keyword={keyword}"),
                Platform::Kuaishou => format!("https://www.kuaishou.com/search/video?searchKey={keyword}"),
            }
        }
        "creator" => {
            let url = tauri::Url::parse(query).map_err(|_| "Hãy dán link trang kênh (bắt đầu bằng https://)".to_string())?;
            let host = url.host_str().unwrap_or_default();
            if url.scheme() != "https" || !on(host, platform.site()) {
                return Err(format!("Link kênh phải thuộc {}", platform.site()));
            }
            url.to_string()
        }
        _ => return Err("Kiểu tìm không hợp lệ".into()),
    };
    tauri::Url::parse(&text).map_err(|error| error.to_string())
}

/// Installed before any page script in the discovery page. It copies video
/// entries out of the JSON the page itself receives (Douyin `aweme` objects,
/// Kuaishou `photo` objects), so lists that only render after a signed API
/// call, or in a virtualised player, can still be collected. Read-only: the
/// page's requests and responses are not changed.
pub const CAPTURE_SCRIPT: &str = r##"(() => {
  if (window.__dubflowCapture) return;
  const found = new Map();
  window.__dubflowCapture = found;
  const first = (value) => Array.isArray(value) ? value[0] : value;
  const text = (value) => typeof value === "string" ? value : "";
  // Newest last, with the address the page showed when it fetched the video.
  const remember = (key, item) => {
    found.delete(key);
    found.set(key, { ...item, at: location.href });
    if (found.size > 1000) found.delete(found.keys().next().value);
  };
  const walk = (node, depth, budget) => {
    if (!node || typeof node !== "object" || depth > 14 || budget.left-- <= 0) return;
    if (Array.isArray(node)) { for (const child of node) walk(child, depth + 1, budget); return; }
    if (/^\d{8,24}$/.test(text(node.aweme_id)) && typeof node.desc === "string") {
      const cover = node.video && node.video.cover && first(node.video.cover.url_list);
      remember("dy" + node.aweme_id, { page_url: "https://www.douyin.com/video/" + node.aweme_id, title: node.desc,
        cover_url: text(cover), author: text(node.author && node.author.nickname), media_url: "" });
    }
    const photo = node.photo && typeof node.photo === "object" ? node.photo : node;
    const media = text(photo.photoUrl) || text(first(photo.photoUrls) && first(photo.photoUrls).url) || text(first(photo.mainMvUrls) && first(photo.mainMvUrls).url);
    if (text(photo.id) && media && (photo.caption !== undefined || photo.coverUrl)) {
      remember("ks" + photo.id, { page_url: "https://www.kuaishou.com/short-video/" + photo.id, title: text(photo.caption),
        cover_url: text(photo.coverUrl), author: text(node.author && node.author.name), media_url: media });
    }
    for (const key in node) walk(node[key], depth + 1, budget);
  };
  const inspect = (body) => {
    if (typeof body !== "string" || body.length > 8000000 || !/aweme_id|photoUrl|mainMvUrls/.test(body)) return;
    try { walk(JSON.parse(body), 0, { left: 50000 }); } catch (error) {}
  };
  const originalFetch = window.fetch;
  if (originalFetch) {
    window.fetch = function (...args) {
      return originalFetch.apply(this, args).then((response) => {
        try { response.clone().text().then(inspect, () => {}); } catch (error) {}
        return response;
      });
    };
  }
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener("load", () => {
      try { inspect(this.responseType === "" || this.responseType === "text" ? this.responseText : (this.responseType === "json" ? JSON.stringify(this.response) : "")); } catch (error) {}
    });
    return send.apply(this, args);
  };
})();"##;

/// Runs inside the platform page and returns a JSON string of the videos the
/// page shows: first the video it is playing (on a video page, flagged
/// `current`), then the video cards that are rendered, in page order, each
/// titled from its own card. The JSON the page fetched (`CAPTURE_SCRIPT`,
/// Kuaishou's Apollo cache) only fills in details of those cards; it lists
/// videos by itself only when the page draws its list without links, and
/// then only what was fetched at the current address. Hidden menus and
/// pages visited earlier never add videos.
pub const COLLECT_SCRIPT: &str = r##"(() => {
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 200);
  const abs = (value) => { try { return new URL(value, location.href).href; } catch (e) { return ""; } };
  const host = location.hostname;
  const site = host.endsWith("kuaishou.com") ? "kuaishou" : host.endsWith("bilibili.com") ? "bilibili" : host.endsWith("douyin.com") ? "douyin" : "";
  const idOf = (href) => {
    const link = String(href || "");
    let match = null;
    if (site === "bilibili") match = link.match(/\/video\/(BV[0-9A-Za-z]{10})/);
    else if (site === "douyin") match = link.match(/\/video\/(\d{8,24})/) || link.match(/[?&]modal_id=(\d{8,24})/);
    else if (site === "kuaishou") match = link.match(/\/short-video\/([0-9A-Za-z_-]{4,40})/);
    return match ? match[1] : "";
  };
  const pageOf = (id) => site === "bilibili" ? "https://www.bilibili.com/video/" + id
    : site === "douyin" ? "https://www.douyin.com/video/" + id : "https://www.kuaishou.com/short-video/" + id;
  // A title is text, not a duration, a count or a date.
  const titled = (value) => { const text = clean(value); return text.length >= 2 && !/^[\d\s:.,·万亿wW+\-/年月日前小时分钟天]*$/.test(text) ? text : ""; };
  const shown = (el) => {
    if (!el.getClientRects().length) return false;
    const box = el.getBoundingClientRect();
    if (box.width < 4 || box.height < 4) return false;
    const style = getComputedStyle(el);
    return style.visibility !== "hidden" && style.display !== "none";
  };
  const links = (root) => Array.from(root.querySelectorAll("a[href]")).filter((a) => idOf(a.href));
  // The card of a link: its largest ancestor that links to no other video.
  const cardOf = (a, id) => {
    let card = a;
    for (let depth = 0; depth < 8; depth++) {
      const parent = card.parentElement;
      if (!parent || parent === document.body || links(parent).some((other) => idOf(other.href) !== id)) break;
      card = parent;
    }
    return card;
  };
  const coverOf = (el) => { const img = el.querySelector("img"); return img ? (img.currentSrc || img.src || img.getAttribute("data-src") || "") : ""; };
  const titleOf = (a, card) => {
    const tries = [a.getAttribute("title")];
    for (const el of card.querySelectorAll("[title]")) tries.push(el.getAttribute("title"));
    for (const el of card.querySelectorAll("h1, h2, h3, h4, [class*='title'], [class*='desc'], [class*='caption']")) tries.push(el.innerText);
    for (const img of card.querySelectorAll("img[alt]")) tries.push(img.getAttribute("alt"));
    tries.push(a.innerText);
    for (const line of String(card.innerText || "").split("\n")) tries.push(line);
    for (const value of tries) { const text = titled(value); if (text) return text; }
    return "";
  };
  const authorOf = (card) => clean((card.querySelector("[class*='author'], [class*='owner'], [class*='up-name'], [class*='upname'], [class*='nickname']") || {}).innerText);
  const meta = (name) => (document.querySelector(`meta[property='${name}'], meta[name='${name}'], meta[itemprop='${name}']`) || {}).content || "";

  const items = new Map();
  const add = (id, data, current) => {
    if (!id) return;
    const item = items.get(id) || { page_url: pageOf(id), title: "", cover_url: "", author: "", media_url: "", current: false };
    item.title = item.title || clean(data.title);
    item.cover_url = item.cover_url || (data.cover ? abs(data.cover) : "");
    item.author = item.author || clean(data.author);
    item.media_url = item.media_url || (data.media ? abs(data.media) : "");
    item.current = item.current || Boolean(current);
    items.set(id, item);
  };
  // What the page's own JSON said about each video, by id.
  const captured = new Map();
  try {
    for (const item of (window.__dubflowCapture || new Map()).values()) {
      const id = idOf(item.page_url);
      if (id) captured.set(id, item);
    }
    if (site === "kuaishou") {
      const state = (window.__APOLLO_STATE__ || {}).defaultClient || {};
      for (const value of Object.values(state)) {
        if (value && value.id && (value.photoUrl || value.coverUrl) && value.caption !== undefined && !captured.has(String(value.id))) {
          captured.set(String(value.id), { page_url: pageOf(value.id), title: value.caption, cover_url: value.coverUrl, author: "", media_url: value.photoUrl || "" });
        }
      }
    }
  } catch (error) {}
  const fromCapture = (id) => { const item = captured.get(id); return item ? { title: titled(item.title), cover: item.cover_url, author: item.author, media: item.media_url } : {}; };

  try {
    if (!site) return JSON.stringify({ items: [], url: location.href });
    // 1. The video the page is playing, first.
    const playing = idOf(location.href);
    if (playing) {
      const heading = document.querySelector("h1");
      const title = fromCapture(playing).title || titled(heading && (heading.getAttribute("title") || heading.innerText))
        || titled(meta("og:title").replace(/_哔哩哔哩_bilibili$/, "")) || titled(document.title.replace(/(_哔哩哔哩_bilibili| - 抖音| - 快手)$/, ""));
      add(playing, { ...fromCapture(playing), title, cover: fromCapture(playing).cover || meta("og:image") || meta("image"),
        author: fromCapture(playing).author || clean((document.querySelector("[class*='up-name'], [class*='author-name'], [data-e2e='video-author'] [class*='name']") || {}).innerText) }, true);
    }
    // 2. Video cards the page shows, in page order.
    for (const a of links(document)) {
      if (!shown(a)) continue;
      const id = idOf(a.href);
      const card = cardOf(a, id);
      const known = fromCapture(id);
      add(id, { title: known.title || titleOf(a, card), cover: known.cover || coverOf(card), author: known.author || authorOf(card), media: known.media }, false);
    }
    // 3. Lists drawn without links (Douyin/Kuaishou players): what the page fetched for this address.
    if (items.size <= (playing ? 1 : 0)) {
      for (const [id, item] of captured) {
        if (!item.at || item.at === location.href) add(id, fromCapture(id), false);
      }
    }
  } catch (error) {
    return JSON.stringify({ error: String(error).slice(0, 200) });
  }
  return JSON.stringify({ items: Array.from(items.values()).slice(0, 300), url: location.href });
})()"##;

pub const SCROLL_SCRIPT: &str = "window.scrollBy({ top: Math.round(window.innerHeight * 2.5), behavior: 'smooth' });";
pub const BACK_SCRIPT: &str = "history.back();";

/// Whether a link the page wants in a new window may load in the discovery
/// page instead (web pages only; never a separate window).
pub fn loads_in_place(url: &tauri::Url) -> bool {
    matches!(url.scheme(), "https" | "http") && url.host_str().is_some() && url.as_str().len() <= 4096
}

#[derive(Debug, Deserialize)]
struct RawItem {
    page_url: String,
    #[serde(default)]
    title: String,
    #[serde(default)]
    cover_url: String,
    #[serde(default)]
    author: String,
    #[serde(default)]
    media_url: String,
    #[serde(default)]
    current: bool,
}

#[derive(Debug, Deserialize)]
struct RawCollection {
    #[serde(default)]
    items: Vec<RawItem>,
    error: Option<String>,
}

#[derive(Debug, Serialize, PartialEq)]
pub struct DiscoveredVideo {
    pub page_url: String,
    pub title: String,
    pub cover_url: Option<String>,
    pub author: String,
    /// What to download: the page itself, or (Kuaishou) the media file the page plays.
    pub fetch_url: String,
    /// The video the page is playing (a video page), not a card on it.
    pub current: bool,
}

fn https_on(value: &str, domains: &[&str]) -> Option<tauri::Url> {
    let url = tauri::Url::parse(value).ok()?;
    let host = url.host_str()?.to_owned();
    (url.scheme() == "https" && value.len() <= 2048 && domains.iter().any(|domain| on(&host, domain))).then_some(url)
}

fn short(text: &str) -> String {
    text.chars().filter(|c| !c.is_control()).take(MAX_TEXT).collect::<String>().trim().to_owned()
}

/// Validate what the page script returned. The page is untrusted: only
/// HTTPS links on the platform (and Kuaishou's media CDNs) are kept.
pub fn parse_collection(platform: Platform, json_text: &str) -> Result<Vec<DiscoveredVideo>, String> {
    // eval_with_callback serialises the script's return value, a JSON string, as JSON again.
    let inner: String = serde_json::from_str(json_text).map_err(|_| "Trang chưa sẵn sàng; hãy đợi trang tải xong rồi thử lại".to_string())?;
    let raw: RawCollection = serde_json::from_str(&inner).map_err(|_| "Không đọc được danh sách video trên trang".to_string())?;
    if let Some(error) = raw.error {
        return Err(format!("Không đọc được trang: {}", short(&error)));
    }
    let mut items = Vec::new();
    for item in raw.items.into_iter().take(MAX_ITEMS) {
        let Some(page) = https_on(&item.page_url, &[platform.site()]) else { continue };
        let media = (platform == Platform::Kuaishou && !item.media_url.is_empty())
            .then(|| https_on(&item.media_url, &["kwaicdn.com", "kuaishou.com", "yximgs.com", "kwimgs.com"]))
            .flatten();
        let cover = https_on(&item.cover_url, &[platform.site(), "hdslb.com", "douyinpic.com", "douyinstatic.com", "byteimg.com", "kwaicdn.com", "yximgs.com", "kwimgs.com"]);
        let page_url = page.to_string();
        // Kuaishou pages are not a download source; without a media URL the item cannot be fetched.
        let fetch_url = match (platform, media) {
            (Platform::Kuaishou, Some(media)) => media.to_string(),
            (Platform::Kuaishou, None) => continue,
            _ => page_url.clone(),
        };
        let title = short(&item.title);
        items.push(DiscoveredVideo { page_url, title: if title.is_empty() { "(không có tiêu đề)".into() } else { title },
            cover_url: cover.map(|url| url.to_string()), author: short(&item.author), fetch_url, current: item.current });
    }
    Ok(items)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn search_and_creator_pages_stay_on_the_platform() {
        assert_eq!(page_url(Platform::Douyin, "search", "仙侠 动漫").unwrap().as_str(), "https://www.douyin.com/search/%E4%BB%99%E4%BE%A0%20%E5%8A%A8%E6%BC%AB?type=video");
        assert_eq!(page_url(Platform::Bilibili, "search", "美食").unwrap().as_str(), "https://search.bilibili.com/video?keyword=%E7%BE%8E%E9%A3%9F");
        assert!(page_url(Platform::Kuaishou, "search", "a&b=c").unwrap().as_str().ends_with("searchKey=a%26b%3Dc"));
        assert_eq!(page_url(Platform::Bilibili, "creator", "https://space.bilibili.com/546195/video").unwrap().as_str(), "https://space.bilibili.com/546195/video");
        for bad in ["http://space.bilibili.com/1", "https://evilbilibili.com/1", "https://www.douyin.com/user/x", "not a url"] {
            assert!(page_url(Platform::Bilibili, "creator", bad).is_err(), "{bad}");
        }
        assert!(page_url(Platform::Douyin, "search", "  ").is_err());
        assert!(page_url(Platform::Douyin, "feed", "x").is_err());
        assert!(Platform::parse("tiktok").is_err());
    }

    fn collected(items: serde_json::Value) -> String {
        serde_json::to_string(&serde_json::json!({ "items": items }).to_string()).unwrap()
    }

    #[test]
    fn collection_keeps_only_platform_links_and_kuaishou_media() {
        let bilibili = collected(serde_json::json!([
            {"page_url": "https://www.bilibili.com/video/BV1gKJ86JEuP", "title": "  A\ntitle ", "cover_url": "https://i0.hdslb.com/x.jpg", "author": "UP"},
            {"page_url": "https://evil.example/video/BV1gKJ86JEuP", "title": "x"},
            {"page_url": "http://www.bilibili.com/video/BV1iqpt6cEMU", "title": "plain http"},
            {"page_url": "https://www.bilibili.com/video/BV1iqpt6cEMU", "title": "", "cover_url": "https://tracker.example/p.gif"}
        ]));
        let items = parse_collection(Platform::Bilibili, &bilibili).unwrap();
        assert_eq!(items.len(), 2);
        assert_eq!(items[0].title, "A\ntitle".replace('\n', ""));
        assert_eq!(items[0].fetch_url, "https://www.bilibili.com/video/BV1gKJ86JEuP");
        assert_eq!(items[1].title, "(không có tiêu đề)");
        assert_eq!(items[1].cover_url, None);

        let kuaishou = collected(serde_json::json!([
            {"page_url": "https://www.kuaishou.com/short-video/3x1", "title": "t", "media_url": "https://v2.kwaicdn.com/a.mp4?x=1"},
            {"page_url": "https://www.kuaishou.com/short-video/3x2", "title": "no media"},
            {"page_url": "https://www.kuaishou.com/short-video/3x3", "title": "bad media", "media_url": "https://evil.example/a.mp4"}
        ]));
        let items = parse_collection(Platform::Kuaishou, &kuaishou).unwrap();
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].fetch_url, "https://v2.kwaicdn.com/a.mp4?x=1");
        assert_eq!(items[0].page_url, "https://www.kuaishou.com/short-video/3x1");
    }

    #[test]
    fn the_playing_video_stays_flagged() {
        let page = collected(serde_json::json!([
            {"page_url": "https://www.bilibili.com/video/BV1VKyxQ6xE9", "title": "playing", "current": true},
            {"page_url": "https://www.bilibili.com/video/BV1iqpt6cEMU", "title": "suggested"}
        ]));
        let items = parse_collection(Platform::Bilibili, &page).unwrap();
        assert_eq!(items.iter().map(|item| item.current).collect::<Vec<_>>(), [true, false]);
    }

    #[test]
    fn only_web_links_from_new_windows_load_in_the_page() {
        for good in ["https://www.douyin.com/video/7300000000000000000", "http://passport.kuaishou.com/x"] {
            assert!(loads_in_place(&tauri::Url::parse(good).unwrap()), "{good}");
        }
        for bad in ["javascript:alert(1)", "file:///C:/x", "data:text/html,x", "bytedance://open", "about:blank"] {
            assert!(!loads_in_place(&tauri::Url::parse(bad).unwrap()), "{bad}");
        }
    }

    #[test]
    fn page_errors_and_unready_pages_are_reported() {
        let error = serde_json::to_string(&serde_json::json!({"error": "boom"}).to_string()).unwrap();
        assert!(parse_collection(Platform::Douyin, &error).unwrap_err().contains("boom"));
        assert!(parse_collection(Platform::Douyin, "null").is_err());
        assert!(parse_collection(Platform::Douyin, "{}").is_err());
    }
}
