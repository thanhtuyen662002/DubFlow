//! Native private-work quota extents, counted by the unchanged alljob byte walk.
//! A sparse regular control file accounts logical private capacity. It is not a
//! retained media object or source/runtime/graph admission. Drop never deletes it.
use super::*;
use serde_json::{json, Value};

const HEADER_LIMIT: usize = 4096;
const PRIVATE_LIMIT: u64 = (512 + 64) * 1024 * 1024;
const WORK_ENTRIES: usize = 4096;
const WORK_DEPTH: usize = 4;

#[cfg(windows)]
mod win {
    use super::*;
    use std::ffi::c_void;
    use std::os::windows::io::AsRawHandle;
    #[repr(C)]
    struct DirectoryInformation {
        attributes: u32,
        created: [u32; 2],
        accessed: [u32; 2],
        written: [u32; 2],
        volume: u32,
        size_high: u32,
        size_low: u32,
        links: u32,
        index_high: u32,
        index_low: u32,
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn DeviceIoControl(
            file: *mut c_void,
            code: u32,
            input: *const c_void,
            input_size: u32,
            output: *mut c_void,
            output_size: u32,
            returned: *mut u32,
            overlapped: *mut c_void,
        ) -> i32;
        fn SetFileInformationByHandle(
            file: *mut c_void,
            class: u32,
            data: *const c_void,
            size: u32,
        ) -> i32;
        fn GetFileInformationByHandle(
            file: *mut c_void,
            information: *mut DirectoryInformation,
        ) -> i32;
    }
    pub(super) fn directory_identity(file: &File) -> SupervisorResult<(u64, u64)> {
        // A work directory is deliberately not retained regular cue media.
        // BY_HANDLE_FILE_INFORMATION is52bytes on Windows x64; require the
        // directory bit rather than weakening single_cue_file's file guard.
        if !file.metadata()?.is_dir() {
            return Err(invalid("chunk work handle is not a directory"));
        }
        let mut information: DirectoryInformation = unsafe { std::mem::zeroed() };
        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut information) } == 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        if information.links != 1
            || information.attributes & 0x10 == 0
            || information.attributes & 0x400 != 0
        {
            return Err(invalid(
                "chunk work requires a single-link no-reparse directory handle",
            ));
        }
        Ok((
            u64::from(information.volume),
            u64::from(information.index_high) << 32 | u64::from(information.index_low),
        ))
    }

    #[cfg(test)]
    pub(super) fn directory_information_size() -> usize {
        std::mem::size_of::<DirectoryInformation>()
    }
    pub(super) fn sparse(file: &File) -> SupervisorResult<()> {
        let mut returned = 0;
        // FSCTL_SET_SPARSE: null input sets sparse; metadata.len still accounts
        // the entire logical extent in every existing Review quota writer.
        if unsafe {
            DeviceIoControl(
                file.as_raw_handle(),
                0x0009_00c4,
                std::ptr::null(),
                0,
                std::ptr::null_mut(),
                0,
                &mut returned,
                std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(std::io::Error::last_os_error().into());
        }
        Ok(())
    }
    pub(super) fn remove(file: &File) -> SupervisorResult<()> {
        // FILE_DISPOSITION_INFO contains BOOLEAN (one byte), FileDispositionInfo=4.
        // Creation/recovery requested DELETE and disallowed external delete-share.
        let delete = 1u8;
        if unsafe {
            SetFileInformationByHandle(file.as_raw_handle(), 4, (&delete as *const u8).cast(), 1)
        } == 0
        {
            return Err(std::io::Error::last_os_error().into());
        }
        Ok(())
    }
}

fn open_control(path: &Path, create: bool) -> SupervisorResult<File> {
    plain(
        path.parent()
            .ok_or_else(|| invalid("chunk capacity parent missing"))?,
    )?;
    if !create {
        drop(regular(path)?);
    }
    let mut options = OpenOptions::new();
    options.read(true).write(create).create_new(create);
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(0x20000 | 0x800);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        // Reopening a readonly extent needs read/DELETE/WRITE_ATTRIBUTES rather
        // than generic content-write permission. No handle is cloneable/exported.
        options.access_mode(if create { 0xc001_0000 } else { 0x8001_0100 });
        options.share_mode(0x1 | 0x2).custom_flags(0x0020_0000);
    }
    let file = options.open(path)?;
    plain(path)?;
    if !file.metadata()?.is_file() {
        return Err(invalid("chunk capacity is not a regular control file"));
    }
    single_cue_file(&file)?;
    match file.try_lock() {
        Ok(()) => Ok(file),
        Err(TryLockError::WouldBlock) => {
            Err(invalid("chunk capacity still belongs to its active worker"))
        }
        Err(TryLockError::Error(error)) => Err(error.into()),
    }
}

fn open_work(path: &Path) -> SupervisorResult<File> {
    plain(path)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(0x20000 | 0x10000 | 0x800);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options
            .custom_flags(0x0200_0000 | 0x0020_0000)
            .share_mode(0x1 | 0x2);
    }
    let file = options.open(path)?;
    plain(path)?;
    if !file.metadata()?.is_dir() {
        return Err(invalid("chunk workspace must be a plain native directory"));
    }
    Ok(file)
}
fn work_identity(file: &File) -> SupervisorResult<(u64, u64)> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = file.metadata()?;
        Ok((metadata.dev(), metadata.ino()))
    }
    #[cfg(windows)]
    {
        win::directory_identity(file)
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = file;
        Err(invalid("chunk work identity unavailable"))
    }
}
fn header(
    job_namespace: &str,
    scope: &str,
    maximum: u64,
    work: (u64, u64),
) -> SupervisorResult<Vec<u8>> {
    let value = json!({"schema_version":1,"kind":"review_native_chunk_capacity","job_namespace":job_namespace,
        "scope_sha256":scope,"maximum_private_bytes":maximum.to_string(),
        "work_identity":{"volume":work.0.to_string(),"file":work.1.to_string()}});
    let mut raw = String::new();
    super::super::review_document::canonical(&value, &mut raw)?;
    if raw.is_empty() || raw.len() > HEADER_LIMIT {
        return Err(invalid("chunk capacity header exceeds its bound"));
    }
    let mut bytes = (raw.len() as u32).to_be_bytes().to_vec();
    bytes.extend_from_slice(raw.as_bytes());
    Ok(bytes)
}

/// Owned control/work handles survive only for this native worker. Crash/Drop
/// releases OS handles while leaving the full quota extent and workspace intact.
pub(crate) struct ChunkCapacity {
    path: PathBuf,
    work: PathBuf,
    file: File,
    work_file: File,
    identity: (u64, u64),
    directory_identity: (u64, u64),
    header: Vec<u8>,
    extent: u64,
    maximum: u64,
    modified: std::time::SystemTime,
    scope: String,
    recovered: bool,
}
impl ChunkCapacity {
    pub(crate) fn work_root(&self) -> &Path {
        &self.work
    }
    pub(crate) fn scope_sha256(&self) -> &str {
        &self.scope
    }
    pub(crate) fn maximum_private_bytes(&self) -> u64 {
        self.maximum
    }
    pub(crate) fn recovered(&self) -> bool {
        self.recovered
    }
    fn verify(&self, store: &RetainedMixStore, deadline: Instant) -> SupervisorResult<()> {
        time_left(Some(deadline))?;
        if self.path.parent() != Some(store.objects.root.as_path()) {
            return Err(invalid("chunk capacity belongs to another native job"));
        }
        let metadata = self.file.metadata()?;
        if single_cue_file(&self.file)? != self.identity
            || metadata.len() != self.extent
            || !metadata.permissions().readonly()
            || metadata.modified()? != self.modified
        {
            return Err(invalid("chunk capacity size/identity/seal changed"));
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x200 == 0 {
                return Err(invalid("chunk capacity lost its sparse control attribute"));
            }
        }
        let fresh = regular(&self.path)?;
        if single_cue_file(&fresh)? != self.identity
            || fresh.metadata()?.len() != self.extent
            || !fresh.metadata()?.permissions().readonly()
        {
            return Err(invalid("chunk capacity pathname changed its pinned handle"));
        }
        let mut held = &self.file;
        held.seek(SeekFrom::Start(0))?;
        let mut prefix = vec![0u8; self.header.len()];
        held.read_exact(&mut prefix)?;
        if prefix != self.header {
            return Err(invalid("chunk capacity native header changed"));
        }
        if work_identity(&self.work_file)? != self.directory_identity
            || work_identity(&open_work(&self.work)?)? != self.directory_identity
        {
            return Err(invalid(
                "chunk workspace changed its native directory identity",
            ));
        }
        time_left(Some(deadline))
    }
    fn work_bytes(
        &self,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<(u64, usize)> {
        let mut pending = vec![(self.work.clone(), 0usize)];
        let mut count = 0usize;
        let mut files = 0usize;
        let mut bytes = 0u64;
        while let Some((path, depth)) = pending.pop() {
            plain(&path)?;
            for entry in fs::read_dir(path)? {
                if cancelled() {
                    return Err(invalid("chunk capacity inventory cancelled"));
                }
                time_left(Some(deadline))?;
                count += 1;
                if count > WORK_ENTRIES {
                    return Err(invalid("chunk workspace exceeds bounded inventory"));
                }
                let path = entry?.path();
                plain(&path)?;
                if fs::symlink_metadata(&path)?.is_dir() {
                    if depth >= WORK_DEPTH {
                        return Err(invalid("chunk workspace exceeds bounded directory depth"));
                    }
                    pending.push((path, depth + 1));
                } else {
                    let file = regular(&path)?;
                    single_cue_file(&file)?;
                    files += 1;
                    bytes = bytes
                        .checked_add(file.metadata()?.len())
                        .filter(|n| *n <= self.maximum)
                        .ok_or_else(|| {
                            invalid("chunk private bytes exceed reserved native capacity")
                        })?;
                }
            }
        }
        if cancelled() {
            return Err(invalid("chunk capacity inventory cancelled"));
        }
        time_left(Some(deadline))?;
        Ok((bytes, files))
    }
}

impl RetainedMixStore {
    pub(crate) fn reserve_chunk_capacity(
        &self,
        scope: &str,
        maximum: u64,
        mut available: impl FnMut(&Path) -> SupervisorResult<u64>,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<ChunkCapacity> {
        if cancelled() {
            return Err(invalid(
                "chunk capacity admission cancelled before allocation",
            ));
        }
        if !valid_hash(scope) || !(1..=PRIVATE_LIMIT).contains(&maximum) {
            return Err(invalid(
                "chunk capacity needs native full scope and bounded private policy",
            ));
        }
        time_left(Some(deadline))?;
        let job = self
            .objects
            .root
            .file_name()
            .and_then(|s| s.to_str())
            .ok_or_else(|| invalid("native chunk job namespace missing"))?;
        let output = self
            .namespace
            .parent()
            .ok_or_else(|| invalid("chunk output root missing"))?;
        let base = output.join(".dubflow-review-candidates");
        let parent = base.join(job);
        let work = parent.join(format!("chunk-capacity-v1-{}", &scope[7..]));
        let path = self
            .objects
            .root
            .join(format!(".chunk-capacity-v1-{}.bin", &scope[7..]));
        let recovered = match fs::symlink_metadata(&path) {
            Ok(_) => true,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => false,
            Err(e) => return Err(e.into()),
        };
        for directory_path in [&base, &parent, &work] {
            if recovered {
                plain(directory_path)?;
                if !fs::symlink_metadata(directory_path)?.is_dir() {
                    return Err(invalid(
                        "chunk capacity workspace is missing; restore it explicitly",
                    ));
                }
            } else {
                directory(directory_path)?;
            }
        }
        let work_file = open_work(&work)?;
        let directory_identity = work_identity(&work_file)?;
        if !recovered && fs::read_dir(&work)?.next().is_some() {
            return Err(invalid(
                "new chunk capacity cannot adopt a preexisting nonempty workspace",
            ));
        }
        let header = header(job, scope, maximum, directory_identity)?;
        let extent = maximum
            .checked_add(header.len() as u64)
            .ok_or_else(|| invalid("chunk capacity extent overflow"))?;
        let used = self.used_bytes(deadline)?;
        let projected = if recovered {
            used
        } else {
            used.checked_add(extent)
                .ok_or_else(|| invalid("chunk capacity quota overflow"))?
        };
        if projected > self.budget.maximum_bytes {
            return Err(invalid(
                "chunk capacity exceeds shared old/undo/orphan/alljob quota",
            ));
        }
        let required = maximum
            .checked_add(self.budget.minimum_free_bytes)
            .ok_or_else(|| invalid("chunk capacity caller reserve overflow"))?;
        if available(&self.namespace)? < required || available(&work)? < required {
            return Err(invalid("chunk capacity lacks fresh caller space"));
        }
        time_left(Some(deadline))?;
        let mut file = open_control(&path, !recovered)?;
        if !recovered {
            // Sparse logical length is counted by old quota writers. Fresh free
            // bytes remain separately checked before each worker allocation.
            #[cfg(windows)]
            win::sparse(&file)?;
            file.set_len(extent)?;
            file.write_all(&header)?;
            file.sync_all()?;
            time_left(Some(deadline))?;
            let mut mode = file.metadata()?.permissions();
            mode.set_readonly(true);
            file.set_permissions(mode)?;
        }
        let modified = file.metadata()?.modified()?;
        let identity = single_cue_file(&file)?;
        let token = ChunkCapacity {
            path,
            work,
            file,
            work_file,
            identity,
            directory_identity,
            header,
            extent,
            maximum,
            modified,
            scope: scope.into(),
            recovered,
        };
        token.verify(self, deadline)?;
        let (bytes, files) = token.work_bytes(deadline, cancelled)?;
        if !recovered && (bytes != 0 || files != 0) {
            return Err(invalid(
                "new chunk capacity cannot repin preexisting private files",
            ));
        }
        if available(&token.work)? < required {
            return Err(invalid(
                "chunk caller reserve changed during native admission",
            ));
        }
        time_left(Some(deadline))?;
        Ok(token)
    }

    pub(crate) fn check_chunk_capacity(
        &self,
        token: &ChunkCapacity,
        additional: u64,
        mut available: impl FnMut(&Path) -> SupervisorResult<u64>,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<()> {
        token.verify(self, deadline)?;
        let (bytes, _) = token.work_bytes(deadline, cancelled)?;
        if bytes
            .checked_add(additional)
            .is_none_or(|n| n > token.maximum)
        {
            return Err(invalid(
                "chunk request exceeds remaining reserved private capacity",
            ));
        }
        if self.used_bytes(deadline)? > self.budget.maximum_bytes {
            return Err(invalid("shared Review quota changed during chunk work"));
        }
        let needed = additional
            .checked_add(self.budget.minimum_free_bytes)
            .ok_or_else(|| invalid("chunk caller reserve overflow"))?;
        if available(&self.namespace)? < needed || available(&token.work)? < needed {
            return Err(invalid("chunk caller free space changed"));
        }
        token.verify(self, deadline)?;
        time_left(Some(deadline))
    }

    pub(crate) fn release_chunk_capacity(
        &self,
        token: ChunkCapacity,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<()> {
        token.verify(self, deadline)?;
        if token.work_bytes(deadline, cancelled)?.1 != 0 {
            return Err(invalid(
                "chunk private work is not empty; preserve accounted quota extent",
            ));
        }
        token.verify(self, deadline)?;
        let mut mode = token.file.metadata()?.permissions();
        mode.set_readonly(false);
        token.file.set_permissions(mode)?;
        #[cfg(windows)]
        if let Err(error) = win::remove(&token.file) {
            let mut mode = token.file.metadata()?.permissions();
            mode.set_readonly(true);
            let _ = token.file.set_permissions(mode);
            return Err(error);
        }
        #[cfg(not(windows))]
        if let Err(error) = fs::remove_file(&token.path) {
            let mut mode = token.file.metadata()?.permissions();
            mode.set_readonly(true);
            let _ = token.file.set_permissions(mode);
            return Err(error.into());
        }
        let path = token.path.clone();
        drop(token);
        match fs::symlink_metadata(path) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            _ => Err(invalid("chunk quota extent release is not yet visible")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, SystemTime, UNIX_EPOCH};

    #[cfg(windows)]
    #[test]
    fn native_directory_identity_accepts_only_directory_handles_without_weakening_cue_media() {
        let fixture = Fixture::new();
        let work = fixture.0.join("directory-only-identity");
        fs::create_dir(&work).unwrap();
        let directory = open_work(&work).unwrap();
        assert_eq!(win::directory_information_size(), 52);
        assert_eq!(
            work_identity(&directory).unwrap(),
            work_identity(&open_work(&work).unwrap()).unwrap()
        );
        assert!(single_cue_file(&directory).is_err());
        let path = fixture.0.join("regular-file-is-not-work");
        fs::write(&path, b"owned regular fixture").unwrap();
        let file = File::open(&path).unwrap();
        assert!(single_cue_file(&file).is_ok());
        assert!(work_identity(&file).is_err());
    }
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "dubflow-chunk-capacity-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            assert!(!root.exists());
            fs::create_dir(&root).unwrap();
            Self(root)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let root = self.0.canonicalize().unwrap();
            let parent = std::env::temp_dir().canonicalize().unwrap();
            assert_eq!(root.parent(), Some(parent.as_path()));
            assert!(root
                .file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("dubflow-chunk-capacity-"));
            fn unseal(path: &Path) {
                for entry in fs::read_dir(path).unwrap() {
                    let path = entry.unwrap().path();
                    let metadata = fs::symlink_metadata(&path).unwrap();
                    assert!(!metadata.file_type().is_symlink());
                    if metadata.is_dir() {
                        unseal(&path);
                    } else {
                        let mut mode = metadata.permissions();
                        mode.set_readonly(false);
                        fs::set_permissions(path, mode).unwrap();
                    }
                }
            }
            unseal(&root);
            fs::remove_dir_all(root).unwrap();
        }
    }
    fn budget(maximum_bytes: u64) -> MixStorageBudget {
        MixStorageBudget {
            maximum_bytes,
            minimum_free_bytes: 100,
        }
    }
    fn deadline() -> Instant {
        Instant::now() + Duration::from_secs(30)
    }
    fn scope() -> String {
        format!(
            "sha256:{:x}",
            Sha256::digest(b"native current context/plan/chunk/environment fixture")
        )
    }
    fn owned_bytes(token: &ChunkCapacity) -> Vec<u8> {
        let mut file = &token.file;
        file.seek(SeekFrom::Start(0)).unwrap();
        let mut bytes = Vec::new();
        file.take(token.extent + 1).read_to_end(&mut bytes).unwrap();
        assert_eq!(bytes.len() as u64, token.extent);
        bytes
    }
    #[test]
    fn regular_logical_extent_counts_in_existing_alljob_quota_after_output_writer_release() {
        let f = Fixture::new();
        let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
        let token = store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        let used = store.used_bytes(deadline()).unwrap();
        assert_eq!(used, token.extent);
        assert!(used >= 4096);
        let path = token.path.clone();
        let before = fs::metadata(&path).unwrap().modified().unwrap();
        drop(store);
        // A native worker owns only its control handle, so another job can take
        // the real output writer; its unchanged quota walk counts the extent.
        let other = RetainedMixStore::new(&f.0, "other-job", budget(used + 1)).unwrap();
        assert_eq!(other.used_bytes(deadline()).unwrap(), used);
        assert!(other
            .accept_json_record(
                "{\"new\":1}",
                |_| panic!("quota must refuse before available probe"),
                deadline()
            )
            .is_err());
        assert_eq!(fs::metadata(path).unwrap().modified().unwrap(), before);
        drop(other);
        drop(token);
    }
    #[test]
    fn native_private_capacity_checks_remaining_bytes_caller_space_cancel_and_no_metadata_rewrite()
    {
        let f = Fixture::new();
        let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
        let token = store
            .reserve_chunk_capacity(&scope(), 8192, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        let path = token.work_root().join("partial.bin");
        fs::write(&path, vec![7u8; 4096]).unwrap();
        let before = (
            owned_bytes(&token),
            fs::metadata(&token.path).unwrap().modified().unwrap(),
        );
        store
            .check_chunk_capacity(&token, 4096, |_| Ok(4196), deadline(), &mut || false)
            .unwrap();
        assert!(store
            .check_chunk_capacity(
                &token,
                4097,
                |_| panic!("private limit before OS probe"),
                deadline(),
                &mut || false
            )
            .is_err());
        assert!(store
            .check_chunk_capacity(&token, 4096, |_| Ok(4195), deadline(), &mut || false)
            .is_err());
        assert!(store
            .check_chunk_capacity(&token, 0, |_| Ok(u64::MAX), deadline(), &mut || true)
            .is_err());
        assert_eq!(
            (
                owned_bytes(&token),
                fs::metadata(&token.path).unwrap().modified().unwrap()
            ),
            before
        );
        assert_eq!(fs::read(path).unwrap(), vec![7u8; 4096]);
    }
    #[test]
    fn orphan_capacity_reopens_same_scope_preserves_work_and_releases_only_after_all_files_removed()
    {
        let f = Fixture::new();
        let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
        let token = store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        let private = token.work_root().join("zero-byte-private.json");
        fs::write(&private, b"").unwrap();
        let extent = token.path.clone();
        let before = fs::metadata(&extent).unwrap().modified().unwrap();
        assert!(store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .is_err());
        drop(token);
        let recovered = store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        assert!(recovered.recovered());
        assert_eq!(fs::metadata(&extent).unwrap().modified().unwrap(), before);
        assert!(store
            .release_chunk_capacity(recovered, deadline(), &mut || false)
            .is_err());
        assert!(extent.exists() && private.exists());
        let recovered = store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        fs::remove_file(&private).unwrap(); // Only this fresh fixture-owned file.
        store
            .release_chunk_capacity(recovered, deadline(), &mut || false)
            .unwrap();
        assert!(!extent.exists());
        assert_eq!(store.used_bytes(deadline()).unwrap(), 0);
    }
    #[test]
    fn capacity_foreign_job_changed_limit_or_header_refuses_without_repin_or_implicit_cleanup() {
        let f = Fixture::new();
        let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
        let token = store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        let extent = token.path.clone();
        drop(store);
        let other = RetainedMixStore::new(&f.0, "other-job", budget(100000)).unwrap();
        assert!(other
            .check_chunk_capacity(
                &token,
                0,
                |_| panic!("foreign job before OS probe"),
                deadline(),
                &mut || false
            )
            .is_err());
        drop(other);
        let store = RetainedMixStore::open_existing(&f.0, "capacity-job", budget(100000)).unwrap();
        drop(token);
        assert!(store
            .reserve_chunk_capacity(&scope(), 4097, |_| Ok(u64::MAX), deadline(), &mut || false)
            .is_err());
        let token = store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        let mut mode = token.file.metadata().unwrap().permissions();
        mode.set_readonly(false);
        token.file.set_permissions(mode).unwrap();
        assert!(store
            .check_chunk_capacity(&token, 0, |_| Ok(u64::MAX), deadline(), &mut || false)
            .is_err());
        let mut mode = token.file.metadata().unwrap().permissions();
        mode.set_readonly(true);
        token.file.set_permissions(mode).unwrap();
        drop(token);
        let mut mode = fs::metadata(&extent).unwrap().permissions();
        mode.set_readonly(false);
        fs::set_permissions(&extent, mode).unwrap();
        let mut writer = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&extent)
            .unwrap();
        writer.seek(SeekFrom::Start(4)).unwrap();
        writer.write_all(b"!").unwrap();
        writer.sync_all().unwrap();
        drop(writer);
        let mut mode = fs::metadata(&extent).unwrap().permissions();
        mode.set_readonly(true);
        fs::set_permissions(&extent, mode).unwrap();
        assert!(store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .is_err());
        assert!(extent.exists());
    }
    #[test]
    fn workspace_depth_or_directory_replacement_refuses_and_native_windows_handle_blocks_rename() {
        let f = Fixture::new();
        let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
        let token = store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        let mut path = token.work_root().to_path_buf();
        for _ in 0..6 {
            path = path.join("nested");
            fs::create_dir(&path).unwrap();
        }
        assert!(store
            .check_chunk_capacity(&token, 0, |_| Ok(u64::MAX), deadline(), &mut || false)
            .is_err());
        #[cfg(windows)]
        {
            assert!(fs::rename(&token.path, token.path.with_extension("foreign")).is_err());
            assert!(fs::rename(
                token.work_root(),
                token.work_root().with_extension("foreign")
            )
            .is_err());
            let work = token.work_root().to_path_buf();
            drop(token);
            fs::rename(&work, work.with_extension("prior")).unwrap();
            fs::create_dir(&work).unwrap();
            assert!(store
                .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
                .is_err());
        }
        #[cfg(unix)]
        {
            fs::rename(token.work_root(), token.work_root().with_extension("prior")).unwrap();
            fs::create_dir(token.work_root()).unwrap();
            assert!(store
                .check_chunk_capacity(&token, 0, |_| Ok(u64::MAX), deadline(), &mut || false)
                .is_err());
        }
    }
    #[test]
    fn missing_recovery_workspace_is_never_recreated_and_quota_extent_remains_accounted() {
        let f = Fixture::new();
        let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
        let token = store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        let work = token.work_root().to_path_buf();
        let extent = token.path.clone();
        let used = store.used_bytes(deadline()).unwrap();
        drop(token);
        fs::remove_dir(&work).unwrap(); // Only this freshly owned empty fixture directory.
        assert!(store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .is_err());
        assert!(!work.exists() && extent.exists());
        assert_eq!(store.used_bytes(deadline()).unwrap(), used);
    }
}
