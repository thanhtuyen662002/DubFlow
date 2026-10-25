//! Video discovery: open a platform's search or creator page in an app-owned
//! browser window, then read the video links the real page has rendered.
//!
//! Search and creator lists on Douyin and Kuaishou need browser signatures
//! that change often. Letting the site's own page fetch them, and only reading
//! the resulting DOM, avoids re-implementing those signatures.

use serde::{Deserialize, Serialize};

pub const WINDOW: &str = "discover";
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

/// Runs inside the platform page and returns a JSON string of the video
/// cards currently in the DOM (and, on Kuaishou, its Apollo cache, which
/// also carries the direct media URL).
pub const COLLECT_SCRIPT: &str = r##"(() => {
  const items = new Map();
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 200);
  const abs = (value) => { try { return new URL(value, location.href).href; } catch (e) { return ""; } };
  const add = (page, title, cover, author, media) => {
    page = abs(page).split("?")[0].split("#")[0];
    if (!page || items.has(page)) return;
    items.set(page, { page_url: page, title: clean(title), cover_url: abs(cover || ""), author: clean(author), media_url: media ? abs(media) : "" });
  };
  const cardOf = (a) => a.closest("li, [class*='card'], [class*='item'], [class*='video']") || a;
  const coverOf = (el) => { const img = el.querySelector("img"); return img ? (img.currentSrc || img.src || img.getAttribute("data-src") || "") : ""; };
  const host = location.hostname;
  try {
    if (host.endsWith("kuaishou.com")) {
      const state = (window.__APOLLO_STATE__ || {}).defaultClient || {};
      for (const value of Object.values(state)) {
        if (value && value.id && (value.photoUrl || value.coverUrl) && (value.caption !== undefined)) {
          add("https://www.kuaishou.com/short-video/" + value.id, value.caption, value.coverUrl, "", value.photoUrl);
        }
      }
      document.querySelectorAll("a[href*='/short-video/']").forEach((a) => { const card = cardOf(a); add(a.href, a.title || card.innerText, coverOf(card), "", ""); });
    } else if (host.endsWith("bilibili.com")) {
      document.querySelectorAll("a[href*='/video/BV']").forEach((a) => {
        const match = a.href.match(/\/video\/(BV[0-9A-Za-z]{10})/);
        if (!match) return;
        const card = cardOf(a);
        const title = a.getAttribute("title") || (card.querySelector("h3") || {}).title || (card.querySelector("h3") || a).innerText;
        const author = (card.querySelector("[class*='author'], [class*='owner'], [class*='up-name']") || {}).innerText;
        add("https://www.bilibili.com/video/" + match[1], title, coverOf(card), author, "");
      });
    } else if (host.endsWith("douyin.com")) {
      document.querySelectorAll("a[href*='/video/']").forEach((a) => {
        const match = a.href.match(/\/video\/(\d{8,24})/);
        if (!match) return;
        const card = cardOf(a);
        const author = (card.querySelector("[class*='author'], [class*='nickname']") || {}).innerText;
        add("https://www.douyin.com/video/" + match[1], card.innerText || a.innerText, coverOf(card), author, "");
      });
    }
  } catch (error) {
    return JSON.stringify({ error: String(error).slice(0, 200) });
  }
  return JSON.stringify({ items: Array.from(items.values()).slice(0, 300), url: location.href });
})()"##;

pub const SCROLL_SCRIPT: &str = "window.scrollBy({ top: Math.round(window.innerHeight * 2.5), behavior: 'smooth' });";

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
            cover_url: cover.map(|url| url.to_string()), author: short(&item.author), fetch_url });
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
    fn page_errors_and_unready_pages_are_reported() {
        let error = serde_json::to_string(&serde_json::json!({"error": "boom"}).to_string()).unwrap();
        assert!(parse_collection(Platform::Douyin, &error).unwrap_err().contains("boom"));
        assert!(parse_collection(Platform::Douyin, "null").is_err());
        assert!(parse_collection(Platform::Douyin, "{}").is_err());
    }
}
