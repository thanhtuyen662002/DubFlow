//! OS ownership is established before any service code executes.
use std::io;
use std::process::{Child, Command};

pub(super) struct OwnedChild {
    pub child: Child,
    tree: platform::Tree,
}
impl OwnedChild {
    pub fn spawn(command: &mut Command) -> io::Result<Self> {
        platform::configure(command);
        let mut child = command.spawn()?;
        let tree = match platform::Tree::attach(&child) {
            Ok(tree) => tree,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
        };
        let owned = Self { child, tree };
        owned.tree.resume(&owned.child)?;
        Ok(owned)
    }
    pub fn terminate(&mut self) {
        self.tree.terminate();
        let _ = self.child.kill();
    }
}
impl Drop for OwnedChild {
    fn drop(&mut self) {
        self.terminate();
        // Nonblocking reap; request() separately observes bounded retirement.
        let _ = self.child.try_wait();
    }
}

#[cfg(unix)]
mod platform {
    use super::*;
    use std::os::unix::process::CommandExt;
    unsafe extern "C" {
        fn kill(pid: i32, signal: i32) -> i32;
    }
    pub fn configure(command: &mut Command) {
        command.process_group(0);
    }
    pub struct Tree(i32);
    impl Tree {
        pub fn attach(child: &Child) -> io::Result<Self> {
            Ok(Self(child.id() as i32))
        }
        pub fn resume(&self, _: &Child) -> io::Result<()> {
            Ok(())
        }
        pub fn terminate(&mut self) {
            if self.0 != 0 {
                unsafe {
                    kill(-self.0, 9);
                }
                self.0 = 0;
            }
        }
    }
}

#[cfg(windows)]
mod platform {
    use super::*;
    use std::ffi::c_void;
    use std::os::windows::{io::AsRawHandle, process::CommandExt};
    type Handle = *mut c_void;
    #[repr(C)]
    #[derive(Default)]
    struct BasicLimits {
        process_time: i64,
        job_time: i64,
        flags: u32,
        min_ws: usize,
        max_ws: usize,
        active: u32,
        affinity: usize,
        priority: u32,
        scheduling: u32,
    }
    #[repr(C)]
    #[derive(Default)]
    struct ExtendedLimits {
        basic: BasicLimits,
        io: [u64; 6],
        process_memory: usize,
        job_memory: usize,
        peak_process: usize,
        peak_job: usize,
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn CreateJobObjectW(attributes: *const c_void, name: *const u16) -> Handle;
        fn SetInformationJobObject(
            job: Handle,
            class: i32,
            info: *const c_void,
            length: u32,
        ) -> i32;
        fn AssignProcessToJobObject(job: Handle, process: Handle) -> i32;
        fn CloseHandle(handle: Handle) -> i32;
    }
    #[link(name = "ntdll")]
    unsafe extern "system" {
        fn NtResumeProcess(process: Handle) -> i32;
    }
    pub fn configure(command: &mut Command) {
        command.creation_flags(0x0800_0004); // CREATE_NO_WINDOW | CREATE_SUSPENDED
    }
    pub struct Tree(Handle);
    impl Tree {
        pub fn attach(child: &Child) -> io::Result<Self> {
            let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
            if handle.is_null() {
                return Err(io::Error::last_os_error());
            }
            let tree = Self(handle);
            let mut limits = ExtendedLimits::default();
            limits.basic.flags = 0x2000; // KILL_ON_JOB_CLOSE; no breakaway/inheritance
            if unsafe {
                SetInformationJobObject(
                    handle,
                    9,
                    std::ptr::from_ref(&limits).cast(),
                    std::mem::size_of::<ExtendedLimits>() as u32,
                )
            } == 0
                || unsafe { AssignProcessToJobObject(handle, child.as_raw_handle()) } == 0
            {
                return Err(io::Error::last_os_error());
            }
            Ok(tree)
        }
        pub fn resume(&self, child: &Child) -> io::Result<()> {
            if unsafe { NtResumeProcess(child.as_raw_handle()) } < 0 {
                return Err(io::Error::other("resume failed"));
            }
            Ok(())
        }
        pub fn terminate(&mut self) {
            if !self.0.is_null() {
                unsafe {
                    CloseHandle(self.0);
                }
                self.0 = std::ptr::null_mut();
            }
        }
    }
    impl Drop for Tree {
        fn drop(&mut self) {
            self.terminate();
        }
    }
}
