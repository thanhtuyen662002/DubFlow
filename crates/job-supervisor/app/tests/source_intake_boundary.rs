#[path = "../src/source_intake.rs"]
mod source_intake;
use serde_json::{json, Value};
use source_intake::*;

fn item(identity: &str, url: &str) -> Value {
    json!({"identity_key": identity, "source_id":"source-1", "provider_id":"generic",
        "source_url":url, "source_ref":url})
}

#[test]
fn malformed_and_multibyte_items_do_not_discard_valid_neighbors() {
    let url = "https://example.test/clip,phần2.mp4";
    let exact = "ệ".repeat(341) + "a";
    assert_eq!(exact.len(), MAX_IDENTITY);
    let result = admit(vec![
        item("url:first", url),
        item(&(exact.clone() + "ệ"), "https://example.test/long"),
        json!({"identity_key":4}),
        json!(null),
        item(&exact, "https://example.test/exact"),
        item("url:creds", "https://user:secret@example.test/a"),
        item("url:bad", "https://example.test/\u{85}"),
        item("url:last", "https://example.test/last"),
    ]);
    assert_eq!(result.items.len(), 3);
    assert_eq!(result.items[0].source_url, url);
    assert_eq!(result.items[2].identity_key, "url:last");
    assert_eq!(
        result.rejected.iter().map(|r| r.index).collect::<Vec<_>>(),
        [1, 2, 3, 5, 6]
    );
    assert_eq!(result.rejected[0].code, "INPUT_TOO_LONG");
    assert!(!serde_json::to_string(&result.rejected)
        .unwrap()
        .contains("secret"));
}

#[test]
fn all_field_byte_limits_and_duplicate_rejection_preserve_valid_items() {
    for (field, max) in [
        ("identity_key", MAX_IDENTITY),
        ("source_id", MAX_SOURCE_ID),
        ("provider_id", MAX_PROVIDER),
        ("source_ref", MAX_REFERENCE),
    ] {
        let mut good = item("url:good", "https://example.test/good");
        good[field] = json!("a".repeat(max));
        let mut bad = good.clone();
        bad[field] = json!("a".repeat(max) + "ệ");
        let result = admit(vec![bad, good]);
        assert_eq!(result.items.len(), 1, "{field}");
        assert_eq!(result.rejected[0].code, "INPUT_TOO_LONG");
    }
    let mut long = item("url:a", "https://example.test/a");
    long["source_url"] =
        json!("https://example.test/".to_owned() + &"a".repeat(MAX_REFERENCE - 21));
    assert_eq!(long["source_url"].as_str().unwrap().len(), MAX_REFERENCE);
    let mut over = long.clone();
    over["source_url"] = json!(over["source_url"].as_str().unwrap().to_owned() + "a");
    let admitted = admit(vec![over, long]);
    assert_eq!(admitted.items.len(), 1);
    assert_eq!(admitted.rejected[0].field, "source_url");
    let good = item("url:a", "https://example.test/a");
    let result = admit(vec![
        good.clone(),
        good,
        item("url:b", "https://example.test/b"),
    ]);
    assert_eq!(result.items.len(), 2);
    assert_eq!(result.rejected[0].code, "DUPLICATE_SOURCE");
}

#[test]
fn typescript_producer_matches_native_limits_and_admission() {
    let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let test_file = manifest.join(file!());
    // Cargo may report file!() relative to the workspace (app/tests/...) or
    // package (tests/...), while the standalone Windows harness uses an
    // absolute path. Locate the real repository instead of assuming one form.
    let root = manifest
        .ancestors()
        .chain(test_file.ancestors())
        .find(|path| {
            path.join("apps/desktop/test/source_intake.test.mjs")
                .is_file()
        })
        .expect("the actual checked-in TypeScript producer test must exist");
    // Required Integration must actually execute TS regressions; no missing-node
    // skip. No workflow/registry changes or new package dependencies are needed.
    let output = std::process::Command::new("node")
        .current_dir(&root)
        .args([
            "--experimental-strip-types",
            "apps/desktop/test/source_intake.test.mjs",
            "--wire",
        ])
        .output()
        .expect("Integration needs Node >=22.6 with strip-types");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let wire: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(
        wire["limits"],
        json!({"reference":MAX_REFERENCE,"identity":MAX_IDENTITY,
        "sourceId":MAX_SOURCE_ID,"provider":MAX_PROVIDER})
    );
    for key in ["items", "distinct"] {
        let values = wire[key].as_array().unwrap().clone();
        let count = values.len();
        let result = admit(values);
        assert_eq!(result.items.len(), count);
        assert!(result.rejected.is_empty());
    }
}
