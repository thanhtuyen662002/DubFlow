use std::fs;
use std::path::{Component, Path, PathBuf};

pub fn validate_job_id(value: &str) -> Result<(), String> {
    if value.is_empty() || value.len() > 128 || value == "." || value == ".."
        || value.chars().any(char::is_control)
        || value.bytes().any(|byte| byte == b'/' || byte == b'\\')
    {
        return Err("job_id is invalid".into());
    }
    Ok(())
}

fn under_root(root: &Path, source: &Path, job_id: &str) -> Result<PathBuf, String> {
    validate_job_id(job_id)?;
    let mut output = root.join(format!("{} - vi",
        source.file_stem().and_then(|name| name.to_str()).unwrap_or("video")));
    // Preserve the complete ID, including case, and the existing component layout.
    for (index, chunk) in job_id.as_bytes().chunks(64).enumerate() {
        let encoded: String = chunk.iter().map(|byte| format!("{byte:02x}")).collect();
        output = output.join(if index == 0 { format!("run-{encoded}") } else { encoded });
    }
    Ok(output)
}

pub fn default_job_output(source: &Path, job_id: &str) -> Result<PathBuf, String> {
    under_root(&source.parent().unwrap_or(Path::new(".")).join("DubFlow Output"), source, job_id)
}

// Resolve existing ancestors before counting: Windows canonical paths include
// the verbatim prefix also seen later by the native Review reader.
fn canonical_destination(path: &Path) -> Result<PathBuf, String> {
    if !path.is_absolute() || path.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err("Nơi lưu phải là đường dẫn tuyệt đối, không chứa '..'.".into());
    }
    let mut ancestor = path;
    let mut missing = Vec::new();
    loop {
        match fs::metadata(ancestor) {
            Ok(info) => {
                if !info.is_dir() { return Err("Nơi lưu có thành phần không phải thư mục.".into()); }
                let mut resolved = ancestor.canonicalize()
                    .map_err(|_| "Không xác định được thư mục lưu. Hãy chọn lại nơi lưu.".to_owned())?;
                for name in missing.iter().rev() { resolved.push(name); }
                return Ok(resolved);
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                missing.push(ancestor.file_name().ok_or("Không xác định được thư mục lưu.")?.to_os_string());
                ancestor = ancestor.parent().ok_or("Không xác định được thư mục lưu.")?;
            }
            Err(_) => return Err("Không đọc được thư mục lưu. Hãy chọn thư mục có quyền truy cập.".into()),
        }
    }
}

pub fn validate_review_path(output: &Path, windows: bool) -> Result<(), String> {
    // Same compact scratch and UTF-16 ceiling as supervisor select_scratch.
    // This is an early usability check; native ownership/capacity checks still run.
    if windows && output.join(".dfr").join("temp").as_os_str()
        .to_string_lossy().encode_utf16().count() > 200
    {
        return Err("Đường dẫn nơi lưu quá dài để chỉnh sửa lời thoại trên Windows. Chọn một thư mục ngắn hơn rồi bấm Bắt đầu xử lý; kết quả cũ vẫn được giữ.".into());
    }
    Ok(())
}

pub fn resolve_job_output(source: &Path, job_id: &str, output_dir: Option<&str>, output_root: Option<&str>) -> Result<PathBuf, String> {
    validate_job_id(job_id)?;
    if output_dir.is_some() && output_root.is_some() {
        return Err("Chỉ chọn một nơi lưu kết quả.".into());
    }
    let proposed = if let Some(value) = output_dir {
        // Existing API callers select an exact destination, unchanged.
        PathBuf::from(value)
    } else if let Some(value) = output_root {
        let root = Path::new(value);
        if !root.is_absolute() || !root.is_dir() {
            return Err("Thư mục đã chọn không còn truy cập được. Hãy chọn lại nơi lưu.".into());
        }
        under_root(root, source, job_id)?
    } else {
        default_job_output(source, job_id)?
    };
    let output = canonical_destination(&proposed)?;
    validate_review_path(&output, cfg!(windows))?;
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scratch_budget_counts_verbatim_prefix_and_utf16() {
        assert!(validate_review_path(Path::new(&"a".repeat(190)), true).is_ok());
        assert!(validate_review_path(Path::new(&"a".repeat(191)), true).is_err());
        assert!(validate_review_path(Path::new(&"🎬".repeat(95)), true).is_ok());
        assert!(validate_review_path(Path::new(&"🎬".repeat(96)), true).is_err());
        assert!(validate_review_path(Path::new(&"a".repeat(500)), false).is_ok());
    }

    #[test]
    fn output_root_keeps_complete_job_identity_and_legacy_default() {
        let root = Path::new("short");
        let source = Path::new("input/movie.mp4");
        let first = under_root(root, source, "job-original").unwrap();
        assert_ne!(first, under_root(root, source, "job-next").unwrap());
        assert_ne!(first.to_string_lossy().to_lowercase(),
            under_root(root, source, "JOB-original").unwrap().to_string_lossy().to_lowercase());
        assert_eq!(default_job_output(source, "job-original").unwrap(),
            under_root(Path::new("input/DubFlow Output"), source, "job-original").unwrap());
        for id in ["", "..", "../escape", "bad\\id"] {
            assert!(under_root(root, source, id).is_err());
        }
        assert!(under_root(root, source, &"x".repeat(128)).unwrap().components()
            .all(|part| part.as_os_str().to_string_lossy().len() <= 132));
    }

    #[test]
    fn resolution_is_read_only_and_preserves_exact_destination_api() {
        let root = std::env::temp_dir().join(format!("df-out-{}", std::process::id()));
        fs::create_dir(&root).unwrap();
        let source = root.join("v.mp4");
        let root_string = root.to_str().unwrap();
        let exact = root.join("original");
        let result = resolve_job_output(&source, "j", Some(exact.to_str().unwrap()), None).unwrap();
        assert_eq!(result, root.canonicalize().unwrap().join("original"));
        assert!(!exact.exists());
        let next = resolve_job_output(&source, "k", None, Some(root_string)).unwrap();
        assert!(!next.exists());
        if cfg!(windows) {
            let canonical_units = root.canonicalize().unwrap().as_os_str()
                .to_string_lossy().encode_utf16().count();
            // Include the real canonical verbatim prefix, not a DOS-only count.
            let at_limit = root.join("b".repeat(190 - canonical_units - 1));
            let beyond = root.join("b".repeat(191 - canonical_units - 1));
            assert!(resolve_job_output(&source, "j", Some(at_limit.to_str().unwrap()), None).is_ok());
            assert!(resolve_job_output(&source, "j", Some(beyond.to_str().unwrap()), None).is_err());
            assert!(!at_limit.exists() && !beyond.exists());
        }
        assert!(resolve_job_output(&source, "j", Some(root_string), Some(root_string)).is_err());
        assert!(resolve_job_output(&source, "j", None, Some(exact.to_str().unwrap())).is_err());
        assert!(resolve_job_output(&source, "j", Some("relative"), None).is_err());
        let too_deep = root.join("a".repeat(180));
        if cfg!(windows) {
            assert!(resolve_job_output(&source, "j", Some(too_deep.to_str().unwrap()), None).is_err());
            assert!(!too_deep.exists());
        }
        assert_eq!(fs::read_dir(&root).unwrap().count(), 0);
        fs::remove_dir(root).unwrap();
    }
}
