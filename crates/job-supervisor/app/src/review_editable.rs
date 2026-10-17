//! Current corrected editable export through an explicitly admitted processing
//! release. Worker packets grant no path, live-token or publication authority.
use super::review_assets::{
    verify_editable_files, AcceptedAudioStore, EditableFileSpec, MixStorageBudget,
};
use super::review_document::{canonical, closed, decode, identity, text, tick};
use super::review_export::{prepare_with_walk, AdmittedExportInputs};
use super::review_export_publication::{manifest as bundle_manifest, validate_bundle};
use super::review_publication::{token, RecoveryWalk};
use super::review_retention::NativeMixStore;
use super::review_runtime::AdmittedProcessingRuntime;
use super::{SupervisorError, SupervisorResult};
use dubflow_job_state::{review_graph::ReviewGraphState, DurableStore};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::ffi::OsString;
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

const LIMIT: usize = 16 * 1024 * 1024;
const PACKET_LIMIT: usize = 64 * 1024;
const BUNDLE_MANIFEST: &str = "review_export_manifest.json";

fn invalid(message: &str) -> SupervisorError {
    SupervisorError::Invalid(message.into())
}
fn hash(bytes: &[u8]) -> String {
    format!("sha256:{:x}", Sha256::digest(bytes))
}
fn exact(value: &Value) -> SupervisorResult<String> {
    let mut raw = String::new();
    canonical(value, &mut raw)?;
    Ok(raw)
}
fn budget() -> MixStorageBudget {
    MixStorageBudget {
        maximum_bytes: 32 * 1024 * 1024 * 1024,
        minimum_free_bytes: 2 * 1024 * 1024 * 1024,
    }
}
fn fence(store: &DurableStore, state: &ReviewGraphState) -> SupervisorResult<()> {
    if store.review_graph_state(&state.token.job_id)?.as_ref() != Some(state) {
        return Err(invalid("editable current token changed; reload"));
    }
    Ok(())
}
fn metadata(path: &Path, limit: usize, deadline: Instant) -> SupervisorResult<Vec<u8>> {
    if Instant::now() >= deadline {
        return Err(invalid("editable metadata deadline exceeded"));
    }
    let mut file = super::review_assets::regular(path)?;
    let before = file.metadata()?;
    if before.len() == 0 || before.len() > limit as u64 {
        return Err(invalid("editable metadata byte limit exceeded"));
    }
    let mut raw = Vec::new();
    file.by_ref().take(before.len() + 1).read_to_end(&mut raw)?;
    if raw.len() as u64 != before.len()
        || file.metadata()?.len() != before.len()
        || file.metadata()?.modified()? != before.modified()?
        || Instant::now() >= deadline
    {
        return Err(invalid("editable metadata changed while reading"));
    }
    Ok(raw)
}
fn json_bytes(raw: &[u8]) -> SupervisorResult<Value> {
    decode(
        std::str::from_utf8(raw).map_err(|_| invalid("editable metadata is not UTF-8"))?,
        64 * 1024 * 1024,
    )
}

fn integer(value: &Value, positive: bool, wide: bool) -> SupervisorResult<u64> {
    let n = if let Some(s) = value.as_str() {
        let n = s
            .parse::<u64>()
            .map_err(|_| invalid("editable integer invalid"))?;
        if n.to_string() != s {
            return Err(invalid("editable integer is not canonical"));
        }
        n
    } else {
        value
            .as_u64()
            .ok_or_else(|| invalid("editable time must be an integer"))?
    };
    if (positive && n == 0) || (!wide && n > i64::MAX as u64) {
        return Err(invalid("editable integer outside its range"));
    }
    Ok(n)
}
fn gcd(mut a: u64, mut b: u64) -> u64 {
    while b != 0 {
        let c = a % b;
        a = b;
        b = c;
    }
    a
}
fn cue_edge(cue: &Value, edge: &str, numerator: u64, denominator: u64) -> SupervisorResult<u64> {
    let tick_key = format!("{edge}_ticks");
    let ms_key = format!("{edge}_ms");
    let direct = cue
        .get(&tick_key)
        .map(|v| integer(v, false, false))
        .transpose()?;
    let milliseconds = cue
        .get(&ms_key)
        .map(|v| integer(v, false, false))
        .transpose()?;
    let converted = milliseconds
        .map(|ms| -> SupervisorResult<u64> {
            let top = u128::from(ms)
                .checked_mul(u128::from(denominator))
                .ok_or_else(|| invalid("editable clock overflow"))?;
            let bottom = u128::from(numerator) * 1000;
            let ticks = if edge == "end" {
                top.checked_add(bottom - 1)
                    .ok_or_else(|| invalid("editable clock overflow"))?
                    / bottom
            } else {
                top / bottom
            };
            u64::try_from(ticks)
                .ok()
                .filter(|n| *n <= i64::MAX as u64)
                .ok_or_else(|| invalid("editable clock outside signed64 range"))
        })
        .transpose()?;
    if direct.zip(converted).is_some_and(|(a, b)| a != b) {
        return Err(invalid("editable source tick/millisecond aliases conflict"));
    }
    direct
        .or(converted)
        .ok_or_else(|| invalid("editable cue lacks its source clock"))
}

/// Independent integer-only projection; the complete original timeline is also
/// copied/hash-bound as a resource. Confidence/source geometry are never rewritten.
fn projection(source: &Value) -> SupervisorResult<Value> {
    closed(&source["time_base"], &["numerator", "denominator"])?;
    let n = integer(&source["time_base"]["numerator"], true, true)?;
    let d = integer(&source["time_base"]["denominator"], true, true)?;
    let common = gcd(n, d);
    let n = n / common;
    let d = d / common;
    let duration = if source["duration_ticks"].is_null() {
        None
    } else {
        Some(integer(&source["duration_ticks"], false, false)?)
    };
    let cues = source["cues"]
        .as_array()
        .filter(|c| !c.is_empty() && c.len() <= 10000)
        .ok_or_else(|| invalid("editable canonical cues missing"))?;
    let mut ids = HashSet::new();
    let mut result = Vec::new();
    for cue in cues {
        let id = text(&cue["cue_id"], 256)?;
        if !ids.insert(id) {
            return Err(invalid("editable canonical cue identity repeats"));
        }
        let start = cue_edge(cue, "start", n, d)?;
        let end = cue_edge(cue, "end", n, d)?;
        if end <= start || duration.is_some_and(|v| end > v) {
            return Err(invalid("editable canonical cue range invalid"));
        }
        let mut item =
            json!({"cue_id":id,"start_ticks":start.to_string(),"end_ticks":end.to_string()});
        if let Some(assets) = cue.get("asset_ids") {
            let assets = assets
                .as_array()
                .filter(|v| v.len() <= 32)
                .ok_or_else(|| invalid("editable cue assets invalid"))?;
            for value in assets {
                identity(value)?;
            }
            item["asset_ids"] = json!(assets);
        }
        for field in ["track_id", "speaker_id", "voice_id"] {
            if let Some(value) = cue.get(field) {
                text(value, 128)?;
                item[field] = value.clone();
            }
        }
        result.push(item);
    }
    Ok(
        json!({"time_base":{"numerator":n.to_string(),"denominator":d.to_string()},
        "duration_ticks":duration.map(|v|v.to_string()),"cues":result}),
    )
}

fn expected_pack(
    bundle: &Value,
    manifest_hash: &str,
    manifest_size: u64,
    timeline: &Value,
) -> SupervisorResult<Value> {
    let pins = bundle["files"]
        .as_array()
        .ok_or_else(|| invalid("editable bundle pins missing"))?;
    let resource = |role: &str| -> SupervisorResult<&Value> {
        pins.iter()
            .find(|v| v["role"] == role)
            .ok_or_else(|| invalid("editable required bundle role missing"))
    };
    let source = resource("source_video")?;
    let source_name = text(&source["relative_path"], 128)?;
    let extension = source_name
        .strip_prefix("source.")
        .ok_or_else(|| invalid("editable source basename changed"))?;
    let mut assets = vec![
        json!({"asset_id":"source-video","kind":"video","relative_path":format!("media/{source_name}"),
        "format":extension,"content_hash":source["sha256"],"size_bytes":source["size_bytes"]}),
    ];
    for (role, id, kind, format, folder) in [
        (
            "corrected_timeline",
            "canonical-timeline",
            "timeline",
            "json",
            "metadata",
        ),
        ("final_video", "localized-video", "video", "mp4", "media"),
        (
            "captions_srt",
            "vietsub-srt",
            "subtitle",
            "srt",
            "subtitles",
        ),
        (
            "captions_ass",
            "vietsub-ass",
            "subtitle",
            "ass",
            "subtitles",
        ),
        ("qc_report", "qc-report", "metadata", "json", "metadata"),
        (
            "original_audio",
            "source-audio",
            "original_audio",
            "wav",
            "audio",
        ),
        (
            "dialogue_stem",
            "dialogue-stem",
            "dub_audio",
            "wav",
            "audio",
        ),
        ("final_mix", "final-mix", "mixed_audio", "wav", "audio"),
    ] {
        if bundle["mode"] == "b1"
            && ["original_audio", "dialogue_stem", "final_mix"].contains(&role)
        {
            continue;
        }
        let pin = resource(role)?;
        assets.push(json!({"asset_id":id,"kind":kind,"relative_path":format!("{folder}/{id}-{}",text(&pin["relative_path"],128)?),
            "format":format,"content_hash":pin["sha256"],"size_bytes":pin["size_bytes"]}));
    }
    assets.push(json!({"asset_id":"review-bundle-manifest","kind":"metadata",
        "relative_path":"metadata/review-bundle-manifest-review_export_manifest.json","format":"json",
        "content_hash":manifest_hash,"size_bytes":manifest_size.to_string()}));
    let b2 = bundle["mode"] == "b2";
    let mut tracks = Vec::new();
    let mut subtitle = false;
    for group in ["video", "audio", "subtitle"] {
        for asset in &assets {
            let kind = asset["kind"].as_str().unwrap();
            let category = if ["original_audio", "dub_audio", "mixed_audio"].contains(&kind) {
                "audio"
            } else {
                kind
            };
            if category != group {
                continue;
            }
            let id = asset["asset_id"].as_str().unwrap();
            let active = match group {
                "video" => id == "source-video",
                "audio" => b2 && kind == "mixed_audio",
                _ => {
                    let first = !subtitle;
                    subtitle = true;
                    first
                }
            };
            let mut track = json!({"track_id":format!("track-{id}"),"kind":group,"order":tracks.len(),
                "asset_ids":[id],"language":if group == "subtitle" {Some("vi")} else {None},
                "role":kind,"enabled_by_default":active});
            if group == "video" {
                track["mute_embedded_audio"] = (b2 || id != "source-video").into();
            }
            tracks.push(track);
        }
    }
    let projected = projection(timeline)?;
    let asset_ids: HashSet<_> = assets
        .iter()
        .map(|a| a["asset_id"].as_str().unwrap())
        .collect();
    let track_ids: HashSet<_> = tracks
        .iter()
        .map(|t| t["track_id"].as_str().unwrap())
        .collect();
    for cue in projected["cues"].as_array().unwrap() {
        if cue.get("asset_ids").is_some_and(|v| {
            v.as_array()
                .unwrap()
                .iter()
                .any(|id| !asset_ids.contains(id.as_str().unwrap()))
        }) || cue
            .get("track_id")
            .is_some_and(|id| !track_ids.contains(id.as_str().unwrap()))
        {
            return Err(invalid("editable cue references an unknown asset/track"));
        }
    }
    Ok(
        json!({"schema_version":2,"kind":"capcut_import_pack","pack_id":format!("review-{}",text(&bundle["generation"],64)?),
        "assets":assets,"tracks":tracks,"timeline":projected}),
    )
}

/// The guidance is a copy of the existing v2 guide1 recipe's B1/B2 projection.
/// It is checked independently so changed prose cannot redirect import to a
/// different resource while a correct manifest happens to remain present.
fn guide(pack: &Value) -> SupervisorResult<Vec<u8>> {
    let assets = pack["assets"].as_array().unwrap();
    let path = |id: &str| -> SupervisorResult<&str> {
        assets
            .iter()
            .find(|a| a["asset_id"] == id)
            .and_then(|a| a["relative_path"].as_str())
            .ok_or_else(|| invalid("editing guide resource missing"))
    };
    let b2 = assets.iter().any(|a| a["asset_id"] == "final-mix");
    let mut lines = vec![
        "DubFlow — Hướng dẫn chỉnh sửa".to_owned(),
        "".into(),
        "1. Tạo một dự án trong CapCut Desktop.".into(),
        "2. Dùng Import > From device để thêm video và âm thanh bên dưới.".into(),
        "   Đặt video và bản mix ở đầu timeline.".into(),
        "".into(),
        "Video:".into(),
        format!("- {}", path("source-video")?),
    ];
    lines.push(
        if b2 {
            "- Đặt âm lượng của video về 0; dùng bản âm thanh đã chọn bên dưới."
        } else {
            "- Giữ âm thanh của video nếu bạn chưa chọn một bản mix thay thế."
        }
        .into(),
    );
    if b2 {
        lines.extend([
            "".into(),
            "Âm thanh đã chọn:".into(),
            format!("- {}", path("final-mix")?),
            "- Dùng bản này cho âm thanh đầu ra. Các stem còn lại dành cho việc phối lại.".into(),
        ]);
    }
    lines.extend([
        "".into(),
        "3. Nhập phụ đề qua Captions > Add captions / Import file.".into(),
        "   Chọn tệp phụ đề phù hợp với định dạng mà phiên bản CapCut của bạn hỗ trợ:".into(),
        format!("- {}", path("vietsub-srt")?),
        "   Sau khi nhập, từng câu xuất hiện thành một lớp văn bản có thể chỉnh sửa.".into(),
        "".into(),
        "Lưu dự án trong CapCut sau khi sắp xếp các lớp.".into(),
        "Có thể chuyển toàn bộ gói trước khi nhập. Sau khi nhập, giữ các tệp media".into(),
        "ở nơi đã chọn; nếu chuyển chúng, hãy liên kết lại media trong trình biên tập.".into(),
        "Các video và stem bổ sung trong gói là lựa chọn để dựng hoặc phối lại.".into(),
        "".into(),
        "Tài liệu CapCut:".into(),
        "https://www.capcut.com/help/how-to-import-subtitles".into(),
        "".into(),
    ]);
    Ok(lines.join("\n").into_bytes())
}

fn validate_pack(
    root: &Path,
    bundle_root: &Path,
    bundle: &Value,
    bundle_hash: &str,
    expected_manifest_hash: Option<&str>,
    seal: bool,
    deadline: Instant,
) -> SupervisorResult<(Value, String, Vec<EditableFileSpec>)> {
    let bundle_bytes = metadata(
        &bundle_root.join(BUNDLE_MANIFEST),
        64 * 1024 * 1024,
        deadline,
    )?;
    if hash(&bundle_bytes) != bundle_hash || json_bytes(&bundle_bytes)? != *bundle {
        return Err(invalid("editable source bundle manifest changed"));
    }
    let timeline_pin = bundle["files"]
        .as_array()
        .unwrap()
        .iter()
        .find(|p| p["role"] == "corrected_timeline")
        .unwrap();
    let timeline_bytes = metadata(
        &bundle_root.join(text(&timeline_pin["relative_path"], 128)?),
        64 * 1024 * 1024,
        deadline,
    )?;
    if hash(&timeline_bytes) != timeline_pin["sha256"]
        || timeline_bytes.len() as u64 != tick(&timeline_pin["size_bytes"], true)?
    {
        return Err(invalid("editable canonical timeline changed"));
    }
    let expected = expected_pack(
        bundle,
        bundle_hash,
        bundle_bytes.len() as u64,
        &json_bytes(&timeline_bytes)?,
    )?;
    let raw = metadata(&root.join("manifest.json"), LIMIT, deadline)?;
    let manifest_hash = hash(&raw);
    if expected_manifest_hash.is_some_and(|h| h != manifest_hash) || json_bytes(&raw)? != expected {
        return Err(invalid("editable pack manifest semantics/hash changed"));
    }
    let guide = guide(&expected)?;
    let marker = exact(
        &json!({"recipe":"portable-import-pack-copy-v2-guide1","manifest":expected,"editing_guide_sha256":hash(&guide)}),
    )?;
    if metadata(&root.join(".dubflow-input-pins.json"), LIMIT, deadline)? != marker.as_bytes()
        || metadata(&root.join("HUONG_DAN.txt"), 128 * 1024, deadline)? != guide
    {
        return Err(invalid(
            "editable marker or guidance differs from the admitted recipe",
        ));
    }
    let mut specs: Vec<_> = expected["assets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|asset| -> SupervisorResult<EditableFileSpec> {
            Ok(EditableFileSpec {
                relative_path: text(&asset["relative_path"], 256)?.into(),
                sha256: text(&asset["content_hash"], 71)?.into(),
                size_bytes: tick(&asset["size_bytes"], true)?,
            })
        })
        .collect::<SupervisorResult<_>>()?;
    for (name, bytes) in [
        ("manifest.json", raw.as_slice()),
        (".dubflow-input-pins.json", marker.as_bytes()),
        ("HUONG_DAN.txt", guide.as_slice()),
    ] {
        specs.push(EditableFileSpec {
            relative_path: name.into(),
            sha256: hash(bytes),
            size_bytes: bytes.len() as u64,
        });
    }
    verify_editable_files(root, &specs, seal, deadline)?;
    Ok((expected, manifest_hash, specs))
}

fn worker_packet(
    raw: &str,
    context: &Value,
    context_hash: &str,
    candidate: &Path,
    resource_count: usize,
) -> SupervisorResult<Value> {
    let value = decode(raw, PACKET_LIMIT)?;
    closed(
        &value,
        &[
            "schema_version",
            "kind",
            "context_sha256",
            "current_token",
            "bundle_manifest_sha256",
            "generation",
            "processing_recipe_identity",
            "candidate_path",
            "candidate_manifest_sha256",
            "pack_format",
            "resource_count",
            "current_generation_checked",
            "active_graph_published",
            "correction_ready",
        ],
    )?;
    if value["schema_version"].as_u64() != Some(1)
        || value["kind"] != "review_private_editable_pack_candidate"
        || value["context_sha256"] != context_hash
        || value["current_token"] != context["current_token"]
        || value["bundle_manifest_sha256"] != context["bundle_manifest_sha256"]
        || value["generation"] != context["generation"]
        || value["processing_recipe_identity"] != context["processing_recipe_identity"]
        || PathBuf::from(text(&value["candidate_path"], 32767)?) != candidate
        || value["pack_format"].as_u64() != Some(2)
        || value["resource_count"].as_u64() != Some(resource_count as u64)
        || value["current_generation_checked"] != false
        || value["active_graph_published"] != false
        || value["correction_ready"] != false
    {
        return Err(invalid(
            "editable worker packet substitutes native context, path, resource set or authority",
        ));
    }
    let h = text(&value["candidate_manifest_sha256"], 71)?;
    if h.len() != 71
        || !h.starts_with("sha256:")
        || !h[7..]
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    {
        return Err(invalid("editable candidate hash is invalid"));
    }
    Ok(value)
}

fn context(
    inputs: &AdmittedExportInputs,
    bundle: &Value,
    bundle_root: &Path,
    bundle_hash: &str,
    recipe: &Value,
) -> SupervisorResult<Value> {
    let limits = budget();
    let value = json!({"schema_version":1,"kind":"review_native_editable_pack_context","current_token":bundle["current_token"],
        "bundle_root":bundle_root,"bundle_manifest_sha256":bundle_hash,"generation":bundle["generation"],
        "storage_root":inputs.output_pin().root(),"output_volume_id":inputs.output_pin().volume_id(),"processing_recipe_identity":recipe["recipe_identity"],
        "maximum_bytes":limits.maximum_bytes.to_string(),"minimum_free_bytes":limits.minimum_free_bytes.to_string()});
    if exact(&value)?.len() > PACKET_LIMIT {
        return Err(invalid("editable context exceeds its byte bound"));
    }
    Ok(value)
}

pub(super) fn publish(
    store: &DurableStore,
    data: &Path,
    state: &ReviewGraphState,
    processing: &AdmittedProcessingRuntime,
) -> SupervisorResult<Value> {
    super::review_pin::plain(data)?;
    let db = data.join("control/jobs.sqlite3");
    super::review_pin::plain(&db)?;
    let _job = super::JobExecutionGuard::acquire(&db, &state.token.job_id)?;
    fence(store, state)?;
    let mut walk = RecoveryWalk::new(Instant::now() + Duration::from_secs(720));
    let inputs = prepare_with_walk(store, data, state, &mut walk)?;
    let (bundle, raw_bundle) = bundle_manifest(&inputs)?;
    let generation = text(&bundle["generation"], 64)?.to_owned();
    let bundle_hash = hash(raw_bundle.as_bytes());
    let recipe = processing.editable_pack_receipt(None)?;
    let assets = AcceptedAudioStore::open_existing(data, &state.token.job_id)?;
    let media = NativeMixStore::open_existing(inputs.output_pin(), &state.token.job_id, budget())?;
    media.editable_capacity(walk.deadline())?;
    let bundle_root = media
        .export_published_directory(&generation)?
        .ok_or_else(|| {
            invalid("publish the admitted current standard bundle before editable export")
        })?;
    if validate_bundle(&bundle_root, &bundle_hash, walk.deadline())? != bundle {
        return Err(invalid(
            "editable source bundle is not the exact native current generation",
        ));
    }
    let context_value = context(&inputs, &bundle, &bundle_root, &bundle_hash, &recipe)?;
    let context = assets.accept_json(&exact(&context_value)?)?;
    let execution = processing.editable_pack_receipt(Some(&context.sha256))?;
    if execution["recipe_identity"] != recipe["recipe_identity"] {
        return Err(invalid("editable recipe changed before dispatch"));
    }
    let deadline = Instant::now() + Duration::from_secs(720);
    let existing = media.editable_published_directory(&generation)?;
    let (candidate, worker, expected_hash) = if let Some(root) = existing {
        (root.join("import-pack"), Value::Null, None)
    } else {
        let root = media.editable_pending_directory(&generation)?;
        let candidate = root.join("import-pack");
        fence(store, state)?;
        assets.read_json(&context)?;
        let mut command = Command::new(&processing.runtime().python);
        command.args([
            OsString::from("-I"),
            OsString::from("-S"),
            OsString::from("-B"),
            OsString::from(text(&execution["entry"]["path"], 32767)?),
            OsString::from("--context"),
            context.path.as_os_str().to_owned(),
            OsString::from("--context-sha256"),
            OsString::from(&context.sha256),
        ]);
        command.current_dir(processing.runtime().root.join("app"));
        let result = super::review_seed::collect(command, Duration::from_secs(720), PACKET_LIMIT)?;
        let worker = worker_packet(
            &result,
            &context_value,
            &context.sha256,
            &candidate,
            if bundle["mode"] == "b2" { 10 } else { 7 },
        )?;
        let expected = text(&worker["candidate_manifest_sha256"], 71)?.to_owned();
        (candidate, worker, Some(expected))
    };
    if exact(&processing.editable_pack_receipt(Some(&context.sha256))?)? != exact(&execution)? {
        return Err(invalid("editable processing release changed after copy"));
    }
    assets.read_json(&context)?;
    if validate_bundle(&bundle_root, &bundle_hash, deadline)? != bundle {
        return Err(invalid("editable source bundle changed after copy"));
    }
    let (_, pack_hash, specs) = validate_pack(
        &candidate,
        &bundle_root,
        &bundle,
        &bundle_hash,
        expected_hash.as_deref(),
        true,
        deadline,
    )?;
    media.editable_capacity(deadline)?;
    fence(store, state)?;
    drop(media);
    // The physical current reader opens its own output writer lease. Release
    // the copy lease first, then reproduce every current/bundle dependency.
    let mut final_walk = RecoveryWalk::new(Instant::now() + Duration::from_secs(720));
    let fresh = prepare_with_walk(store, data, state, &mut final_walk)?;
    let (fresh_bundle, fresh_raw) = bundle_manifest(&fresh)?;
    if fresh_bundle != bundle
        || fresh_raw != raw_bundle
        || exact(&processing.editable_pack_receipt(Some(&context.sha256))?)? != exact(&execution)?
    {
        return Err(invalid("editable dependencies changed before publication"));
    }
    let media = NativeMixStore::open_existing(fresh.output_pin(), &state.token.job_id, budget())?;
    if validate_bundle(&bundle_root, &bundle_hash, final_walk.deadline())? != bundle {
        return Err(invalid("editable bundle changed before publication"));
    }
    validate_pack(
        &candidate,
        &bundle_root,
        &bundle,
        &bundle_hash,
        Some(&pack_hash),
        false,
        final_walk.deadline(),
    )?;
    let path = media.publish_editable_directory(
        &generation,
        &specs,
        || fence(store, state),
        final_walk.deadline(),
    )?;
    validate_pack(
        &path,
        &bundle_root,
        &bundle,
        &bundle_hash,
        Some(&pack_hash),
        false,
        final_walk.deadline(),
    )?;
    fence(store, state)?;
    final_walk.check()?;
    Ok(
        json!({"schema_version":1,"kind":"review_native_current_editable_result","current_token":token(state),
        "generation":generation,"path":path,"manifest_sha256":pack_hash,"bundle_manifest_sha256":bundle_hash,
        "processing_receipt":execution,"context_sha256":context.sha256,"worker":worker,"files_verified":specs.len(),
        "standard_bundle_published":true,"production_editable_pack_published":true,"current_generation_checked":true,
        "vendor_execution":false,"active_graph_changed":false,"correction_ready":false}),
    )
}

pub(super) fn run(mut args: impl Iterator<Item = OsString>) -> SupervisorResult<()> {
    const KEYS: [&str; 10] = [
        "--data-root",
        "--job-id",
        "--revision",
        "--snapshot-sha256",
        "--graph-sha256",
        "--original-manifest-sha256",
        "--processing-root",
        "--processing-manifest-sha256",
        "--processing-manifest-size-bytes",
        "--processing-source-sha",
    ];
    let mut values = HashMap::new();
    while let Some(flag) = args.next() {
        let flag = flag
            .into_string()
            .map_err(|_| invalid("editable argument is not UTF-8"))?;
        if !KEYS.contains(&flag.as_str()) || values.contains_key(&flag) {
            return Err(invalid("unknown or duplicate editable argument"));
        }
        let value = args
            .next()
            .ok_or_else(|| invalid("editable argument missing"))?
            .into_string()
            .map_err(|_| invalid("editable argument is not UTF-8"))?;
        values.insert(flag, value);
    }
    let required = |key: &str| -> SupervisorResult<&str> {
        values.get(key).map(String::as_str).ok_or_else(||invalid("editable publication requires complete native token and explicit processing selection"))
    };
    for key in KEYS {
        required(key)?;
    }
    let data = PathBuf::from(required("--data-root")?);
    if !data.is_absolute() || data.components().any(|p| matches!(p, Component::ParentDir)) {
        return Err(invalid("editable control root must be absolute"));
    }
    super::review_pin::plain(&data)?;
    let db = data.join("control/jobs.sqlite3");
    super::review_pin::plain(&db)?;
    let job = required("--job-id")?;
    super::validate_job_id(job)?;
    let store = DurableStore::open_read_only(&db)?;
    let state = store
        .review_graph_state(job)?
        .ok_or_else(|| invalid("Review graph is not initialized"))?;
    if state.token.revision != tick(&json!(required("--revision")?), false)?
        || state.token.snapshot_sha256 != required("--snapshot-sha256")?
        || state.token.graph_sha256 != required("--graph-sha256")?
        || state.token.original_manifest_sha256 != required("--original-manifest-sha256")?
    {
        return Err(invalid("editable current token changed; reload"));
    }
    let processing = AdmittedProcessingRuntime::read(
        &store,
        job,
        &PathBuf::from(required("--processing-root")?),
        &json!({"sha256":required("--processing-manifest-sha256")?,"size_bytes":tick(&json!(required("--processing-manifest-size-bytes")?),true)?}),
        required("--processing-source-sha")?,
    )?;
    println!("{}", publish(&store, &data, &state, &processing)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::super::review_seed::tests::with_admitted_original;
    use super::*;
    use std::fs;

    fn fixtures() -> Value {
        decode(
            include_str!("../../../../tests/review/editable-pack-python-v1.json"),
            1024 * 1024,
        )
        .unwrap()
    }
    fn writable(path: &Path) {
        let mut permissions = fs::metadata(path).unwrap().permissions();
        #[cfg(windows)]
        permissions.set_readonly(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            permissions.set_mode(0o600);
        }
        fs::set_permissions(path, permissions).unwrap();
    }
    fn release(root: &Path) {
        for entry in fs::read_dir(root).unwrap() {
            let entry = entry.unwrap();
            if entry.file_type().unwrap().is_dir() {
                release(&entry.path());
            } else {
                writable(&entry.path());
            }
        }
    }
    fn materialize(root: &Path, case: &Value) -> (PathBuf, PathBuf, Value, String) {
        let bundle = root.join("native bundle with spaces");
        let pack = root.join("editable pack with spaces");
        fs::create_dir_all(&bundle).unwrap();
        fs::create_dir_all(&pack).unwrap();
        for (key, directory) in [("bundle_files", &bundle), ("pack_files", &pack)] {
            for file in case[key].as_array().unwrap() {
                let path = directory.join(file["relative_path"].as_str().unwrap());
                fs::create_dir_all(path.parent().unwrap()).unwrap();
                fs::write(&path, file["utf8"].as_str().unwrap().as_bytes()).unwrap();
            }
        }
        let bytes = fs::read(bundle.join(BUNDLE_MANIFEST)).unwrap();
        let value = json_bytes(&bytes).unwrap();
        (bundle, pack, value, hash(&bytes))
    }

    #[test]
    fn independent_native_semantics_match_python_b1_b2_bytes_and_preserve_wide_token_and_source_confidence(
    ) {
        with_admitted_original(|_, data, _| {
            for case in fixtures()["modes"].as_array().unwrap() {
                let root = data.join(format!("python-{}", case["mode"].as_str().unwrap()));
                let (bundle, pack, value, pin) = materialize(&root, case);
                let before = fs::read(bundle.join("timeline.json")).unwrap();
                let (_, manifest_hash, specs) = validate_pack(
                    &pack,
                    &bundle,
                    &value,
                    &pin,
                    None,
                    true,
                    Instant::now() + Duration::from_secs(10),
                )
                .unwrap();
                assert_eq!(value["current_token"]["revision"], "9007199254740993");
                assert_eq!(specs.len(), if case["mode"] == "b2" { 13 } else { 10 });
                let parsed = json_bytes(&before).unwrap();
                assert_eq!(
                    parsed["cues"][0]["confidence"].as_f64().unwrap().to_bits(),
                    0.9838564395904541f64.to_bits()
                );
                let files: Vec<_> = specs
                    .iter()
                    .map(|s| {
                        (
                            &s.relative_path,
                            fs::metadata(pack.join(&s.relative_path))
                                .unwrap()
                                .modified()
                                .unwrap(),
                        )
                    })
                    .collect();
                validate_pack(
                    &pack,
                    &bundle,
                    &value,
                    &pin,
                    Some(&manifest_hash),
                    false,
                    Instant::now() + Duration::from_secs(10),
                )
                .unwrap();
                for (name, mtime) in files {
                    assert_eq!(
                        fs::metadata(pack.join(name)).unwrap().modified().unwrap(),
                        mtime
                    );
                }
                assert_eq!(fs::read(bundle.join("timeline.json")).unwrap(), before);
                release(&root);
            }
        });
    }

    #[test]
    fn native_pack_refuses_forged_tracks_timeline_roles_guide_extra_files_and_equal_size_corruption(
    ) {
        with_admitted_original(|_, data, _| {
            let all = fixtures();
            let case = &all["modes"][1];
            let root = data.join("native semantic refusals");
            let (bundle, pack, value, pin) = materialize(&root, case);
            let path = pack.join("manifest.json");
            let original = fs::read(&path).unwrap();
            let parsed = json_bytes(&original).unwrap();
            for change in 0..4 {
                let mut changed = parsed.clone();
                match change {
                    0 => changed["tracks"][0]["mute_embedded_audio"] = false.into(),
                    1 => changed["assets"][0]["relative_path"] = "../source.mp4".into(),
                    2 => changed["timeline"]["cues"][0]["start_ticks"] = "90091".into(),
                    _ => changed["assets"][1]["kind"] = "metadata".into(),
                }
                fs::write(&path, changed.to_string()).unwrap();
                assert!(validate_pack(
                    &pack,
                    &bundle,
                    &value,
                    &pin,
                    None,
                    true,
                    Instant::now() + Duration::from_secs(5)
                )
                .is_err());
            }
            fs::write(&path, &original).unwrap();
            let guide = pack.join("HUONG_DAN.txt");
            let guide_raw = fs::read(&guide).unwrap();
            fs::write(&guide, b"use another source").unwrap();
            assert!(validate_pack(
                &pack,
                &bundle,
                &value,
                &pin,
                None,
                true,
                Instant::now() + Duration::from_secs(5)
            )
            .is_err());
            fs::write(&guide, &guide_raw).unwrap();
            fs::write(pack.join("unregistered.partial"), b"orphan").unwrap();
            assert!(validate_pack(
                &pack,
                &bundle,
                &value,
                &pin,
                None,
                true,
                Instant::now() + Duration::from_secs(5)
            )
            .is_err());
            fs::remove_file(pack.join("unregistered.partial")).unwrap();
            let source = pack.join("media/source.mp4");
            let bytes = fs::read(&source).unwrap();
            let mut bad = bytes.clone();
            bad[0] ^= 1;
            writable(&source);
            fs::write(&source, bad).unwrap();
            assert!(validate_pack(
                &pack,
                &bundle,
                &value,
                &pin,
                None,
                true,
                Instant::now() + Duration::from_secs(5)
            )
            .is_err());
            assert_eq!(fs::read(bundle.join("source.mp4")).unwrap(), bytes);
            release(&root);
        });
    }

    #[test]
    fn editable_worker_packet_cannot_choose_another_path_token_resource_set_or_publication_authority(
    ) {
        let context = json!({"current_token":{"revision":"9007199254740993","original_manifest_sha256":hash(b"origin")},
            "bundle_manifest_sha256":hash(b"bundle"),"generation":"a".repeat(64),"processing_recipe_identity":hash(b"recipe")});
        let path = std::env::temp_dir().join("native-derived-private-pack");
        let sha = hash(b"context");
        let good = json!({"schema_version":1,"kind":"review_private_editable_pack_candidate","context_sha256":sha,
            "current_token":context["current_token"],"bundle_manifest_sha256":context["bundle_manifest_sha256"],
            "generation":context["generation"],"processing_recipe_identity":context["processing_recipe_identity"],
            "candidate_path":path,"candidate_manifest_sha256":hash(b"manifest"),"pack_format":2,"resource_count":10,
            "current_generation_checked":false,"active_graph_published":false,"correction_ready":false});
        worker_packet(&good.to_string(), &context, &sha, &path, 10).unwrap();
        for change in 0..7 {
            let mut forged = good.clone();
            match change {
                0 => forged["candidate_path"] = json!(path.join("outside")),
                1 => forged["current_token"]["revision"] = "9007199254740992".into(),
                2 => {
                    forged["current_token"]["original_manifest_sha256"] =
                        hash(b"other origin").into()
                }
                3 => forged["resource_count"] = 7.into(),
                4 => forged["current_generation_checked"] = true.into(),
                5 => forged["active_graph_published"] = true.into(),
                _ => forged["processing_recipe_identity"] = hash(b"worker-chosen-recipe").into(),
            }
            assert!(worker_packet(&forged.to_string(), &context, &sha, &path, 10).is_err());
        }
    }

    #[test]
    fn editable_projection_preserves_large_integer_ticks_reduces_rational_and_refuses_conflicting_aliases(
    ) {
        let value = json!({"time_base":{"numerator":"2","denominator":"180000"},"duration_ticks":"9007199254740995",
            "cues":[{"cue_id":"a","start_ticks":"9007199254740993","end_ticks":"9007199254740994","confidence":0.9838564395904541}]});
        let result = projection(&value).unwrap();
        assert_eq!(
            result["time_base"],
            json!({"numerator":"1","denominator":"90000"})
        );
        assert_eq!(result["cues"][0]["start_ticks"], "9007199254740993");
        let mut alias = value.clone();
        alias["cues"][0]["start_ms"] = 1001.into();
        assert!(projection(&alias).is_err());
        let mut fractional = value.clone();
        fractional["cues"][0]["start_ticks"] = json!(1.25);
        assert!(projection(&fractional).is_err());
    }
}
