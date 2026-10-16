//! One bounded owned clock process. Original metadata collect remains unchanged.
use super::*;
use std::thread::JoinHandle;

struct ClockProcess {
    process: Option<AdapterProcess>,
    readers: Vec<JoinHandle<()>>,
}
impl ClockProcess {
    fn finish(&mut self) -> SupervisorResult<()> {
        // Close the native tree before pipe joins, including inherited FFprobe
        // handles after cancellation or a refused parent observation.
        drop(self.process.take());
        let until = Instant::now() + Duration::from_secs(2);
        while self.readers.iter().any(|t| !t.is_finished()) && Instant::now() < until {
            std::thread::sleep(Duration::from_millis(5));
        }
        let mut joined = true;
        for thread in self.readers.drain(..) {
            if !thread.is_finished() {
                joined = false;
                drop(thread);
            } else if thread.join().is_err() {
                joined = false;
            }
        }
        if !joined {
            return Err(invalid("clock pipe readers did not join completely"));
        }
        Ok(())
    }
}
impl Drop for ClockProcess {
    fn drop(&mut self) {
        let _ = self.finish();
    }
}
pub(crate) fn collect_clock_worker(
    mut command: Command,
    deadline: Instant,
    limit: usize,
    observe: &mut impl FnMut(Instant) -> SupervisorResult<()>,
) -> SupervisorResult<String> {
    if limit == 0
        || limit > 8 * 1024 * 1024 + 16384
        || deadline
            .checked_duration_since(Instant::now())
            .is_none_or(|d| d.is_zero() || d > Duration::from_secs(180))
    {
        return Err(invalid(
            "clock process needs one bounded absolute deadline and byte limit",
        ));
    }
    observe(deadline)?;
    if Instant::now() >= deadline {
        return Err(invalid("clock pre-spawn observation exhausted deadline"));
    }
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let mut child = AdapterProcess {
        child: command.spawn()?,
        tree: None,
    };
    child.tree = Some(super::super::WorkerTree::attach(&child.child)?);
    let stdout = child.child.stdout.take().unwrap();
    let stderr = child.child.stderr.take().unwrap();
    let mut stdin = child.child.stdin.take().unwrap();
    let mut scope = ClockProcess {
        process: Some(child),
        readers: Vec::new(),
    };
    let (send, receive) = mpsc::sync_channel(4);
    for (error, cap, stream) in [
        (false, limit, Box::new(stdout) as Box<dyn Read + Send>),
        (true, 4096, Box::new(stderr) as Box<dyn Read + Send>),
    ] {
        let send = send.clone();
        let thread = std::thread::Builder::new()
            .name("review-clock-pipe".into())
            .spawn(move || {
                let mut bytes = Vec::new();
                let result = stream
                    .take(cap as u64 + 1)
                    .read_to_end(&mut bytes)
                    .map(|_| bytes);
                let _ = send.send((error, cap, result));
            })?;
        scope.readers.push(thread);
    }
    drop(send);
    observe(deadline)?;
    if Instant::now() >= deadline {
        return Err(invalid(
            "clock containment exhausted deadline before byte gate",
        ));
    }
    stdin.write_all(&[1])?;
    drop(stdin);
    let (mut out, mut diagnostic) = (None, None);
    let result = loop {
        observe(deadline)?;
        if Instant::now() >= deadline {
            break Err(invalid("clock process exceeded absolute native deadline"));
        }
        match receive.recv_timeout(Duration::from_millis(20)) {
            Ok((is_error, cap, value)) => {
                let bytes = value?;
                if bytes.len() > cap {
                    break Err(invalid("clock process exceeded bounded pipe inventory"));
                }
                if is_error {
                    diagnostic = Some(bytes);
                } else {
                    out = Some(bytes);
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) | Err(mpsc::RecvTimeoutError::Disconnected) => {}
        }
        if let Some(status) = scope.process.as_mut().unwrap().child.try_wait()? {
            if let (Some(output), Some(errors)) = (&out, &diagnostic) {
                if !status.success() || !errors.is_empty() {
                    break Err(invalid(&format!(
                        "clock worker failed or emitted diagnostics: {}",
                        String::from_utf8_lossy(errors)
                    )));
                }
                break String::from_utf8(output.clone())
                    .map_err(|_| invalid("clock worker response is not UTF8"));
            }
        }
    };
    scope.finish()?;
    observe(deadline)?;
    if Instant::now() >= deadline {
        return Err(invalid("clock completion exceeded native deadline"));
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    fn child(mode: &str) -> Command {
        let mut c = Command::new(std::env::current_exe().unwrap());
        c.args([
            "--exact",
            "review_seed::clock_collect::tests::clock_child_fixture",
            "--ignored",
            "--nocapture",
            "--quiet",
        ]);
        c.env("DUBFLOW_CLOCK_CHILD_FIXTURE", mode);
        c
    }
    #[test]
    #[ignore]
    fn clock_child_fixture() {
        assert_eq!(
            std::env::var("DUBFLOW_CLOCK_CHILD_FIXTURE")
                .ok()
                .as_deref()
                .is_some(),
            true
        );
        let mut gate = [0u8; 1];
        std::io::stdin().read_exact(&mut gate).unwrap();
        assert_eq!(gate, [1]);
        match std::env::var("DUBFLOW_CLOCK_CHILD_FIXTURE")
            .unwrap()
            .as_str()
        {
            "ok" => {
                std::io::stdout()
                    .write_all(b"clock-byte-gate-observed\n")
                    .unwrap();
            }
            "flood" => {
                std::io::stdout().write_all(&vec![b'x'; 8192]).unwrap();
            }
            "sleep" => std::thread::sleep(Duration::from_secs(10)),
            _ => panic!("unknown owned fixture mode"),
        }
    }
    #[test]
    fn native_clock_collector_contains_gate_and_refuses_bounded_pipe_flood() {
        let until = Instant::now() + Duration::from_secs(10);
        let raw = collect_clock_worker(child("ok"), until, 8192, &mut |_| Ok(())).unwrap();
        assert!(raw.contains("clock-byte-gate-observed"));
        assert!(collect_clock_worker(
            child("flood"),
            Instant::now() + Duration::from_secs(10),
            256,
            &mut |_| Ok(())
        )
        .is_err());
        assert!(collect_clock_worker(
            child("ok"),
            Instant::now() + Duration::from_secs(181),
            8192,
            &mut |_| Ok(())
        )
        .is_err());
    }
    #[test]
    fn native_clock_collector_cancellation_and_absolute_deadline_never_return_partial_output() {
        let start = Instant::now();
        assert!(collect_clock_worker(
            child("sleep"),
            start + Duration::from_secs(10),
            8192,
            &mut |_| {
                if start.elapsed() > Duration::from_millis(40) {
                    Err(invalid("owned cancel fixture"))
                } else {
                    Ok(())
                }
            }
        )
        .is_err());
        assert!(start.elapsed() < Duration::from_secs(5));
        let start = Instant::now();
        assert!(collect_clock_worker(
            child("sleep"),
            start + Duration::from_millis(150),
            8192,
            &mut |_| Ok(())
        )
        .is_err());
        assert!(start.elapsed() < Duration::from_secs(5));
    }
}
