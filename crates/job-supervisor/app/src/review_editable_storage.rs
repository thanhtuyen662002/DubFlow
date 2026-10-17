//! Explicit editable layout inside the existing output-wide writer/quota lease.
//! This module accepts only native-derived paths and complete expected file pins.
use super::*;
use std::collections::{HashMap, HashSet};

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct EditableFileSpec {
    pub(crate) relative_path: String,
    pub(crate) sha256: String,
    pub(crate) size_bytes: u64,
}

fn pack_directory(name: &str) -> bool {
    name == "import-pack"
        || name
            .strip_prefix(".import-pack.")
            .and_then(|s| s.strip_suffix(".partial"))
            .is_some_and(|s| {
                s.len() == 32
                    && s.bytes()
                        .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
            })
}

/// Only tag/generation/pack/resource-directory nesting is permitted. Count all
/// regular orphan/partial/foreign bytes, including other jobs and old versions.
pub(super) fn inventory_bytes(
    tag: &Path,
    account: &mut impl FnMut(&Path) -> SupervisorResult<()>,
) -> SupervisorResult<u64> {
    let mut total = 0u64;
    for generation in fs::read_dir(tag)? {
        let generation = generation?;
        account(&generation.path())?;
        let name = generation.file_name();
        if !valid_hash(&format!("sha256:{}", name.to_str().unwrap_or("")))
            || !generation.file_type()?.is_dir()
        {
            return Err(invalid("unknown editable generation directory"));
        }
        for pack in fs::read_dir(generation.path())? {
            let pack = pack?;
            account(&pack.path())?;
            if pack.file_type()?.is_dir() {
                if !pack_directory(pack.file_name().to_str().unwrap_or("")) {
                    return Err(invalid("unknown editable pack checkpoint directory"));
                }
                for entry in fs::read_dir(pack.path())? {
                    let entry = entry?;
                    account(&entry.path())?;
                    if entry.file_type()?.is_dir() {
                        if !["media", "audio", "metadata", "subtitles"]
                            .contains(&entry.file_name().to_str().unwrap_or(""))
                        {
                            return Err(invalid("unknown editable resource directory"));
                        }
                        for resource in fs::read_dir(entry.path())? {
                            let resource = resource?;
                            account(&resource.path())?;
                            total = add_file_bytes(total, &resource.path())?;
                        }
                    } else {
                        total = add_file_bytes(total, &entry.path())?;
                    }
                }
            } else {
                total = add_file_bytes(total, &pack.path())?;
            }
        }
    }
    Ok(total)
}

fn add_file_bytes(total: u64, path: &Path) -> SupervisorResult<u64> {
    let file = regular(path)?;
    single_cue_file(&file)?;
    total
        .checked_add(file.metadata()?.len())
        .ok_or_else(|| invalid("editable storage byte count overflow"))
}

fn relative_path(name: &str) -> SupervisorResult<Vec<&str>> {
    let parts: Vec<_> = name.split('/').collect();
    if name.len() > 256
        || parts.is_empty()
        || parts.len() > 2
        || parts.iter().any(|s| {
            s.is_empty()
                || *s == "."
                || *s == ".."
                || !s
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"._-".contains(&c))
        })
        || (parts.len() == 2 && !["media", "audio", "subtitles", "metadata"].contains(&parts[0]))
    {
        return Err(invalid(
            "editable file pin is not a portable closed resource path",
        ));
    }
    Ok(parts)
}

/// Verify complete inventory/hash/handle identity before optional readonly seal.
/// Sealing is only used for native-derived private candidates; existing sealed
/// packs are revalidated without rewriting bytes or modification times.
pub(crate) fn verify_editable_files(
    root: &Path,
    files: &[EditableFileSpec],
    seal: bool,
    deadline: Instant,
) -> SupervisorResult<()> {
    plain(root)?;
    if !root.is_absolute()
        || !fs::symlink_metadata(root)?.file_type().is_dir()
        || !(10..=13).contains(&files.len())
    {
        return Err(invalid("editable pack root or complete file count invalid"));
    }
    let mut expected = HashMap::new();
    let mut directories = HashSet::new();
    for spec in files {
        let parts = relative_path(&spec.relative_path)?;
        if !valid_hash(&spec.sha256)
            || spec.size_bytes == 0
            || expected.insert(spec.relative_path.as_str(), spec).is_some()
        {
            return Err(invalid("invalid or duplicate editable file pin"));
        }
        if parts.len() == 2 {
            directories.insert(parts[0]);
        }
    }
    let mut found = HashSet::new();
    let mut found_directories = HashSet::new();
    let mut pending = vec![(root.to_path_buf(), String::new())];
    while let Some((directory, prefix)) = pending.pop() {
        plain(&directory)?;
        for entry in fs::read_dir(&directory)? {
            time_left(Some(deadline))?;
            let entry = entry?;
            let name = entry.file_name();
            let name = name
                .to_str()
                .ok_or_else(|| invalid("editable filename is not UTF-8"))?;
            let relative = format!("{prefix}{name}");
            plain(&entry.path())?;
            if entry.file_type()?.is_dir() {
                if !prefix.is_empty()
                    || !directories.contains(name)
                    || !found_directories.insert(name.to_owned())
                {
                    return Err(invalid("editable pack contains an unregistered directory"));
                }
                pending.push((entry.path(), format!("{name}/")));
                continue;
            }
            let spec = expected
                .get(relative.as_str())
                .ok_or_else(|| invalid("editable pack contains an unregistered file"))?;
            if !found.insert(relative) {
                return Err(invalid("duplicate editable file"));
            }
            let mut file = regular(&entry.path())?;
            let identity = single_cue_file(&file)?;
            let before = file.metadata()?;
            if before.len() != spec.size_bytes || (!seal && !before.permissions().readonly()) {
                return Err(invalid("editable file size/seal differs from native pin"));
            }
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
                    .ok_or_else(|| invalid("editable file size overflow"))?;
                if count > spec.size_bytes {
                    return Err(invalid("editable file grew"));
                }
                sha.update(&buffer[..n]);
            }
            if count != spec.size_bytes || format!("sha256:{:x}", sha.finalize()) != spec.sha256 {
                return Err(invalid("editable full file hash changed"));
            }
            let reopened = regular(&entry.path())?;
            if single_cue_file(&reopened)? != identity
                || single_cue_file(&file)? != identity
                || reopened.metadata()?.len() != before.len()
                || reopened.metadata()?.modified()? != before.modified()?
            {
                return Err(invalid("editable file identity changed during hashing"));
            }
            if seal && !before.permissions().readonly() {
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
                let writable = options.open(entry.path())?;
                if single_cue_file(&writable)? != identity {
                    return Err(invalid("editable file changed before sync"));
                }
                writable.sync_all()?;
                let mut permissions = before.permissions();
                permissions.set_readonly(true);
                fs::set_permissions(entry.path(), permissions)?;
            }
            let after = regular(&entry.path())?;
            if single_cue_file(&after)? != identity
                || !after.metadata()?.permissions().readonly()
                || after.metadata()?.len() != before.len()
                || after.metadata()?.modified()? != before.modified()?
            {
                return Err(invalid("editable readonly seal changed its file identity"));
            }
        }
    }
    if found.len() != files.len() || found_directories.len() != directories.len() {
        return Err(invalid("editable pack lacks a registered resource"));
    }
    time_left(Some(deadline))
}

impl RetainedMixStore {
    pub(crate) fn editable_pending_directory(&self, generation: &str) -> SupervisorResult<PathBuf> {
        if !valid_hash(&format!("sha256:{generation}")) {
            return Err(invalid("invalid editable generation key"));
        }
        let tag = self.objects.root.join(".editable-pending-v1");
        directory(&tag)?;
        let root = tag.join(generation);
        directory(&root)?;
        Ok(root)
    }

    pub(crate) fn editable_published_directory(
        &self,
        generation: &str,
    ) -> SupervisorResult<Option<PathBuf>> {
        if !valid_hash(&format!("sha256:{generation}")) {
            return Err(invalid("invalid editable generation key"));
        }
        let root = self
            .objects
            .root
            .join(".editable-published-v1")
            .join(generation);
        match fs::symlink_metadata(&root) {
            Ok(info) => {
                plain(&root)?;
                if !info.is_dir() {
                    return Err(invalid("editable version is not a directory"));
                }
                Ok(Some(root))
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(error.into()),
        }
    }

    pub(crate) fn editable_capacity(
        &self,
        available: u64,
        deadline: Instant,
    ) -> SupervisorResult<()> {
        if self.used_bytes(deadline)? > self.budget.maximum_bytes
            || available < self.budget.minimum_free_bytes
        {
            return Err(invalid(
                "editable aggregate quota or fresh caller reserve unavailable",
            ));
        }
        time_left(Some(deadline))
    }

    pub(crate) fn publish_editable_directory(
        &self,
        generation: &str,
        files: &[EditableFileSpec],
        mut available: impl FnMut(&Path) -> SupervisorResult<u64>,
        mut fence: impl FnMut() -> SupervisorResult<()>,
        deadline: Instant,
    ) -> SupervisorResult<PathBuf> {
        let verify = |root: &Path| -> SupervisorResult<()> {
            let mut entries = fs::read_dir(root)?;
            let entry = entries
                .next()
                .ok_or_else(|| invalid("editable version is empty"))??;
            if entry.file_name() != "import-pack" || entries.next().is_some() {
                return Err(invalid(
                    "editable generation contains an unregistered checkpoint",
                ));
            }
            verify_editable_files(&root.join("import-pack"), files, false, deadline)
        };
        if let Some(root) = self.editable_published_directory(generation)? {
            verify(&root)?;
            self.editable_capacity(available(&root)?, deadline)?;
            fence()?;
            return Ok(root.join("import-pack"));
        }
        let pending = self.editable_pending_directory(generation)?;
        verify(&pending)?;
        let tag = self.objects.root.join(".editable-published-v1");
        directory(&tag)?;
        let target = tag.join(generation);
        self.editable_capacity(available(&pending)?, deadline)?;
        fence()?;
        time_left(Some(deadline))?;
        export_copy::rename_new(&pending, &target)?;
        verify(&target)?;
        self.editable_capacity(available(&target)?, deadline)?;
        fence()?;
        Ok(target.join("import-pack"))
    }
}

#[cfg(test)]
mod tests {
    use super::super::super::review_seed::tests::with_admitted_original;
    use super::*;

    fn release(root: &Path) {
        for entry in fs::read_dir(root).unwrap() {
            let entry = entry.unwrap();
            if entry.file_type().unwrap().is_dir() {
                release(&entry.path());
            } else {
                let mut permissions = entry.metadata().unwrap().permissions();
                #[cfg(windows)]
                permissions.set_readonly(false);
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    permissions.set_mode(0o600);
                }
                fs::set_permissions(entry.path(), permissions).unwrap();
            }
        }
    }
    fn specs(root: &Path) -> Vec<EditableFileSpec> {
        directory(root).unwrap();
        (0..10)
            .map(|i| {
                let name = format!("file-{i}.txt");
                let raw = format!("native byte-only file {i}");
                fs::write(root.join(&name), &raw).unwrap();
                EditableFileSpec {
                    relative_path: name,
                    sha256: format!("sha256:{:x}", Sha256::digest(raw.as_bytes())),
                    size_bytes: raw.len() as u64,
                }
            })
            .collect()
    }

    #[cfg(any(target_os = "linux", windows))]
    #[test]
    fn editable_inventory_counts_other_jobs_partial_directories_orphans_and_refuses_unknown_depth()
    {
        with_admitted_original(|_, data, _| {
            let output = data.join("editable quota");
            fs::create_dir(&output).unwrap();
            let store = RetainedMixStore::new(
                &output,
                "one",
                MixStorageBudget {
                    maximum_bytes: 1000,
                    minimum_free_bytes: 1,
                },
            )
            .unwrap();
            let other = store.namespace.join("a".repeat(64));
            directory(&other).unwrap();
            let tag = other.join(".editable-pending-v1");
            directory(&tag).unwrap();
            let gen = tag.join("b".repeat(64));
            directory(&gen).unwrap();
            let pack = gen.join(format!(".import-pack.{}.partial", "c".repeat(32)));
            directory(&pack).unwrap();
            let media = pack.join("media");
            directory(&media).unwrap();
            fs::write(media.join("foreign.partial"), vec![1; 700]).unwrap();
            fs::write(gen.join("orphan.bin"), vec![1; 100]).unwrap();
            let deadline = Instant::now() + std::time::Duration::from_secs(5);
            assert_eq!(store.used_bytes(deadline).unwrap(), 800);
            store.editable_capacity(1, deadline).unwrap();
            fs::write(media.join("another.partial"), vec![1; 201]).unwrap();
            assert!(store.editable_capacity(u64::MAX, deadline).is_err());
            let nested = media.join("not-a-file");
            fs::create_dir(&nested).unwrap();
            assert!(store.used_bytes(deadline).is_err());
            fs::remove_dir(nested).unwrap();
            release(&output);
        });
    }

    #[cfg(any(target_os = "linux", windows))]
    #[test]
    fn editable_publication_fence_refusal_preserves_staging_then_reuses_complete_version_without_rewrite(
    ) {
        with_admitted_original(|_, data, _| {
            let output = data.join("editable atomic");
            fs::create_dir(&output).unwrap();
            let store = RetainedMixStore::new(
                &output,
                "editable",
                MixStorageBudget {
                    maximum_bytes: 10000,
                    minimum_free_bytes: 1,
                },
            )
            .unwrap();
            let generation = "d".repeat(64);
            let pending = store.editable_pending_directory(&generation).unwrap();
            let pack = pending.join("import-pack");
            let files = specs(&pack);
            let deadline = Instant::now() + std::time::Duration::from_secs(5);
            verify_editable_files(&pack, &files, true, deadline).unwrap();
            assert!(store
                .publish_editable_directory(
                    &generation,
                    &files,
                    |_| Ok(u64::MAX),
                    || Err(invalid("changed current head")),
                    deadline
                )
                .is_err());
            assert!(pack.is_dir());
            assert!(store
                .editable_published_directory(&generation)
                .unwrap()
                .is_none());
            let path = store
                .publish_editable_directory(
                    &generation,
                    &files,
                    |_| Ok(u64::MAX),
                    || Ok(()),
                    deadline,
                )
                .unwrap();
            assert!(!pack.exists());
            let mtime = fs::metadata(path.join(&files[0].relative_path))
                .unwrap()
                .modified()
                .unwrap();
            assert_eq!(
                store
                    .publish_editable_directory(
                        &generation,
                        &files,
                        |_| Ok(u64::MAX),
                        || Ok(()),
                        deadline
                    )
                    .unwrap(),
                path
            );
            assert_eq!(
                fs::metadata(path.join(&files[0].relative_path))
                    .unwrap()
                    .modified()
                    .unwrap(),
                mtime
            );
            assert!(store
                .publish_editable_directory(&generation, &files, |_| Ok(0), || Ok(()), deadline)
                .is_err());
            release(&output);
        });
    }
}
