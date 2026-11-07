//! The Douyin sign-in, kept outside the per-version WebView2 profile so an
//! update never logs the user out.
//!
//! Each DubFlow version has its own WebView2 profile. A new version's first
//! start copies the previous profile's cookie store (`inherit_previous_profile`),
//! but that copy can miss: the previous version's WebView2 may still hold the
//! store locked while the update starts the new one, or an older version that
//! is still open may look like the newest profile. So whenever the profile is
//! logged in, DubFlow also keeps the douyin.com cookies in its data folder,
//! encrypted with Windows DPAPI for this Windows user, and puts them back into
//! a profile that has lost the sign-in. "Đăng xuất" deletes the copy.

use std::fs;
use std::path::Path;

use serde::{Deserialize, Serialize};
use tauri::webview::cookie::{time::OffsetDateTime, Cookie, Expiration, SameSite};

/// The saved copy, below the data root's `control` folder.
pub(crate) const LOGIN_FILE: &str = "douyin-login.bin";
const SCHEMA_VERSION: u64 = 1;
/// More than any real douyin.com cookie jar; anything larger is not ours.
const MAX_COOKIES: usize = 200;
const MAX_FILE_BYTES: u64 = 1024 * 1024;

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub(crate) struct SavedCookie {
    pub name: String,
    pub value: String,
    pub domain: String,
    pub path: String,
    /// Unix seconds; `None` for a cookie that ends with the browser session.
    pub expires: Option<i64>,
    pub http_only: bool,
    pub secure: bool,
    pub same_site: Option<String>,
}

#[derive(Serialize, Deserialize)]
struct SavedLogin {
    schema_version: u64,
    cookies: Vec<SavedCookie>,
}

pub(crate) fn is_douyin_domain(domain: &str) -> bool {
    let domain = domain.trim_start_matches('.').to_ascii_lowercase();
    domain == "douyin.com" || domain.ends_with(".douyin.com")
}

/// Douyin's login cookie is there and not empty.
pub(crate) fn has_login<'a>(cookies: impl IntoIterator<Item = (&'a str, &'a str)>) -> bool {
    cookies.into_iter().any(|(name, value)| (name == "sessionid" || name == "sessionid_ss") && !value.is_empty())
}

fn saved_has_login(cookies: &[SavedCookie]) -> bool {
    has_login(cookies.iter().map(|cookie| (cookie.name.as_str(), cookie.value.as_str())))
}

pub(crate) fn from_cookie(cookie: &Cookie<'_>) -> SavedCookie {
    SavedCookie {
        name: cookie.name().to_owned(),
        value: cookie.value().to_owned(),
        domain: cookie.domain().unwrap_or_default().to_owned(),
        path: cookie.path().unwrap_or("/").to_owned(),
        expires: cookie.expires_datetime().map(|at| at.unix_timestamp()),
        http_only: cookie.http_only().unwrap_or(false),
        secure: cookie.secure().unwrap_or(false),
        same_site: cookie.same_site().map(|same_site| same_site.to_string()),
    }
}

/// The domain to give WebView2. Reading a cookie drops the leading dot of a
/// domain cookie (`.douyin.com` reads as `douyin.com`), and WebView2 treats
/// `douyin.com` as that host only, so www.douyin.com would not get it. Douyin
/// sets its sign-in on `.douyin.com`, so the bare site name gets its dot back;
/// the extra dot here is the one the cookie crate strips on the way out.
fn webview_domain(domain: &str) -> String {
    let domain = domain.trim_start_matches('.');
    if domain.eq_ignore_ascii_case("douyin.com") { "..douyin.com".to_owned() } else { domain.to_owned() }
}

pub(crate) fn to_cookie(saved: &SavedCookie) -> Cookie<'static> {
    let mut cookie = Cookie::build((saved.name.clone(), saved.value.clone()))
        .domain(webview_domain(&saved.domain))
        .path(if saved.path.is_empty() { "/".to_owned() } else { saved.path.clone() })
        .http_only(saved.http_only)
        .secure(saved.secure);
    if let Some(at) = saved.expires.and_then(|at| OffsetDateTime::from_unix_timestamp(at).ok()) {
        cookie = cookie.expires(Expiration::DateTime(at));
    }
    let same_site = match saved.same_site.as_deref() {
        Some("Strict") => Some(SameSite::Strict),
        Some("Lax") => Some(SameSite::Lax),
        Some("None") => Some(SameSite::None),
        _ => None,
    };
    if let Some(same_site) = same_site {
        cookie = cookie.same_site(same_site);
    }
    cookie.build()
}

/// The cookies worth keeping: douyin.com ones with a name, not yet expired.
pub(crate) fn keep(cookies: impl IntoIterator<Item = SavedCookie>, now: i64) -> Vec<SavedCookie> {
    cookies.into_iter()
        .filter(|cookie| !cookie.name.is_empty() && is_douyin_domain(&cookie.domain))
        .filter(|cookie| !matches!(cookie.expires, Some(at) if at <= now))
        .take(MAX_COOKIES)
        .collect()
}

pub(crate) fn encode(cookies: &[SavedCookie]) -> Vec<u8> {
    serde_json::to_vec(&SavedLogin { schema_version: SCHEMA_VERSION, cookies: cookies.to_vec() }).unwrap_or_default()
}

/// The saved cookies still worth restoring, or `None` when they no longer
/// hold a sign-in (unreadable, another schema, or the login cookie expired).
pub(crate) fn decode(bytes: &[u8], now: i64) -> Option<Vec<SavedCookie>> {
    let saved: SavedLogin = serde_json::from_slice(bytes).ok()?;
    if saved.schema_version != SCHEMA_VERSION {
        return None;
    }
    let cookies = keep(saved.cookies, now);
    saved_has_login(&cookies).then_some(cookies)
}

/// Keep `cookies` as the saved sign-in when they hold one. Returns whether it saved.
pub(crate) fn save(path: &Path, cookies: Vec<SavedCookie>, now: i64) -> Result<bool, String> {
    let cookies = keep(cookies, now);
    if !saved_has_login(&cookies) {
        return Ok(false);
    }
    let sealed = protect(&encode(&cookies))?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let partial = path.with_extension("bin.part");
    fs::write(&partial, sealed).and_then(|_| fs::rename(&partial, path)).map_err(|error| error.to_string())?;
    Ok(true)
}

/// The saved sign-in, when there is one that can still be used.
pub(crate) fn load(path: &Path, now: i64) -> Option<Vec<SavedCookie>> {
    if fs::metadata(path).ok()?.len() > MAX_FILE_BYTES {
        return None;
    }
    decode(&unprotect(&fs::read(path).ok()?).ok()?, now)
}

pub(crate) fn forget(path: &Path) {
    let _ = fs::remove_file(path);
}

#[cfg(windows)]
fn dpapi(data: &[u8], seal: bool) -> Result<Vec<u8>, String> {
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{CryptProtectData, CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB};
    let input = CRYPT_INTEGER_BLOB { cbData: u32::try_from(data.len()).map_err(|error| error.to_string())?, pbData: data.as_ptr() as *mut u8 };
    let mut output = CRYPT_INTEGER_BLOB { cbData: 0, pbData: std::ptr::null_mut() };
    // SAFETY: `input` points at `data` for the call; Windows allocates
    // `output`, which is copied and then released with LocalFree.
    let ok = unsafe {
        if seal {
            CryptProtectData(&input, std::ptr::null(), std::ptr::null(), std::ptr::null(), std::ptr::null(), CRYPTPROTECT_UI_FORBIDDEN, &mut output)
        } else {
            CryptUnprotectData(&input, std::ptr::null_mut(), std::ptr::null(), std::ptr::null(), std::ptr::null(), CRYPTPROTECT_UI_FORBIDDEN, &mut output)
        }
    };
    if ok == 0 || output.pbData.is_null() {
        return Err(std::io::Error::last_os_error().to_string());
    }
    // SAFETY: Windows returned `cbData` bytes at `pbData`.
    let bytes = unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize) }.to_vec();
    unsafe { LocalFree(output.pbData.cast()) };
    Ok(bytes)
}

#[cfg(windows)]
fn protect(data: &[u8]) -> Result<Vec<u8>, String> {
    dpapi(data, true)
}

#[cfg(windows)]
fn unprotect(data: &[u8]) -> Result<Vec<u8>, String> {
    dpapi(data, false)
}

// Only Windows keeps a copy; elsewhere (tests on other systems) nothing is saved.
#[cfg(not(windows))]
fn protect(_data: &[u8]) -> Result<Vec<u8>, String> {
    Err("not supported".into())
}

#[cfg(not(windows))]
fn unprotect(_data: &[u8]) -> Result<Vec<u8>, String> {
    Err("not supported".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cookie(name: &str, value: &str, domain: &str, expires: Option<i64>) -> SavedCookie {
        SavedCookie {
            name: name.into(), value: value.into(), domain: domain.into(), path: "/".into(),
            expires, http_only: true, secure: true, same_site: Some("None".into()),
        }
    }

    #[test]
    fn only_unexpired_douyin_cookies_are_kept() {
        let kept = keep([
            cookie("sessionid", "abc", ".douyin.com", Some(2_000)),
            cookie("ttwid", "t", "www.douyin.com", None),
            cookie("old", "x", ".douyin.com", Some(500)),
            cookie("other", "y", ".notdouyin.com", None),
            cookie("", "z", ".douyin.com", None),
        ], 1_000);
        let names: Vec<_> = kept.iter().map(|cookie| cookie.name.as_str()).collect();
        assert_eq!(names, ["sessionid", "ttwid"]);
    }

    #[test]
    fn a_saved_login_round_trips_until_its_login_cookie_expires() {
        let cookies = vec![cookie("sessionid", "abc", ".douyin.com", Some(2_000)), cookie("ttwid", "t", ".douyin.com", None)];
        let bytes = encode(&cookies);
        assert_eq!(decode(&bytes, 1_000), Some(cookies));
        assert_eq!(decode(&bytes, 3_000), None, "an expired login is not restored");
        assert_eq!(decode(b"not json", 1_000), None);
        assert_eq!(decode(br#"{"schema_version":2,"cookies":[]}"#, 1_000), None);
        let visitor = encode(&[cookie("ttwid", "t", ".douyin.com", None)]);
        assert_eq!(decode(&visitor, 1_000), None, "a visitor session is not a sign-in");
    }

    #[test]
    fn cookies_convert_both_ways_with_their_attributes() {
        let saved = cookie("sessionid", "abc", ".douyin.com", Some(1_900_000_000));
        let real = to_cookie(&saved);
        // WebView2 receives ".douyin.com", so www.douyin.com gets the cookie too.
        assert_eq!(real.domain(), Some(".douyin.com"));
        assert_eq!(to_cookie(&cookie("a", "b", "www.douyin.com", None)).domain(), Some("www.douyin.com"));
        assert_eq!(real.http_only(), Some(true));
        assert_eq!(real.same_site(), Some(SameSite::None));
        let back = from_cookie(&real);
        assert_eq!((back.name.as_str(), back.value.as_str(), back.expires), ("sessionid", "abc", Some(1_900_000_000)));
        assert!(is_douyin_domain(&back.domain));
        let session = to_cookie(&cookie("ttwid", "t", ".douyin.com", None));
        assert_eq!(from_cookie(&session).expires, None);
    }

    #[test]
    fn a_profile_without_a_login_saves_nothing() {
        let dir = std::env::temp_dir().join(format!("dubflow-douyin-login-{}", std::process::id()));
        let path = dir.join(LOGIN_FILE);
        assert_eq!(save(&path, vec![cookie("ttwid", "t", ".douyin.com", None)], 1_000), Ok(false));
        assert!(!path.exists());
        let _ = fs::remove_dir_all(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn the_login_survives_on_disk_encrypted() {
        let dir = std::env::temp_dir().join(format!("dubflow-douyin-login-win-{}", std::process::id()));
        let path = dir.join(LOGIN_FILE);
        let cookies = vec![cookie("sessionid", "secret-value", ".douyin.com", Some(2_000))];
        assert_eq!(save(&path, cookies.clone(), 1_000), Ok(true));
        let raw = fs::read(&path).unwrap();
        assert!(!raw.windows(12).any(|window| window == b"secret-value"), "the file is not plain text");
        assert_eq!(load(&path, 1_000), Some(cookies));
        forget(&path);
        assert_eq!(load(&path, 1_000), None);
        let _ = fs::remove_dir_all(&dir);
    }
}
