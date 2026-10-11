//! Own the Windows worker subtree before its initial command starts work.

#[cfg(not(windows))]
pub(super) struct WorkerTree;

#[cfg(not(windows))]
impl WorkerTree {
    pub(super) fn attach(_child: &std::process::Child) -> std::io::Result<Self> {
        Ok(Self)
    }
}

#[cfg(windows)]
pub(super) use windows::WorkerTree;

#[cfg(windows)]
mod windows {
    use std::ffi::c_void;
    use std::io;
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;
    use std::ptr;

    type Handle = *mut c_void;

    #[repr(C)]
    #[derive(Default)]
    struct BasicLimits {
        process_time: i64,
        job_time: i64,
        flags: u32,
        minimum_working_set: usize,
        maximum_working_set: usize,
        active_processes: u32,
        affinity: usize,
        priority: u32,
        scheduling: u32,
    }

    #[repr(C)]
    #[derive(Default)]
    struct IoCounters {
        read_operations: u64,
        write_operations: u64,
        other_operations: u64,
        read_bytes: u64,
        write_bytes: u64,
        other_bytes: u64,
    }

    #[repr(C)]
    #[derive(Default)]
    struct ExtendedLimits {
        basic: BasicLimits,
        io: IoCounters,
        process_memory: usize,
        job_memory: usize,
        peak_process_memory: usize,
        peak_job_memory: usize,
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

    /// The anonymous handle is private to the supervisor and never inherited.
    /// Closing it, including on supervisor death, terminates its whole subtree.
    pub(crate) struct WorkerTree {
        handle: Handle,
    }

    impl WorkerTree {
        pub(crate) fn attach(child: &Child) -> io::Result<Self> {
            // Null SECURITY_ATTRIBUTES makes the new handle non-inheritable;
            // null name prevents another process reopening this job by name.
            let handle = unsafe { CreateJobObjectW(ptr::null(), ptr::null()) };
            if handle.is_null() {
                return Err(io::Error::last_os_error());
            }
            let tree = Self { handle };
            let mut limits = ExtendedLimits::default();
            limits.basic.flags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
                                         // Both ABI structs and the Child's process handle stay live for
                                         // these calls. Assignment occurs before the run command is sent.
            if unsafe {
                SetInformationJobObject(
                    tree.handle,
                    9, // JobObjectExtendedLimitInformation
                    ptr::from_ref(&limits).cast(),
                    std::mem::size_of::<ExtendedLimits>() as u32,
                )
            } == 0
            {
                return Err(io::Error::last_os_error());
            }
            if unsafe { AssignProcessToJobObject(tree.handle, child.as_raw_handle()) } == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(tree)
        }
    }

    impl Drop for WorkerTree {
        fn drop(&mut self) {
            // This sole owned, non-inherited handle cannot be shared or cloned.
            unsafe { CloseHandle(self.handle) };
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::io::{BufRead, BufReader, Write};
        use std::os::windows::process::CommandExt;
        use std::process::{Command, Stdio};

        unsafe extern "system" {
            fn OpenProcess(access: u32, inherit: i32, process_id: u32) -> Handle;
            fn WaitForSingleObject(handle: Handle, milliseconds: u32) -> u32;
        }

        struct ProcessObservation(Handle);

        impl ProcessObservation {
            fn open(pid: u32) -> Self {
                let handle = unsafe { OpenProcess(0x0010_0000, 0, pid) }; // SYNCHRONIZE
                assert!(
                    !handle.is_null(),
                    "capture the actual live process before termination"
                );
                Self(handle)
            }

            fn stopped(&self) {
                assert_eq!(unsafe { WaitForSingleObject(self.0, 3000) }, 0);
            }

            fn running(&self) {
                assert_eq!(unsafe { WaitForSingleObject(self.0, 0) }, 258); // WAIT_TIMEOUT
            }
        }

        impl Drop for ProcessObservation {
            fn drop(&mut self) {
                unsafe { CloseHandle(self.0) };
            }
        }

        struct CleanupChild(Child);

        impl Drop for CleanupChild {
            fn drop(&mut self) {
                let _ = self.0.kill();
                let _ = self.0.wait();
            }
        }

        fn fixture(mode: &str) -> Command {
            let mut command = Command::new(std::env::current_exe().unwrap());
            command
                .args([
                    "--exact",
                    "worker_tree::windows::tests::fixture_process",
                    "--nocapture",
                ])
                .env("DUBFLOW_WORKER_TREE_TEST_MODE", mode)
                .creation_flags(0x0800_0000) // CREATE_NO_WINDOW
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::null());
            command
        }

        fn ready(child: &mut Child) -> Vec<u32> {
            let reader = BufReader::new(child.stdout.take().unwrap());
            let (tx, rx) = std::sync::mpsc::channel();
            std::thread::spawn(move || {
                for line in reader.lines() {
                    let Ok(line) = line else { break };
                    if let Some((_, ids)) = line.split_once("worker-tree-ready:") {
                        let ids: Vec<u32> = ids
                            .trim()
                            .split(',')
                            .map(|value| value.parse().unwrap())
                            .collect();
                        let _ = tx.send(ids);
                        return;
                    }
                }
            });
            rx.recv_timeout(std::time::Duration::from_secs(10))
                .expect("fixture must report its live process tree within a bounded wait")
        }

        fn contained_worker() -> (CleanupChild, WorkerTree, u32) {
            let mut worker = CleanupChild(fixture("worker").spawn().unwrap());
            let tree = WorkerTree::attach(&worker.0).unwrap();
            worker
                .0
                .stdin
                .as_mut()
                .unwrap()
                .write_all(b"start\n")
                .unwrap();
            let ids = ready(&mut worker.0);
            assert_eq!(ids.len(), 1);
            (worker, tree, ids[0])
        }

        #[test]
        fn fixture_process() {
            let Ok(mode) = std::env::var("DUBFLOW_WORKER_TREE_TEST_MODE") else {
                return;
            };
            match mode.as_str() {
                "grandchild" => {
                    println!("worker-tree-ready:{}", std::process::id());
                    std::io::stdout().flush().unwrap();
                }
                "worker" => {
                    let mut line = String::new();
                    std::io::stdin().read_line(&mut line).unwrap();
                    assert_eq!(line, "start\n"); // Expensive work waits for native containment.
                    let mut grandchild = CleanupChild(fixture("grandchild").spawn().unwrap());
                    let ids = ready(&mut grandchild.0);
                    println!("worker-tree-ready:{}", ids[0]);
                    std::io::stdout().flush().unwrap();
                    loop {
                        std::thread::park();
                    }
                }
                "owner" => {
                    let (worker, _tree, grandchild) = contained_worker();
                    println!("worker-tree-ready:{},{}", worker.0.id(), grandchild);
                    std::io::stdout().flush().unwrap();
                    loop {
                        std::thread::park();
                    }
                }
                _ => panic!("unknown private fixture mode"),
            }
            loop {
                std::thread::park();
            }
        }

        #[test]
        fn windows_drop_retires_worker_and_grandchild_preserving_neighbor() {
            let mut neighbor = CleanupChild(fixture("grandchild").spawn().unwrap());
            ready(&mut neighbor.0);
            let (mut worker, tree, grandchild) = contained_worker();
            let worker_handle = ProcessObservation::open(worker.0.id());
            let grandchild_handle = ProcessObservation::open(grandchild);
            worker_handle.running();
            grandchild_handle.running();
            drop(tree);
            worker_handle.stopped();
            grandchild_handle.stopped();
            assert!(neighbor.0.try_wait().unwrap().is_none());
            // Job closure can yield exit zero. The retained kernel handles,
            // observed live before drop and terminal after, prove retirement.
            worker.0.wait().unwrap();
        }

        #[test]
        fn windows_hard_kill_owner_retires_worker_and_grandchild() {
            let mut owner = CleanupChild(fixture("owner").spawn().unwrap());
            let ids = ready(&mut owner.0);
            assert_eq!(ids.len(), 2);
            let worker_handle = ProcessObservation::open(ids[0]);
            let grandchild_handle = ProcessObservation::open(ids[1]);
            worker_handle.running();
            grandchild_handle.running();
            owner.0.kill().unwrap(); // Only the owner; no taskkill /T or injected tree cleanup.
            assert!(!owner.0.wait().unwrap().success());
            worker_handle.stopped();
            grandchild_handle.stopped();
        }

        #[test]
        fn windows_job_limits_match_the_x64_abi() {
            assert_eq!(std::mem::size_of::<BasicLimits>(), 64);
            assert_eq!(std::mem::size_of::<IoCounters>(), 48);
            assert_eq!(std::mem::size_of::<ExtendedLimits>(), 144);
        }
    }
}
