//! Native private-work quota extents, counted by the unchanged alljob byte walk.
//! A sparse regular control file accounts logical private capacity. It is not a
//! retained media object or source/runtime/graph admission. Drop never deletes it.
use super::*;
use serde_json::{Value, json};

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
        fn CreateFileMappingW(
            file: *mut c_void,
            attributes: *const c_void,
            protection: u32,
            high: u32,
            low: u32,
            name: *const u16,
        ) -> *mut c_void;
        fn MapViewOfFile(
            mapping: *mut c_void,
            access: u32,
            high: u32,
            low: u32,
            bytes: usize,
        ) -> *mut c_void;
        fn UnmapViewOfFile(view: *const c_void) -> i32;
        fn CloseHandle(handle: *mut c_void) -> i32;
    }

    /// Read at most HEADER_LIMIT+4 bytes without disturbing the existing byte
    /// lock. Windows denies second-handle ReadFile on a locked range, including
    /// in the owning process; read-only mappings do not acquire/release that
    /// lock. This returns bytes only, never a worker/recovery capability.
    /// https://learn.microsoft.com/en-us/windows/win32/fileio/locking-and-unlocking-byte-ranges-in-files
    pub(super) fn quota_prefix(file: &File, bytes: usize) -> SupervisorResult<Vec<u8>> {
        if !(4..=HEADER_LIMIT + 4).contains(&bytes) || file.metadata()?.len() < bytes as u64 {
            return Err(invalid("chunk quota header mapping exceeds its bound"));
        }
        struct Mapping(*mut c_void);
        impl Drop for Mapping {
            fn drop(&mut self) {
                unsafe {
                    CloseHandle(self.0);
                }
            }
        }
        struct View(*mut c_void);
        impl Drop for View {
            fn drop(&mut self) {
                unsafe {
                    UnmapViewOfFile(self.0);
                }
            }
        }
        // Null attributes/name: private non-inheritable readonly mapping. Its
        // section and view are bounded to the header, not the logical extent.
        let mapping = unsafe {
            CreateFileMappingW(
                file.as_raw_handle(),
                std::ptr::null(),
                2,
                0,
                bytes as u32,
                std::ptr::null(),
            )
        };
        if mapping.is_null() {
            return Err(std::io::Error::last_os_error().into());
        }
        let mapping = Mapping(mapping);
        let view = unsafe { MapViewOfFile(mapping.0, 4, 0, 0, bytes) }; // FILE_MAP_READ.
        if view.is_null() {
            return Err(std::io::Error::last_os_error().into());
        }
        let view = View(view);
        // Both handles remain live while copying this bounded readonly view.
        Ok(unsafe { std::slice::from_raw_parts(view.0.cast::<u8>(), bytes) }.to_vec())
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

/// A byte-count snapshot from the same retained scan that charged the extent.
/// It is deliberately not a ChunkCapacity and cannot authorize a worker, scope
/// recovery, current graph, publication, release or any durable mutation.
pub(super) struct QuotaExtent {
    // Pin the read-only scan handle until its discount has been checked. This
    // prevents inode/file-ID reuse after the retained inventory observation;
    // it owns no control byte lock and is never exported to a worker.
    file: File,
    identity: (u64, u64),
    bytes: u64,
    modified: std::time::SystemTime,
}
impl QuotaExtent {
    pub(super) fn counted(file: &File) -> SupervisorResult<Self> {
        let metadata = file.metadata()?;
        Ok(Self {
            file: file.try_clone()?,
            identity: single_cue_file(file)?,
            bytes: metadata.len(),
            modified: metadata.modified()?,
        })
    }
    pub(super) fn bytes(&self) -> u64 {
        self.bytes
    }
    fn verify(&self, file: &File, path: &Path, device: u64) -> SupervisorResult<()> {
        let fresh = regular(path)?;
        for observed in [&self.file, file, &fresh] {
            let metadata = observed.metadata()?;
            if single_cue_file(observed)? != self.identity
                || self.identity.0 != device
                || metadata.len() != self.bytes
                || metadata.modified()? != self.modified
                || !metadata.permissions().readonly()
            {
                return Err(invalid(
                    "counted chunk quota extent changed identity/size/seal",
                ));
            }
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                if metadata.file_attributes() & 0x200 == 0 {
                    return Err(invalid("counted chunk quota extent is not sparse"));
                }
            }
        }
        Ok(())
    }
}

pub(super) fn quota_scope(name: &std::ffi::OsStr) -> Option<&str> {
    name.to_str()?
        .strip_prefix(".chunk-capacity-v1-")?
        .strip_suffix(".bin")
        .filter(|scope| valid_hash(&format!("sha256:{scope}")))
}

fn quota_prefix(file: &File) -> SupervisorResult<Vec<u8>> {
    let size = file.metadata()?.len().min((HEADER_LIMIT + 4) as u64) as usize;
    if size < 4 {
        return Err(invalid("chunk quota extent lacks its native header"));
    }
    #[cfg(windows)]
    {
        win::quota_prefix(file, size)
    }
    #[cfg(not(windows))]
    {
        let mut input = file;
        input.seek(SeekFrom::Start(0))?;
        let mut prefix = vec![0; size];
        input.read_exact(&mut prefix)?;
        Ok(prefix)
    }
}

/// Deduct actual workspace bytes only after proving a matching sealed extent
/// was charged by the same scan. This never calls open_control/try_lock,
/// reserve/check/release, or creates/adopts/reconstructs a missing workspace.
pub(super) fn quota_covered_bytes(
    namespace: &Path,
    job: &str,
    scope_hex: &str,
    work: &Path,
    counted: &QuotaExtent,
    deadline: Instant,
    shared: &mut usize,
    shared_limit: usize,
) -> SupervisorResult<u64> {
    time_left(Some(deadline))?;
    let scope = format!("sha256:{scope_hex}");
    if !valid_hash(&format!("sha256:{job}"))
        || !valid_hash(&scope)
        || namespace.file_name().and_then(|s| s.to_str()) != Some(".dubflow-review-media-v1")
    {
        return Err(invalid("chunk quota read requires exact native namespace"));
    }
    let output = namespace
        .parent()
        .ok_or_else(|| invalid("chunk quota output missing"))?;
    let expected_work = output
        .join(".dubflow-review-candidates")
        .join(job)
        .join(format!("chunk-capacity-v1-{scope_hex}"));
    if work != expected_work {
        return Err(invalid(
            "chunk quota read cannot discount a foreign workspace",
        ));
    }
    let output_file = open_work(output)?;
    let output_device = work_identity(&output_file)?.0;
    let path = namespace
        .join(job)
        .join(format!(".chunk-capacity-v1-{scope_hex}.bin"));
    let file = regular(&path)?;
    counted.verify(&file, &path, output_device)?;
    let prefix = quota_prefix(&file)?;
    let header_size = u32::from_be_bytes(prefix[..4].try_into().unwrap()) as usize;
    if header_size == 0 || header_size > HEADER_LIMIT || header_size + 4 > prefix.len() {
        return Err(invalid(
            "chunk quota header length differs from native bound",
        ));
    }
    let value: Value = serde_json::from_slice(&prefix[4..4 + header_size])?;
    let maximum: u64 = value
        .get("maximum_private_bytes")
        .and_then(Value::as_str)
        .ok_or_else(|| invalid("chunk quota header lacks native maximum"))?
        .parse()
        .map_err(|_| invalid("chunk quota maximum is not canonical u64"))?;
    if !(1..=PRIVATE_LIMIT).contains(&maximum)
        || maximum.checked_add((header_size + 4) as u64) != Some(counted.bytes)
    {
        return Err(invalid(
            "chunk quota maximum differs from counted logical extent",
        ));
    }
    let work_file = open_work(work)?;
    let identity = work_identity(&work_file)?;
    if identity.0 != output_device
        || header(job, &scope, maximum, identity)? != prefix[..header_size + 4]
    {
        return Err(invalid(
            "chunk quota header/location/work identity mismatch",
        ));
    }
    fn visit(
        path: &Path,
        depth: usize,
        device: u64,
        maximum: u64,
        deadline: Instant,
        shared: &mut usize,
        shared_limit: usize,
        entries: &mut usize,
    ) -> SupervisorResult<u64> {
        plain(path)?;
        let directory = open_work(path)?;
        if work_identity(&directory)?.0 != device {
            return Err(invalid("chunk quota workspace changed device"));
        }
        let mut bytes = 0u64;
        let mut children = fs::read_dir(path)?;
        loop {
            time_left(Some(deadline))?;
            let Some(entry) = children.next() else {
                break;
            };
            *entries = entries
                .checked_add(1)
                .ok_or_else(|| invalid("chunk quota entry overflow"))?;
            *shared = shared
                .checked_add(1)
                .ok_or_else(|| invalid("shared quota entry overflow"))?;
            if *entries > WORK_ENTRIES || *shared > shared_limit {
                return Err(invalid(
                    "chunk quota inventory exceeds existing shared/private bound",
                ));
            }
            let path = entry?.path();
            plain(&path)?;
            let additional = if fs::symlink_metadata(&path)?.is_dir() {
                if depth >= WORK_DEPTH {
                    return Err(invalid(
                        "chunk quota workspace exceeds existing depth bound",
                    ));
                }
                visit(
                    &path,
                    depth + 1,
                    device,
                    maximum,
                    deadline,
                    shared,
                    shared_limit,
                    entries,
                )?
            } else {
                let file = regular(&path)?;
                if single_cue_file(&file)?.0 != device {
                    return Err(invalid("chunk quota file changed device"));
                }
                file.metadata()?.len()
            };
            bytes = bytes
                .checked_add(additional)
                .filter(|n| *n <= maximum)
                .ok_or_else(|| invalid("actual chunk bytes exceed proven quota coverage"))?;
        }
        Ok(bytes)
    }
    let bytes = visit(
        work,
        0,
        output_device,
        maximum,
        deadline,
        shared,
        shared_limit,
        &mut 0,
    )?;
    counted.verify(&file, &path, output_device)?;
    if quota_prefix(&file)? != prefix
        || work_identity(&open_work(work)?)? != identity
        || work_identity(&work_file)? != identity
    {
        return Err(invalid(
            "chunk quota record/work changed while inventory was read",
        ));
    }
    time_left(Some(deadline))?;
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
            assert!(
                root.file_name()
                    .unwrap()
                    .to_string_lossy()
                    .starts_with("dubflow-chunk-capacity-")
            );
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
    fn unreserved_candidates_partial_and_chunk_named_orphans_are_all_charged() {
        let f = Fixture::new();
        let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
        let job = store.objects.root.file_name().unwrap();
        let parent = f.0.join(".dubflow-review-candidates").join(job);
        let unreserved = parent.join(format!("chunk-capacity-v1-{}", &scope()[7..]));
        fs::create_dir_all(&unreserved).unwrap();
        let partial = parent.join(".render-staging.partial");
        fs::create_dir(&partial).unwrap();
        for (path, bytes) in [
            (parent.join("B2-source.wav"), 17),
            (parent.join("old-orphan.pcm"), 19),
            (partial.join("render.mp4.partial"), 23),
            (unreserved.join("chunk.pcm"), 29),
        ] {
            fs::write(path, vec![7u8; bytes]).unwrap();
        }
        assert_eq!(store.used_bytes(deadline()).unwrap(), 17 + 19 + 23 + 29);
        assert_eq!(fs::read_dir(&store.objects.root).unwrap().count(), 0);
        assert_eq!(
            fs::read(unreserved.join("chunk.pcm")).unwrap(),
            vec![7u8; 29]
        );
        drop(store);
        let store = RetainedMixStore::open_existing(&f.0, "capacity-job", budget(88)).unwrap();
        assert!(
            store
                .accept_json_record(
                    "{\"new\":1}",
                    |_| panic!("candidate quota must refuse before allocation probe"),
                    deadline()
                )
                .is_err()
        );
        assert_eq!(store.used_bytes(deadline()).unwrap(), 88);
    }

    #[test]
    fn quota_reads_live_and_orphan_extents_once_without_acquiring_worker_authority() {
        let f = Fixture::new();
        let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
        let token = store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        let private = token.work_root().join("partial.pcm");
        fs::write(&private, vec![3u8; 1024]).unwrap();
        let extent = token.extent;
        let before = (
            owned_bytes(&token),
            fs::metadata(&token.path).unwrap().modified().unwrap(),
        );
        // On Windows the existing owner holds an exclusive byte lock. Quota
        // reads must remain possible without releasing/reacquiring that lock.
        assert_eq!(store.used_bytes(deadline()).unwrap(), extent);
        assert!(
            store
                .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
                .is_err()
        );
        assert_eq!(
            (
                owned_bytes(&token),
                fs::metadata(&token.path).unwrap().modified().unwrap()
            ),
            before
        );
        drop(token);
        assert_eq!(store.used_bytes(deadline()).unwrap(), extent);
        drop(store);
        // Another job can account the same valid coverage, but metadata gives
        // it no owned capacity token or permission to adopt the private files.
        let other = RetainedMixStore::new(&f.0, "other-job", budget(100000)).unwrap();
        assert_eq!(other.used_bytes(deadline()).unwrap(), extent);
        assert_eq!(fs::read(private).unwrap(), vec![3u8; 1024]);
    }

    #[test]
    fn quota_never_discounts_more_actual_bytes_than_the_native_extent_covers() {
        let f = Fixture::new();
        let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
        let token = store
            .reserve_chunk_capacity(&scope(), 64, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        let private = token.work_root().join("oversize.pcm");
        fs::write(&private, [7u8; 65]).unwrap();
        let before = owned_bytes(&token);
        assert!(store.used_bytes(deadline()).is_err());
        assert_eq!(owned_bytes(&token), before);
        assert_eq!(fs::read(private).unwrap(), [7u8; 65]);
    }

    fn rewrite_fixture_extent(path: &Path, prefix: &[u8], extent: u64) {
        let mut mode = fs::metadata(path).unwrap().permissions();
        mode.set_readonly(false);
        fs::set_permissions(path, mode).unwrap();
        let mut file = OpenOptions::new().write(true).open(path).unwrap();
        file.set_len(extent).unwrap();
        file.write_all(prefix).unwrap();
        file.sync_all().unwrap();
        drop(file);
        let mut mode = fs::metadata(path).unwrap().permissions();
        mode.set_readonly(true);
        fs::set_permissions(path, mode).unwrap();
    }

    #[test]
    fn quota_rejects_corrupt_foreign_unsealed_and_wrong_sized_extents_without_repair() {
        for case in [
            "length",
            "json",
            "extra-field",
            "job",
            "scope",
            "work-id",
            "short",
            "long",
            "unsealed",
        ] {
            let f = Fixture::new();
            let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
            let token = store
                .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
                .unwrap();
            let private = token.work_root().join("keep.pcm");
            fs::write(&private, [9u8; 17]).unwrap();
            let path = token.path.clone();
            let job = store.objects.root.file_name().unwrap().to_str().unwrap();
            let mut prefix = token.header.clone();
            let mut extent = token.extent;
            let identity = token.directory_identity;
            match case {
                "length" => prefix[..4].copy_from_slice(&((HEADER_LIMIT + 1) as u32).to_be_bytes()),
                "json" => prefix[4] = b'!',
                "extra-field" => {
                    let mut value: Value = serde_json::from_slice(&prefix[4..]).unwrap();
                    value["unknown_quota_grant"] = json!(true);
                    let mut raw = String::new();
                    super::super::super::review_document::canonical(&value, &mut raw).unwrap();
                    prefix = (raw.len() as u32).to_be_bytes().to_vec();
                    prefix.extend_from_slice(raw.as_bytes());
                    extent = 4096 + prefix.len() as u64;
                }
                "job" => prefix = header(&"b".repeat(64), &scope(), 4096, identity).unwrap(),
                "scope" => {
                    prefix =
                        header(job, &format!("sha256:{}", "c".repeat(64)), 4096, identity).unwrap()
                }
                "work-id" => {
                    prefix = header(job, &scope(), 4096, (identity.0, identity.1 ^ 1)).unwrap();
                    extent = 4096 + prefix.len() as u64;
                }
                "short" => extent -= 1,
                "long" => extent += 1,
                "unsealed" => (),
                _ => unreachable!(),
            }
            drop(token);
            rewrite_fixture_extent(&path, &prefix, extent);
            if case == "unsealed" {
                let mut mode = fs::metadata(&path).unwrap().permissions();
                mode.set_readonly(false);
                fs::set_permissions(&path, mode).unwrap();
            }
            let modified = fs::metadata(&path).unwrap().modified().unwrap();
            assert!(store.used_bytes(deadline()).is_err(), "{case}");
            assert_eq!(fs::metadata(&path).unwrap().len(), extent, "{case}");
            assert_eq!(
                fs::metadata(&path).unwrap().modified().unwrap(),
                modified,
                "{case}"
            );
            assert_eq!(fs::read(private).unwrap(), [9u8; 17], "{case}");
        }
    }

    #[test]
    fn missing_extent_path_counts_both_renamed_record_and_unreserved_orphans() {
        let f = Fixture::new();
        let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
        let token = store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        let work = token.work_root().to_path_buf();
        let extent = token.path.clone();
        let private = work.join("orphan.pcm");
        fs::write(&private, [1u8; 17]).unwrap();
        drop(token);
        let saved = extent.with_extension("saved");
        fs::rename(&extent, &saved).unwrap(); // Fresh owned fixture only, no cleanup/adoption.
        let saved_charge = fs::metadata(&saved).unwrap().len();
        assert_eq!(store.used_bytes(deadline()).unwrap(), saved_charge + 17);
        assert!(!extent.exists());
        assert_eq!(fs::read(private).unwrap(), [1u8; 17]);
        assert!(work.exists());
    }

    #[test]
    fn quota_refuses_replaced_workspace_or_counted_control_identity() {
        for replace_work in [true, false] {
            let f = Fixture::new();
            let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
            let token = store
                .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
                .unwrap();
            let work = token.work_root().to_path_buf();
            let path = token.path.clone();
            let job = store.objects.root.file_name().unwrap().to_str().unwrap();
            let original = regular(&path).unwrap();
            let counted = QuotaExtent::counted(&original).unwrap();
            drop(token);
            if replace_work {
                let prior = work.with_extension("prior");
                fs::rename(&work, &prior).unwrap();
                fs::create_dir(&work).unwrap();
                fs::write(work.join("foreign.pcm"), [8u8; 17]).unwrap();
                assert!(store.used_bytes(deadline()).is_err());
            } else {
                let saved = path.with_extension("prior");
                fs::rename(&path, &saved).unwrap();
                fs::copy(&saved, &path).unwrap();
                assert!(
                    quota_covered_bytes(
                        &store.namespace,
                        job,
                        &scope()[7..],
                        &work,
                        &counted,
                        deadline(),
                        &mut 0,
                        1000
                    )
                    .is_err()
                );
                assert!(saved.exists() && path.exists());
            }
        }
    }

    #[test]
    fn quota_refuses_multilink_control_or_private_files_and_keeps_both_links() {
        for link_control in [true, false] {
            let f = Fixture::new();
            let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
            let token = store
                .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
                .unwrap();
            let private = token.work_root().join("keep.pcm");
            fs::write(&private, [6u8; 17]).unwrap();
            let path = if link_control {
                token.path.clone()
            } else {
                private.clone()
            };
            let alias = path.with_extension("alias");
            drop(token);
            fs::hard_link(&path, &alias).unwrap();
            assert!(store.used_bytes(deadline()).is_err());
            assert!(path.exists() && alias.exists());
            assert_eq!(fs::read(private).unwrap(), [6u8; 17]);
        }
    }

    #[cfg(unix)]
    #[test]
    fn quota_refuses_symlink_private_files_without_reading_foreign_bytes() {
        let f = Fixture::new();
        let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
        let token = store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        let foreign = f.0.join("foreign.pcm");
        fs::write(&foreign, [4u8; 17]).unwrap();
        let link = token.work_root().join("link.pcm");
        std::os::unix::fs::symlink(&foreign, &link).unwrap();
        assert!(store.used_bytes(deadline()).is_err());
        assert_eq!(fs::read(foreign).unwrap(), [4u8; 17]);
        fs::remove_file(link).unwrap(); // Only the exact fresh fixture link.
    }

    #[test]
    fn quota_read_preserves_deadline_shared_entry_and_private_depth_limits() {
        let f = Fixture::new();
        let store = RetainedMixStore::new(&f.0, "capacity-job", budget(100000)).unwrap();
        let token = store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        let job = store.objects.root.file_name().unwrap().to_str().unwrap();
        for n in 0..8 {
            fs::write(token.work_root().join(n.to_string()), [3u8; 1]).unwrap();
        }
        let counted = QuotaExtent::counted(&regular(&token.path).unwrap()).unwrap();
        assert!(store.used_bytes(Instant::now()).is_err());
        let mut count = 0;
        assert!(
            quota_covered_bytes(
                &store.namespace,
                job,
                &scope()[7..],
                token.work_root(),
                &counted,
                deadline(),
                &mut count,
                2
            )
            .is_err()
        );
        assert_eq!(count, 3);
        let mut path = token.work_root().to_path_buf();
        for _ in 0..=WORK_DEPTH {
            path = path.join("nested");
            fs::create_dir(&path).unwrap();
        }
        assert!(store.used_bytes(deadline()).is_err());
        assert!(token.path.exists());
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
        assert!(
            other
                .accept_json_record(
                    "{\"new\":1}",
                    |_| panic!("quota must refuse before available probe"),
                    deadline()
                )
                .is_err()
        );
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
        assert!(
            store
                .check_chunk_capacity(
                    &token,
                    4097,
                    |_| panic!("private limit before OS probe"),
                    deadline(),
                    &mut || false
                )
                .is_err()
        );
        assert!(
            store
                .check_chunk_capacity(&token, 4096, |_| Ok(4195), deadline(), &mut || false)
                .is_err()
        );
        assert!(
            store
                .check_chunk_capacity(&token, 0, |_| Ok(u64::MAX), deadline(), &mut || true)
                .is_err()
        );
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
        assert!(
            store
                .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
                .is_err()
        );
        drop(token);
        let recovered = store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        assert!(recovered.recovered());
        assert_eq!(fs::metadata(&extent).unwrap().modified().unwrap(), before);
        assert!(
            store
                .release_chunk_capacity(recovered, deadline(), &mut || false)
                .is_err()
        );
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
        assert!(
            other
                .check_chunk_capacity(
                    &token,
                    0,
                    |_| panic!("foreign job before OS probe"),
                    deadline(),
                    &mut || false
                )
                .is_err()
        );
        drop(other);
        let store = RetainedMixStore::open_existing(&f.0, "capacity-job", budget(100000)).unwrap();
        drop(token);
        assert!(
            store
                .reserve_chunk_capacity(&scope(), 4097, |_| Ok(u64::MAX), deadline(), &mut || false)
                .is_err()
        );
        let token = store
            .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
            .unwrap();
        let mut mode = token.file.metadata().unwrap().permissions();
        mode.set_readonly(false);
        token.file.set_permissions(mode).unwrap();
        assert!(
            store
                .check_chunk_capacity(&token, 0, |_| Ok(u64::MAX), deadline(), &mut || false)
                .is_err()
        );
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
        assert!(
            store
                .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
                .is_err()
        );
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
        assert!(
            store
                .check_chunk_capacity(&token, 0, |_| Ok(u64::MAX), deadline(), &mut || false)
                .is_err()
        );
        #[cfg(windows)]
        {
            assert!(fs::rename(&token.path, token.path.with_extension("foreign")).is_err());
            assert!(
                fs::rename(
                    token.work_root(),
                    token.work_root().with_extension("foreign")
                )
                .is_err()
            );
            let work = token.work_root().to_path_buf();
            drop(token);
            fs::rename(&work, work.with_extension("prior")).unwrap();
            fs::create_dir(&work).unwrap();
            assert!(
                store
                    .reserve_chunk_capacity(
                        &scope(),
                        4096,
                        |_| Ok(u64::MAX),
                        deadline(),
                        &mut || false
                    )
                    .is_err()
            );
        }
        #[cfg(unix)]
        {
            fs::rename(token.work_root(), token.work_root().with_extension("prior")).unwrap();
            fs::create_dir(token.work_root()).unwrap();
            assert!(
                store
                    .check_chunk_capacity(&token, 0, |_| Ok(u64::MAX), deadline(), &mut || false)
                    .is_err()
            );
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
        assert!(
            store
                .reserve_chunk_capacity(&scope(), 4096, |_| Ok(u64::MAX), deadline(), &mut || false)
                .is_err()
        );
        assert!(!work.exists() && extent.exists());
        assert_eq!(store.used_bytes(deadline()).unwrap(), used);
    }
}
