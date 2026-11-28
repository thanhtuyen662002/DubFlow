//! The Bilibili sign-in for link downloads (#976).
//!
//! Without `SESSDATA` Bilibili only plays its anonymous renditions (480p), so
//! a documentary zoomed to 108% breaks up. A login anywhere in DubFlow's
//! browser profile (the "Đăng nhập Bilibili" dialog, or the Khám phá page)
//! is saved as the link service's protected Bilibili session: 1080p for a
//! normal account, 4K and 1080P+ for 大会员.
//!
//! No cookie value is written here. The state file only says when this app
//! last saved a session and which login it was (a hash of `SESSDATA`), so a
//! new login, a logout on the site or a session about to expire is noticed
//! before the next Bilibili link starts.

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// Cookies Bilibili's player API needs first; the service accepts one Cookie
/// header of at most 2048 bytes and 64 cookies.
pub const PRIORITY: [&str; 13] = ["SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "buvid3", "buvid4", "b_nut",
    "_uuid", "buvid_fp", "sid", "bili_ticket", "bili_ticket_expires", "b_lsid"];
pub const STATE_FILE: &str = "bilibili-session.json";
pub const HOME: &str = "https://www.bilibili.com/";
/// The QR code login: one scan with the Bilibili app.
pub const LOGIN_PAGE: &str = "https://passport.bilibili.com/login";

pub fn is_bilibili_domain(domain: &str) -> bool {
    let domain = domain.trim_start_matches('.').to_ascii_lowercase();
    domain == "bilibili.com" || domain.ends_with(".bilibili.com")
}

/// Which login the cookies hold: a hash of a non-empty `SESSDATA`, or `None`
/// when they are a visitor's.
pub fn login_tag(cookies: &[(String, String)]) -> Option<String> {
    let value = cookies.iter().find(|(name, value)| name == "SESSDATA" && !value.trim().is_empty())?.1.trim();
    let digest = Sha256::digest(value.as_bytes());
    Some(digest.iter().take(12).map(|byte| format!("{byte:02x}")).collect())
}

/// What the state file says: when a session was saved and for which login.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Saved {
    pub at: u64,
    pub login: String,
}

pub fn parse_state(text: &str) -> Option<Saved> {
    let value: Value = serde_json::from_str(text).ok()?;
    if value["schema_version"].as_u64() != Some(1) {
        return None;
    }
    let login = value["login"].as_str().filter(|login| login.len() == 24 && login.bytes().all(|b| b.is_ascii_hexdigit()))?;
    Some(Saved { at: value["saved_at_unix"].as_u64()?, login: login.to_owned() })
}

pub fn state_json(saved_at: u64, login: &str) -> String {
    json!({"schema_version": 1, "saved_at_unix": saved_at, "login": login}).to_string()
}

/// What to do before a Bilibili link starts.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Plan {
    /// The saved session is this login and still fresh.
    Keep,
    /// Save the profile's login (new, another login, or about to expire).
    Save(String),
    /// The profile has no login but a session is saved: remove it, so the
    /// service neither uses a login that ended nor refuses an expired one.
    Clear,
    /// No login anywhere: Bilibili links stay anonymous.
    Anonymous,
}

pub fn plan(profile_login: Option<&str>, saved: Option<&Saved>, now: u64, fresh: impl Fn(u64, u64) -> bool) -> Plan {
    match (profile_login, saved) {
        (Some(login), Some(saved)) if saved.login == login && fresh(saved.at, now) => Plan::Keep,
        (Some(login), _) => Plan::Save(login.to_owned()),
        (None, Some(_)) => Plan::Clear,
        (None, None) => Plan::Anonymous,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn jar(pairs: &[(&str, &str)]) -> Vec<(String, String)> {
        pairs.iter().map(|(name, value)| (name.to_string(), value.to_string())).collect()
    }

    #[test]
    fn only_a_real_sessdata_is_a_login() {
        assert_eq!(login_tag(&jar(&[("buvid3", "x"), ("SESSDATA", "  ")])), None);
        let one = login_tag(&jar(&[("buvid3", "x"), ("SESSDATA", "abc%2C1767225600%2Cdef*12")])).unwrap();
        assert_eq!(one.len(), 24);
        assert_ne!(Some(one.clone()), login_tag(&jar(&[("SESSDATA", "another")])));
        // The tag never carries the cookie value itself.
        assert!(!one.contains("abc"));
    }

    #[test]
    fn bilibili_domains() {
        for good in ["bilibili.com", ".bilibili.com", "www.bilibili.com", "api.BILIBILI.com"] {
            assert!(is_bilibili_domain(good), "{good}");
        }
        for bad in ["evilbilibili.com", "bilibili.com.evil", "b23.tv", ""] {
            assert!(!is_bilibili_domain(bad), "{bad}");
        }
    }

    #[test]
    fn the_state_file_round_trips_and_rejects_anything_else() {
        let login = login_tag(&jar(&[("SESSDATA", "abc")])).unwrap();
        assert_eq!(parse_state(&state_json(1_700_000_000, &login)), Some(Saved { at: 1_700_000_000, login: login.clone() }));
        for bad in ["", "{}", r#"{"schema_version":2,"saved_at_unix":1,"login":"00"}"#,
                    r#"{"schema_version":1,"saved_at_unix":1,"login":"SESSDATA=abc"}"#,
                    r#"{"schema_version":1,"login":"0123456789abcdef01234567"}"#] {
            assert_eq!(parse_state(bad), None, "{bad}");
        }
    }

    #[test]
    fn a_link_saves_a_new_or_stale_login_and_clears_one_that_ended() {
        let fresh = |at: u64, now: u64| now - at < 100;
        let saved = Saved { at: 1_000, login: "aaaaaaaaaaaaaaaaaaaaaaaa".into() };
        assert_eq!(plan(Some("aaaaaaaaaaaaaaaaaaaaaaaa"), Some(&saved), 1_050, fresh), Plan::Keep);
        assert_eq!(plan(Some("aaaaaaaaaaaaaaaaaaaaaaaa"), Some(&saved), 1_200, fresh), Plan::Save("aaaaaaaaaaaaaaaaaaaaaaaa".into()));
        assert_eq!(plan(Some("bbbbbbbbbbbbbbbbbbbbbbbb"), Some(&saved), 1_050, fresh), Plan::Save("bbbbbbbbbbbbbbbbbbbbbbbb".into()));
        assert_eq!(plan(Some("bbbbbbbbbbbbbbbbbbbbbbbb"), None, 1_050, fresh), Plan::Save("bbbbbbbbbbbbbbbbbbbbbbbb".into()));
        assert_eq!(plan(None, Some(&saved), 1_050, fresh), Plan::Clear);
        assert_eq!(plan(None, None, 1_050, fresh), Plan::Anonymous);
    }
}
