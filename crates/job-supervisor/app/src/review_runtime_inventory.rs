//! Optional Windows proof for one fresh inventory over two identical bindings.
//! No admission result is cached. Returned GUID paths come only from held handles.
use super::{invalid, plain, SupervisorResult};
use std::ffi::{c_void, OsString};
use std::fs::{self, File, OpenOptions};
use std::os::windows::ffi::OsStringExt;
use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
use std::os::windows::io::AsRawHandle;
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, Instant};

const MAX_ANCESTORS: usize = 64;
const MAX_WIDE_PATH: usize = 32768;
const PROBE_LIMIT: Duration = Duration::from_secs(1);

#[repr(C)]
struct FileIdInfo {
    volume_serial: u64,
    file_id: [u8; 16],
}
#[link(name = "kernel32")]
unsafe extern "system" {
    fn GetFileInformationByHandleEx(
        file: *mut c_void,
        class: u32,
        information: *mut c_void,
        size: u32,
    ) -> i32;
    fn GetFinalPathNameByHandleW(file: *mut c_void, path: *mut u16, length: u32, flags: u32)
        -> u32;
}
fn check(deadline: Instant) -> SupervisorResult<()> {
    if Instant::now() >= deadline {
        return Err(invalid(
            "runtime directory identity probe deadline exceeded",
        ));
    }
    Ok(())
}
fn open_directory(path: &Path, deadline: Instant) -> SupervisorResult<File> {
    check(deadline)?;
    plain(path)?;
    if path.components().any(|c| matches!(c, Component::ParentDir))
        || !fs::symlink_metadata(path)?.is_dir()
    {
        return Err(invalid("runtime identity needs a plain directory"));
    }
    // Metadata access only. No write/delete access or share is requested.
    // Denying write/delete sharing holds directory/ancestor replacement while
    // the unchanged file walker independently checks every imported path.
    let file = OpenOptions::new()
        .read(true)
        .access_mode(0x80) // FILE_READ_ATTRIBUTES.
        .share_mode(0x1) // FILE_SHARE_READ, without WRITE or DELETE.
        .custom_flags(0x0220_0000) // BACKUP_SEMANTICS | OPEN_REPARSE_POINT.
        .open(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_dir() || metadata.file_attributes() & 0x400 != 0 {
        return Err(invalid("runtime directory handle is not plain"));
    }
    plain(path)?;
    check(deadline)?;
    Ok(file)
}
#[derive(Clone, PartialEq, Eq)]
struct DirectoryIdentity {
    volume_serial: u64,
    file_id: [u8; 16],
    guid_path: PathBuf,
}
fn identity(file: &File, deadline: Instant) -> SupervisorResult<DirectoryIdentity> {
    check(deadline)?;
    let metadata = file.metadata()?;
    if !metadata.is_dir() || metadata.file_attributes() & 0x400 != 0 {
        return Err(invalid("runtime directory identity lost its plain handle"));
    }
    let mut id: FileIdInfo = unsafe { std::mem::zeroed() };
    // FileIdInfo=18, FILE_ID_INFO is a 64-bit volume number plus a128-bit ID.
    // https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_id_info
    if unsafe {
        GetFileInformationByHandleEx(
            file.as_raw_handle(),
            18,
            (&mut id as *mut FileIdInfo).cast(),
            std::mem::size_of::<FileIdInfo>() as u32,
        )
    } == 0
    {
        return Err(std::io::Error::last_os_error().into());
    }
    if id.volume_serial == 0 || id.file_id.iter().all(|b| *b == 0) {
        return Err(invalid("runtime directory native identity is unavailable"));
    }
    let mut wide = vec![0u16; MAX_WIDE_PATH + 1];
    // VOLUME_NAME_GUID, FILE_NAME_NORMALIZED. This name is an observation from
    // this no-reparse handle, never a normalized caller hint or stored job root.
    let count = unsafe {
        GetFinalPathNameByHandleW(
            file.as_raw_handle(),
            wide.as_mut_ptr(),
            wide.len() as u32,
            0x1,
        )
    } as usize;
    if count == 0 || count > MAX_WIDE_PATH || wide[..count].contains(&0) {
        return Err(invalid(
            "runtime handle GUID path is unavailable or oversized",
        ));
    }
    wide.truncate(count);
    let text = String::from_utf16(&wide)
        .map_err(|_| invalid("runtime handle GUID path is not Unicode"))?;
    let bytes = text.as_bytes();
    if bytes.len() < 49
        || !text.starts_with(r"\\?\Volume{")
        || bytes[47] != b'}'
        || bytes[48] != b'\\'
        || !bytes[11..47].iter().enumerate().all(|(i, b)| {
            if matches!(i, 8 | 13 | 18 | 23) {
                *b == b'-'
            } else {
                b.is_ascii_hexdigit()
            }
        })
    {
        return Err(invalid("runtime handle did not return a volume GUID path"));
    }
    let guid_path = PathBuf::from(OsString::from_wide(&wide));
    if !guid_path.is_absolute() {
        return Err(invalid("runtime handle GUID path is not absolute"));
    }
    check(deadline)?;
    Ok(DirectoryIdentity {
        volume_serial: id.volume_serial,
        file_id: id.file_id,
        guid_path,
    })
}
fn hold_ancestors(root: &Path, deadline: Instant) -> SupervisorResult<Vec<File>> {
    if !root.is_absolute() {
        return Err(invalid("runtime root is not absolute"));
    }
    let paths: Vec<_> = root.ancestors().take(MAX_ANCESTORS + 1).collect();
    if paths.is_empty() || paths.len() > MAX_ANCESTORS {
        return Err(invalid("runtime root identity exceeds its handle bound"));
    }
    let mut held = Vec::with_capacity(paths.len());
    for path in paths.into_iter().rev() {
        held.push(open_directory(path, deadline)?);
    }
    Ok(held)
}
pub(super) struct SharedDirectoryFence {
    left_path: PathBuf,
    right_path: PathBuf,
    expected: DirectoryIdentity,
    left: Vec<File>,
    right: Vec<File>,
}
impl SharedDirectoryFence {
    pub(super) fn canonical_root(&self) -> &Path {
        &self.expected.guid_path
    }
    pub(super) fn verify(&self) -> SupervisorResult<()> {
        let deadline = Instant::now() + PROBE_LIMIT;
        for (path, held) in [
            (&self.left_path, self.left.last().unwrap()),
            (&self.right_path, self.right.last().unwrap()),
        ] {
            let fresh = open_directory(path, deadline)?;
            if identity(held, deadline)? != self.expected
                || identity(&fresh, deadline)? != self.expected
            {
                return Err(invalid(
                    "runtime directory binding changed during its fence",
                ));
            }
        }
        Ok(())
    }
}
fn prove_shared(left: &Path, right: &Path) -> SupervisorResult<SharedDirectoryFence> {
    let deadline = Instant::now() + PROBE_LIMIT;
    let left_held = hold_ancestors(left, deadline)?;
    let right_held = hold_ancestors(right, deadline)?;
    let expected = identity(left_held.last().unwrap(), deadline)?;
    if identity(right_held.last().unwrap(), deadline)? != expected {
        return Err(invalid("runtime roots name different physical directories"));
    }
    let result = SharedDirectoryFence {
        left_path: left.into(),
        right_path: right.into(),
        expected,
        left: left_held,
        right: right_held,
    };
    result.verify()?;
    Ok(result)
}
/// An unavailable proof preserves the existing two-walk path. Once a positive
/// proof is selected, any before/after revalidation error refuses that operation.
pub(super) fn try_shared(left: &Path, right: &Path) -> Option<SharedDirectoryFence> {
    prove_shared(left, right).ok()
}
