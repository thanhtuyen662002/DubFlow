//! Private desktop intake validation. Durable schema/source/worker contracts stay unchanged.
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashSet;

pub const MAX_REFERENCE: usize = 4096;
pub const MAX_IDENTITY: usize = 1024;
pub const MAX_SOURCE_ID: usize = 512;
pub const MAX_PROVIDER: usize = 64;
pub const MAX_ITEMS: usize = 10_000;

#[derive(Clone, Debug, Deserialize)]
pub struct SourceInput {
    pub identity_key: String,
    pub source_id: String,
    pub provider_id: String,
    pub source_url: String,
    pub source_ref: String,
}

#[derive(Debug, Serialize)]
pub struct Rejection {
    pub index: usize,
    pub code: &'static str,
    pub field: &'static str,
}

pub struct Admission {
    pub items: Vec<SourceInput>,
    pub rejected: Vec<Rejection>,
}

pub fn validate(item: &SourceInput) -> Result<(), (&'static str, &'static str)> {
    for (value, field, limit) in [
        (&item.identity_key, "identity_key", MAX_IDENTITY),
        (&item.source_id, "source_id", MAX_SOURCE_ID),
        (&item.provider_id, "provider_id", MAX_PROVIDER),
        (&item.source_url, "source_url", MAX_REFERENCE),
        (&item.source_ref, "source_ref", MAX_REFERENCE),
    ] {
        if value.len() > limit {
            return Err(("INPUT_TOO_LONG", field));
        }
        if value.is_empty() || value.chars().any(char::is_control) {
            return Err(("INPUT_INVALID", field));
        }
    }
    let url = &item.source_url;
    let authority = url.split('/').nth(2).unwrap_or_default();
    if !(url.starts_with("https://") || url.starts_with("http://"))
        || authority.is_empty()
        || authority.contains('@')
        || url.chars().any(char::is_whitespace)
    {
        return Err(("URL_INVALID", "source_url"));
    }
    Ok(())
}

/// Deserialize each item separately: a wrong type/missing field cannot poison
/// the entire batch. Errors contain an index and static codes, never raw URLs.
pub fn admit(values: Vec<Value>) -> Admission {
    let mut result = Admission {
        items: Vec::new(),
        rejected: Vec::new(),
    };
    let mut identities = HashSet::new();
    let mut urls = HashSet::new();
    for (index, value) in values.into_iter().enumerate() {
        let item = serde_json::from_value::<SourceInput>(value);
        let error = match &item {
            Err(_) => Some(("INPUT_INVALID", "item")),
            Ok(item) => validate(item).err().or_else(|| {
                if identities.contains(&item.identity_key) || urls.contains(&item.source_url) {
                    Some(("DUPLICATE_SOURCE", "identity_key"))
                } else if result.items.len() >= MAX_ITEMS {
                    Some(("QUEUE_LIMIT", "items"))
                } else {
                    None
                }
            }),
        };
        if let Some((code, field)) = error {
            result.rejected.push(Rejection { index, code, field });
        } else if let Ok(item) = item {
            identities.insert(item.identity_key.clone());
            urls.insert(item.source_url.clone());
            result.items.push(item);
        }
    }
    result
}
