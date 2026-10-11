//! OS lifetime ownership for the separate source database, acquired before open.
//! The retained file is diagnostic metadata; its existence never means ownership.
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::Path;

pub(super) struct SourceOwner {
    _file: File,
}

pub(super) fn reject_links(path: &Path) -> io::Result<()> {
    if !path.is_absolute() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "absolute source path required",
        ));
    }
    for component in path.ancestors() {
        match fs::symlink_metadata(component) {
            Ok(metadata) => {
                let mut linked = metadata.file_type().is_symlink();
                #[cfg(windows)]
                {
                    use std::os::windows::fs::MetadataExt;
                    linked |= metadata.file_attributes() & 0x400 != 0;
                }
                if linked {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "linked source path refused",
                    ));
                }
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => (),
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

impl SourceOwner {
    pub(super) fn acquire(path: &Path, started_ms: u64) -> io::Result<Self> {
        reject_links(path)?;
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true);
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            // No read/write/delete sharing; an inherited or stale text file is
            // not a process probe. Windows releases this claim on process death.
            options.share_mode(0).custom_flags(0x00200000); // OPEN_REPARSE_POINT
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(path)?;
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            extern "C" {
                fn flock(fd: i32, operation: i32) -> i32;
            }
            // LOCK_EX | LOCK_NB. Closing the descriptor releases ownership.
            if unsafe { flock(file.as_raw_fd(), 2 | 4) } != 0 {
                return Err(io::Error::last_os_error());
            }
        }
        reject_links(path)?;
        file.set_len(0)?;
        write!(file, "{{\"schema_version\":1,\"pid\":{},\"started_at_ms\":{},\"scope\":\"source-database\"}}\n", std::process::id(), started_ms)?;
        file.sync_all()?;
        Ok(Self { _file: file })
    }
}
