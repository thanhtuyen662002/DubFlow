//! Bounded restartable copies inside the existing output-wide writer lease.
//! This primitive admits no generation, manifest, media semantics or publication.
use super::*;

pub(crate) struct ExportCopyInput<'a> {
    pub(crate) generation: &'a str,
    pub(crate) name: &'a str,
    pub(crate) source: &'a Path,
    pub(crate) sha256: &'a str,
    pub(crate) size_bytes: u64,
    pub(crate) remaining_after: u64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ExportFileSpec {
    pub(crate) name: String,
    pub(crate) sha256: String,
    pub(crate) size_bytes: u64,
}

/// Moved-bundle verification requires a trusted complete file table. This
/// grants byte integrity only, never current-generation or media admission.
pub(crate) fn verify_export_files(
    root: &Path,
    files: &[ExportFileSpec],
    deadline: Instant,
) -> SupervisorResult<()> {
    plain(root)?;
    if !root.is_absolute()
        || !fs::symlink_metadata(root)?.file_type().is_dir()
        || !(7..=10).contains(&files.len())
    {
        return Err(invalid("export bundle root or file count invalid"));
    }
    let mut expected = std::collections::HashMap::new();
    for file in files {
        if !valid_name(&file.name)
            || !valid_hash(&file.sha256)
            || file.size_bytes == 0
            || expected.insert(file.name.as_str(), file).is_some()
        {
            return Err(invalid("export bundle has invalid or duplicate file pins"));
        }
    }
    let mut count = 0;
    for entry in fs::read_dir(root)? {
        time_left(Some(deadline))?;
        let entry = entry?;
        count += 1;
        let name = entry.file_name();
        let name = name
            .to_str()
            .ok_or_else(|| invalid("export filename is not UTF-8"))?;
        let file = expected
            .get(name)
            .ok_or_else(|| invalid("export bundle contains an unregistered file"))?;
        verify_sealed(
            &entry.path(),
            &ExportCopyInput {
                generation: "",
                name,
                source: &entry.path(),
                sha256: &file.sha256,
                size_bytes: file.size_bytes,
                remaining_after: 0,
            },
            deadline,
        )?;
    }
    if count != files.len() {
        return Err(invalid("export bundle is missing a registered file"));
    }
    time_left(Some(deadline))
}

fn valid_name(name: &str) -> bool {
    matches!(
        name,
        "final_vi.mp4"
            | "captions_vi.srt"
            | "captions_vi.ass"
            | "timeline.json"
            | "qc_report.json"
            | "original_audio.wav"
            | "dialogue_stem.wav"
            | "final_mix.wav"
            | "review_export_manifest.json"
    ) || name.strip_prefix("source.").is_some_and(|extension| {
        (1..=8).contains(&extension.len())
            && extension
                .bytes()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
    })
}

/// Atomic same-volume rename, explicitly refusing replacement on both supported
/// platforms. A preflight existence check alone cannot provide this guarantee.
/// API references: https://man7.org/linux/man-pages/man2/rename.2.html and
/// https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw
pub(super) fn rename_new(source: &Path, target: &Path) -> SupervisorResult<()> {
    #[cfg(target_os = "linux")]
    {
        use std::ffi::CString;
        use std::os::unix::ffi::OsStrExt;
        unsafe extern "C" {
            fn renameat2(
                old_dir: i32,
                old: *const std::ffi::c_char,
                new_dir: i32,
                new: *const std::ffi::c_char,
                flags: u32,
            ) -> i32;
        }
        let old = CString::new(source.as_os_str().as_bytes())
            .map_err(|_| invalid("export path contains NUL"))?;
        let new = CString::new(target.as_os_str().as_bytes())
            .map_err(|_| invalid("export path contains NUL"))?;
        if unsafe { renameat2(-100, old.as_ptr(), -100, new.as_ptr(), 1) } != 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        // Persist the directory entry as well as the previously synced bytes.
        let parent = source
            .parent()
            .ok_or_else(|| invalid("export source parent missing"))?;
        plain(parent)?;
        File::open(parent)?.sync_all()?;
        let parent = target
            .parent()
            .ok_or_else(|| invalid("export target parent missing"))?;
        plain(parent)?;
        File::open(parent)?.sync_all()?;
        Ok(())
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        #[link(name = "kernel32")]
        unsafe extern "system" {
            fn MoveFileExW(old: *const u16, new: *const u16, flags: u32) -> i32;
        }
        let old: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
        let new: Vec<u16> = target.as_os_str().encode_wide().chain(Some(0)).collect();
        if old[..old.len() - 1].contains(&0) || new[..new.len() - 1].contains(&0) {
            return Err(invalid("export path contains NUL"));
        }
        // WRITE_THROUGH, without REPLACE_EXISTING or cross-volume COPY_ALLOWED.
        if unsafe { MoveFileExW(old.as_ptr(), new.as_ptr(), 8) } == 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        Ok(())
    }
    #[cfg(not(any(target_os = "linux", windows)))]
    {
        let _ = (source, target);
        Err(invalid(
            "atomic corrected export rename unsupported on this platform",
        ))
    }
}

fn stream_hash(file: &mut File, hash: &str, size: u64, deadline: Instant) -> SupervisorResult<()> {
    if file.metadata()?.len() != size {
        return Err(invalid("export copy file size changed"));
    }
    file.seek(SeekFrom::Start(0))?;
    let mut sha = Sha256::new();
    let mut count = 0u64;
    let mut buffer = [0u8; BUFFER_BYTES];
    loop {
        time_left(Some(deadline))?;
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        count = count
            .checked_add(n as u64)
            .ok_or_else(|| invalid("export hash byte count overflow"))?;
        if count > size {
            return Err(invalid("export copy file grew"));
        }
        sha.update(&buffer[..n]);
    }
    if count != size
        || format!("sha256:{:x}", sha.finalize()) != hash
        || file.metadata()?.len() != size
    {
        return Err(invalid("export copy full hash changed"));
    }
    time_left(Some(deadline))
}

fn verify_sealed(
    path: &Path,
    input: &ExportCopyInput<'_>,
    deadline: Instant,
) -> SupervisorResult<()> {
    let mut file = regular(path)?;
    let identity = single_cue_file(&file)?;
    let before = file.metadata()?;
    if !before.permissions().readonly() {
        return Err(invalid("export copy is not sealed"));
    }
    stream_hash(&mut file, input.sha256, input.size_bytes, deadline)?;
    let after = regular(path)?;
    if single_cue_file(&after)? != identity
        || single_cue_file(&file)? != identity
        || after.metadata()?.modified() != before.modified()?
        || after.metadata()?.len() != input.size_bytes
        || !after.metadata()?.permissions().readonly()
    {
        return Err(invalid("export copy identity changed"));
    }
    time_left(Some(deadline))
}

fn open_partial(path: &Path) -> SupervisorResult<File> {
    plain(
        path.parent()
            .ok_or_else(|| invalid("export partial parent missing"))?,
    )?;
    let mut options = OpenOptions::new();
    options.read(true).write(true);
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(0x20000 | 0x800);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x00200000);
    }
    let file = match options.create_new(true).open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            let prior = regular(path)?;
            single_cue_file(&prior)?;
            options.create_new(false).create(false).open(path)?
        }
        Err(error) => return Err(error.into()),
    };
    let fresh = regular(path)?;
    if !file.metadata()?.is_file() || single_cue_file(&file)? != single_cue_file(&fresh)? {
        return Err(invalid("export partial was substituted"));
    }
    Ok(file)
}

impl RetainedMixStore {
    /// Allocation forecast only. Existing prefix bytes still require comparison
    /// to the source in copy_export_file before any append/reuse authority.
    pub(crate) fn export_remaining_bytes(
        &self,
        generation: &str,
        files: &[ExportFileSpec],
        deadline: Instant,
    ) -> SupervisorResult<Vec<u64>> {
        let root = self.export_pending_directory(generation)?;
        let mut result = Vec::new();
        for file in files {
            time_left(Some(deadline))?;
            if !valid_name(&file.name) || !valid_hash(&file.sha256) || file.size_bytes == 0 {
                return Err(invalid("invalid export allocation forecast pin"));
            }
            let target = root.join(&file.name);
            let partial = root.join(format!("{}.partial", file.name));
            let input = ExportCopyInput {
                generation,
                name: &file.name,
                source: &target,
                sha256: &file.sha256,
                size_bytes: file.size_bytes,
                remaining_after: 0,
            };
            match fs::symlink_metadata(&target) {
                Ok(_) => {
                    verify_sealed(&target, &input, deadline)?;
                    result.push(0);
                    continue;
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
                Err(error) => return Err(error.into()),
            }
            let used = match fs::symlink_metadata(&partial) {
                Ok(_) => {
                    let open = regular(&partial)?;
                    single_cue_file(&open)?;
                    if open.metadata()?.permissions().readonly() {
                        verify_sealed(&partial, &input, deadline)?;
                    }
                    open.metadata()?.len()
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => 0,
                Err(error) => return Err(error.into()),
            };
            result.push(
                file.size_bytes
                    .checked_sub(used)
                    .ok_or_else(|| invalid("export partial exceeds forecast pin"))?,
            );
        }
        time_left(Some(deadline))?;
        Ok(result)
    }

    pub(crate) fn export_published_directory(
        &self,
        generation: &str,
    ) -> SupervisorResult<Option<PathBuf>> {
        if !valid_hash(&format!("sha256:{generation}")) {
            return Err(invalid("invalid export generation"));
        }
        let path = self
            .objects
            .root
            .join(".exports-published-v1")
            .join(generation);
        match fs::symlink_metadata(&path) {
            Ok(_) => {
                plain(&path)?;
                if !path.is_dir() {
                    return Err(invalid("published export is not a directory"));
                }
                Ok(Some(path))
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(error.into()),
        }
    }

    pub(crate) fn publish_export_directory(
        &self,
        generation: &str,
        files: &[ExportFileSpec],
        mut available_bytes: impl FnMut(&Path) -> SupervisorResult<u64>,
        mut current_fence: impl FnMut() -> SupervisorResult<()>,
        deadline: Instant,
    ) -> SupervisorResult<PathBuf> {
        let capacity =
            |available: &mut dyn FnMut(&Path) -> SupervisorResult<u64>| -> SupervisorResult<()> {
                if self.used_bytes(deadline)? > self.budget.maximum_bytes
                    || available(&self.namespace)? < self.budget.minimum_free_bytes
                {
                    return Err(invalid(
                        "export aggregate quota or fresh reserve changed before publication",
                    ));
                }
                time_left(Some(deadline))
            };
        if let Some(path) = self.export_published_directory(generation)? {
            verify_export_files(&path, files, deadline)?;
            capacity(&mut available_bytes)?;
            current_fence()?;
            return Ok(path);
        }
        let pending = self.export_pending_directory(generation)?;
        verify_export_files(&pending, files, deadline)?;
        let published = self.objects.root.join(".exports-published-v1");
        directory(&published)?;
        let target = published.join(generation);
        capacity(&mut available_bytes)?;
        current_fence()?;
        time_left(Some(deadline))?;
        rename_new(&pending, &target)?;
        verify_export_files(&target, files, deadline)?;
        capacity(&mut available_bytes)?;
        // A late refusal leaves the complete immutable version for recovery.
        current_fence()?;
        time_left(Some(deadline))?;
        Ok(target)
    }

    pub(crate) fn export_pending_directory(&self, generation: &str) -> SupervisorResult<PathBuf> {
        if !valid_hash(&format!("sha256:{generation}")) || !self.objects.root.is_absolute() {
            return Err(invalid("export generation is outside its native namespace"));
        }
        let pending = self.objects.root.join(".exports-pending-v1");
        directory(&pending)?;
        let generation = pending.join(generation);
        directory(&generation)?;
        Ok(generation)
    }

    /// The caller derives these pins from typed current native admission. Resume
    /// verifies every existing prefix byte against a fresh source handle before
    /// appending; it never trusts a recorded offset or truncates another file.
    pub(crate) fn copy_export_file(
        &self,
        input: &ExportCopyInput<'_>,
        mut available_bytes: impl FnMut(&Path) -> SupervisorResult<u64>,
        deadline: Instant,
    ) -> SupervisorResult<PathBuf> {
        time_left(Some(deadline))?;
        if !valid_name(input.name)
            || !valid_hash(input.sha256)
            || input.size_bytes == 0
            || input.size_bytes > 128 * 1024 * 1024 * 1024
            || (input.name.ends_with(".json") && input.size_bytes > MAX_DOCUMENT_BYTES)
            || !input.source.is_absolute()
        {
            return Err(invalid("invalid native corrected export copy pin"));
        }
        let pending = self.export_pending_directory(input.generation)?;
        let target = pending.join(input.name);
        let partial = pending.join(format!("{}.partial", input.name));
        if input.source == target.as_path() || input.source == partial.as_path() {
            return Err(invalid("export copy aliases its input"));
        }
        let mut source = regular(input.source)?;
        let source_before = source.metadata()?;
        if source_before.len() != input.size_bytes {
            return Err(invalid("export source size changed"));
        }
        let source_fence = |source: &mut File| -> SupervisorResult<()> {
            let mut fresh = regular(input.source)?;
            stream_hash(&mut fresh, input.sha256, input.size_bytes, deadline)?;
            if source.metadata()?.modified() != source_before.modified()?
                || fresh.metadata()?.modified() != source_before.modified()?
                || source.metadata()?.len() != input.size_bytes
            {
                return Err(invalid("export source changed during copy"));
            }
            Ok(())
        };
        match fs::symlink_metadata(&target) {
            Ok(_) => {
                verify_sealed(&target, input, deadline)?;
                source_fence(&mut source)?;
                return Ok(target);
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
            Err(error) => return Err(error.into()),
        }
        let reserve = |remaining: u64,
                       available: &mut dyn FnMut(&Path) -> SupervisorResult<u64>|
         -> SupervisorResult<()> {
            time_left(Some(deadline))?;
            let additional = remaining
                .checked_add(input.remaining_after)
                .ok_or_else(|| invalid("export reservation overflow"))?;
            if self
                .used_bytes(deadline)?
                .checked_add(additional)
                .filter(|n| *n <= self.budget.maximum_bytes)
                .is_none()
            {
                return Err(invalid(
                    "export aggregate quota exhausted; preserve prior and partial outputs",
                ));
            }
            let needed = additional
                .checked_add(self.budget.minimum_free_bytes)
                .ok_or_else(|| invalid("export free reserve overflow"))?;
            if available(&self.namespace)? < needed {
                return Err(invalid("export fresh free reserve unavailable"));
            }
            Ok(())
        };
        if fs::symlink_metadata(&partial).is_ok_and(|m| m.permissions().readonly()) {
            verify_sealed(&partial, input, deadline)?;
            source_fence(&mut source)?;
            reserve(0, &mut available_bytes)?;
            rename_new(&partial, &target)?;
            verify_sealed(&target, input, deadline)?;
            return Ok(target);
        }
        let mut copy = open_partial(&partial)?;
        let identity = single_cue_file(&copy)?;
        let prefix = copy.metadata()?.len();
        if prefix > input.size_bytes {
            return Err(invalid("export partial exceeds its admitted size"));
        }
        reserve(input.size_bytes - prefix, &mut available_bytes)?;
        let mut sha = Sha256::new();
        let mut copied = 0u64;
        let mut buffer = [0u8; BUFFER_BYTES];
        let mut prior = [0u8; BUFFER_BYTES];
        while copied < prefix {
            time_left(Some(deadline))?;
            let n = (prefix - copied).min(BUFFER_BYTES as u64) as usize;
            source.read_exact(&mut buffer[..n])?;
            copy.read_exact(&mut prior[..n])?;
            if buffer[..n] != prior[..n] {
                return Err(invalid("export resume prefix differs from admitted source"));
            }
            sha.update(&buffer[..n]);
            copied += n as u64;
        }
        if copy.metadata()?.len() != prefix || single_cue_file(&regular(&partial)?)? != identity {
            return Err(invalid("export resume prefix was substituted"));
        }
        let mut next_check = copied;
        loop {
            time_left(Some(deadline))?;
            if copied >= next_check {
                if single_cue_file(&regular(&partial)?)? != identity
                    || copy.metadata()?.len() != copied
                {
                    return Err(invalid("export partial identity changed during copy"));
                }
                reserve(input.size_bytes - copied, &mut available_bytes)?;
                next_check = copied.saturating_add(4 * 1024 * 1024);
            }
            let n = source.read(&mut buffer)?;
            if n == 0 {
                break;
            }
            let next = copied
                .checked_add(n as u64)
                .filter(|n| *n <= input.size_bytes)
                .ok_or_else(|| invalid("export source grew beyond its admitted size"))?;
            copy.write_all(&buffer[..n])?;
            sha.update(&buffer[..n]);
            copied = next;
        }
        if copied != input.size_bytes || format!("sha256:{:x}", sha.finalize()) != input.sha256 {
            return Err(invalid("export source bytes differ from the native pin"));
        }
        copy.sync_all()?;
        stream_hash(&mut copy, input.sha256, input.size_bytes, deadline)?;
        source_fence(&mut source)?;
        reserve(0, &mut available_bytes)?;
        if single_cue_file(&regular(&partial)?)? != identity || single_cue_file(&copy)? != identity
        {
            return Err(invalid("export partial changed before sealing"));
        }
        let mut permissions = copy.metadata()?.permissions();
        permissions.set_readonly(true);
        copy.set_permissions(permissions)?;
        copy.sync_all()?;
        drop(copy);
        verify_sealed(&partial, input, deadline)?;
        rename_new(&partial, &target)?;
        verify_sealed(&target, input, deadline)?;
        time_left(Some(deadline))?;
        Ok(target)
    }
}
