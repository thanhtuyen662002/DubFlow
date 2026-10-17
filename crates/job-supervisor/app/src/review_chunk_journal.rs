//! Immutable per-plan ordinal markers under the existing output job store.
//! Native source/current-plan/locator semantics are checked by the v2 caller.
//! No mutable head, DATA fallback, SQLite stage write or old-object eviction.
use super::*;
use std::collections::BTreeMap;

const MAX_JOURNAL_BYTES: u64 = 16 * 1024 * 1024;
fn prefix(plan: &str) -> SupervisorResult<String> {
    if !valid_hash(plan) {
        return Err(invalid("chunk journal needs a native full plan hash"));
    }
    Ok(format!(".chunk-checkpoint-v2-{}-", &plan[7..]))
}
fn ordinal_name(prefix: &str, ordinal: u16) -> SupervisorResult<String> {
    if !(1..=4096).contains(&ordinal) {
        return Err(invalid("chunk journal ordinal exceeds its bound"));
    }
    Ok(format!("{prefix}{ordinal:04}.json"))
}
fn observe(deadline: Instant, cancelled: &mut impl FnMut() -> bool) -> SupervisorResult<()> {
    if cancelled() {
        return Err(invalid("chunk journal observation cancelled"));
    }
    time_left(Some(deadline))
}
fn sealed_marker(path: &Path, deadline: Instant) -> SupervisorResult<String> {
    time_left(Some(deadline))?;
    let mut file = regular(path)?;
    let before = file.metadata()?;
    let identity = single_cue_file(&file)?;
    if !before.permissions().readonly()
        || !(1..=MAX_RETAINED_METADATA_BYTES as u64).contains(&before.len())
    {
        return Err(invalid(
            "chunk checkpoint marker is unsealed or exceeds its record bound",
        ));
    }
    let mut bytes = Vec::new();
    (&mut file)
        .take(MAX_RETAINED_METADATA_BYTES as u64 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 != before.len() {
        return Err(invalid("chunk checkpoint marker size changed"));
    }
    let digest = format!("sha256:{:x}", Sha256::digest(&bytes));
    let raw = read_json_file(&mut file, &digest, before.len())?;
    let after = regular(path)?;
    if single_cue_file(&after)? != identity
        || single_cue_file(&file)? != identity
        || after.metadata()?.modified()? != before.modified()?
        || after.metadata()?.len() != before.len()
        || !after.metadata()?.permissions().readonly()
    {
        return Err(invalid("chunk checkpoint marker identity/seal changed"));
    }
    time_left(Some(deadline))?;
    Ok(raw)
}

impl RetainedMixStore {
    fn checkpoint_inventory(
        &self,
        plan: &str,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<(BTreeMap<u16, PathBuf>, u64)> {
        let start = prefix(plan)?;
        plain(&self.objects.root)?;
        let mut found = BTreeMap::new();
        let mut count = 0usize;
        let mut bytes = 0u64;
        for entry in fs::read_dir(&self.objects.root)? {
            observe(deadline, cancelled)?;
            count += 1;
            if count > self.inventory_limit {
                return Err(invalid("chunk journal scan exceeds shared inventory bound"));
            }
            let entry = entry?;
            let name = entry.file_name();
            let name = name
                .to_str()
                .ok_or_else(|| invalid("chunk journal namespace contains nonUTF8 filename"))?;
            let Some(suffix) = name.strip_prefix(&start) else {
                continue;
            };
            let number = suffix
                .strip_suffix(".json")
                .and_then(|s| s.parse::<u16>().ok())
                .ok_or_else(|| invalid("chunk journal marker name is not a native ordinal"))?;
            if ordinal_name(&start, number)? != name {
                return Err(invalid("chunk journal marker ordinal is noncanonical"));
            }
            let path = entry.path();
            let file = regular(&path)?;
            let metadata = file.metadata()?;
            single_cue_file(&file)?;
            if !metadata.permissions().readonly()
                || !(1..=MAX_RETAINED_METADATA_BYTES as u64).contains(&metadata.len())
            {
                return Err(invalid("chunk journal marker is unsealed or oversized"));
            }
            bytes = bytes
                .checked_add(metadata.len())
                .filter(|n| *n <= MAX_JOURNAL_BYTES)
                .ok_or_else(|| invalid("chunk journal aggregate metadata exceeds its bound"))?;
            if found.insert(number, path).is_some() || found.len() > 4096 {
                return Err(invalid(
                    "chunk journal repeats or exceeds ordinal inventory",
                ));
            }
        }
        for (expected, actual) in (1u16..).zip(found.keys()) {
            if expected != *actual {
                return Err(invalid(
                    "chunk journal lost a completed marker; explicit recovery required",
                ));
            }
        }
        observe(deadline, cancelled)?;
        Ok((found, bytes))
    }

    /// Must run inside this store's existing output-wide writer lease. Every
    /// old/undo/orphan/other-job marker byte is counted by the normal quota walk.
    /// Exact collisions reuse unchanged bytes; changed collisions never repair.
    pub(crate) fn publish_chunk_checkpoint(
        &self,
        plan: &str,
        ordinal: u16,
        raw: &str,
        mut available: impl FnMut(&Path) -> SupervisorResult<u64>,
        deadline: Instant,
    ) -> SupervisorResult<String> {
        time_left(Some(deadline))?;
        let start = prefix(plan)?;
        let name = ordinal_name(&start, ordinal)?;
        if raw.is_empty() || raw.len() > MAX_RETAINED_METADATA_BYTES {
            return Err(invalid("chunk checkpoint marker exceeds small JSON bound"));
        }
        json_syntax(raw)?;
        let path = self.objects.root.join(name);
        let (found, bytes) = self.checkpoint_inventory(plan, deadline, &mut || false)?;
        match fs::symlink_metadata(&path) {
            Ok(_) => {
                let old = sealed_marker(&path, deadline)?;
                if old != raw {
                    return Err(invalid(
                        "chunk checkpoint marker collision changed bytes; never overwrite",
                    ));
                }
                return Ok(old);
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            Err(e) => return Err(e.into()),
        }
        if usize::from(ordinal) != found.len() + 1 {
            return Err(invalid(
                "chunk checkpoint marker cannot skip the completed prefix",
            ));
        }
        if bytes
            .checked_add(raw.len() as u64)
            .is_none_or(|n| n > MAX_JOURNAL_BYTES)
        {
            return Err(invalid(
                "chunk journal exceeds aggregate metadata reservation",
            ));
        }
        let used = self.used_bytes(deadline)?;
        if used
            .checked_add(raw.len() as u64)
            .is_none_or(|n| n > self.budget.maximum_bytes)
        {
            return Err(invalid("chunk marker exceeds shared Review quota"));
        }
        let reserve = |remaining: u64| {
            remaining
                .checked_add(self.budget.minimum_free_bytes)
                .ok_or_else(|| invalid("chunk marker reserve overflow"))
        };
        if available(&self.namespace)? < reserve(raw.len() as u64)? {
            return Err(invalid("chunk marker lacks fresh caller space"));
        }
        let (mut file, private) = self.objects.private_copy()?;
        let mut copied = 0u64;
        for block in raw.as_bytes().chunks(BUFFER_BYTES) {
            time_left(Some(deadline))?;
            if available(&self.namespace)? < reserve(raw.len() as u64 - copied)? {
                return Err(invalid("chunk marker caller space changed while writing"));
            }
            file.write_all(block)?;
            copied += block.len() as u64;
        }
        file.sync_all()?;
        let digest = format!("sha256:{:x}", Sha256::digest(raw.as_bytes()));
        read_json_file(&mut file, &digest, raw.len() as u64)?;
        if available(&self.namespace)? < self.budget.minimum_free_bytes {
            return Err(invalid("chunk marker reserve changed before publication"));
        }
        time_left(Some(deadline))?;
        drop(file);
        self.objects.publish(&private, &path)?;
        drop(private);
        let saved = sealed_marker(&path, deadline)?;
        if saved != raw {
            return Err(invalid("chunk marker changed at exclusive publication"));
        }
        Ok(saved)
    }

    /// Bounded immutable marker recovery, without trusting a filename as source
    /// or locator authority. The native caller must admit all returned packets
    /// and independently reopen their output index nodes before reuse.
    pub(crate) fn read_chunk_checkpoints(
        &self,
        plan: &str,
        deadline: Instant,
        cancelled: &mut impl FnMut() -> bool,
    ) -> SupervisorResult<Vec<(u16, String)>> {
        let (found, _) = self.checkpoint_inventory(plan, deadline, cancelled)?;
        let mut records = Vec::new();
        for (ordinal, path) in found {
            observe(deadline, cancelled)?;
            records.push((ordinal, sealed_marker(&path, deadline)?));
        }
        observe(deadline, cancelled)?;
        Ok(records)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, SystemTime, UNIX_EPOCH};
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let parent = std::env::temp_dir();
            let path = parent.join(format!(
                "dubflow-chunk-journal-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            assert!(!path.exists());
            fs::create_dir(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            assert_eq!(self.0.parent(), Some(std::env::temp_dir().as_path()));
            assert!(self
                .0
                .file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("dubflow-chunk-journal-"));
            fn unseal(p: &Path) {
                for e in fs::read_dir(p).unwrap() {
                    let p = e.unwrap().path();
                    let m = fs::symlink_metadata(&p).unwrap();
                    assert!(!m.file_type().is_symlink());
                    if m.is_dir() {
                        unseal(&p)
                    } else {
                        let mut x = m.permissions();
                        x.set_readonly(false);
                        fs::set_permissions(p, x).unwrap();
                    }
                }
            }
            unseal(&self.0);
            fs::remove_dir_all(&self.0).unwrap();
        }
    }
    fn budget(n: u64) -> MixStorageBudget {
        MixStorageBudget {
            maximum_bytes: n,
            minimum_free_bytes: 100,
        }
    }
    fn end() -> Instant {
        Instant::now() + Duration::from_secs(30)
    }
    fn plan() -> String {
        format!("sha256:{:x}", Sha256::digest(b"native current plan"))
    }
    #[test]
    fn immutable_journal_reopens_exact_ordered_markers_deduplicates_without_mtime_change_or_new_space(
    ) {
        let f = Fixture::new();
        let p = plan();
        let one = "{\"ordinal\":1,\"wide\":9007199254740993}\n";
        let two = "{\"ordinal\":2}";
        let store = RetainedMixStore::new(&f.0, "journal-job", budget(10000)).unwrap();
        store
            .publish_chunk_checkpoint(&p, 1, one, |_| Ok(u64::MAX), end())
            .unwrap();
        let path = store
            .objects
            .root
            .join(ordinal_name(&prefix(&p).unwrap(), 1).unwrap());
        let before = fs::metadata(&path).unwrap().modified().unwrap();
        assert_eq!(
            store
                .publish_chunk_checkpoint(
                    &p,
                    1,
                    one,
                    |_| panic!("sealed marker reuse allocates no space"),
                    end()
                )
                .unwrap(),
            one
        );
        assert_eq!(fs::metadata(&path).unwrap().modified().unwrap(), before);
        store
            .publish_chunk_checkpoint(&p, 2, two, |_| Ok(u64::MAX), end())
            .unwrap();
        drop(store);
        let reopened = RetainedMixStore::open_existing(&f.0, "journal-job", budget(10000)).unwrap();
        assert_eq!(
            reopened
                .read_chunk_checkpoints(&p, end(), &mut || false)
                .unwrap(),
            vec![(1, one.into()), (2, two.into())]
        );
        assert_eq!(fs::metadata(&path).unwrap().modified().unwrap(), before);
        assert!(reopened
            .publish_chunk_checkpoint(
                &p,
                1,
                "{\"changed\":1}",
                |_| panic!("changed collision never writes"),
                end()
            )
            .is_err());
        assert_eq!(fs::read_to_string(path).unwrap(), one);
    }
    #[test]
    fn journal_counts_other_job_orphan_media_bytes_and_changed_reserve_preserves_prior_marker() {
        let f = Fixture::new();
        let p = plan();
        let one = "{\"one\":1}";
        let store = RetainedMixStore::new(&f.0, "journal-old", budget(10000)).unwrap();
        store
            .publish_chunk_checkpoint(&p, 1, one, |_| Ok(u64::MAX), end())
            .unwrap();
        fs::write(store.objects.root.join(".copy-media-orphan"), b"orphan").unwrap();
        let used = store.used_bytes(end()).unwrap();
        drop(store);
        let store = RetainedMixStore::new(&f.0, "journal-new", budget(used)).unwrap();
        assert!(store
            .publish_chunk_checkpoint(
                &p,
                1,
                "{\"new\":1}",
                |_| panic!("shared quota refuses before caller space"),
                end()
            )
            .is_err());
        drop(store);
        let store = RetainedMixStore::open_existing(&f.0, "journal-old", budget(200000)).unwrap();
        let next = format!("{{\"large\":\"{}\"}}", "x".repeat(40000));
        for fail_at in [1, 3, 4] {
            let mut calls = 0;
            assert!(store
                .publish_chunk_checkpoint(
                    &p,
                    2,
                    &next,
                    |_| {
                        calls += 1;
                        Ok(if calls == fail_at { 0 } else { u64::MAX })
                    },
                    end()
                )
                .is_err());
            assert_eq!(store.used_bytes(end()).unwrap(), used);
            assert_eq!(
                store
                    .read_chunk_checkpoints(&p, end(), &mut || false)
                    .unwrap(),
                vec![(1, one.into())]
            );
        }
    }
    #[test]
    fn journal_refuses_gap_link_unsealed_bad_names_cancel_and_record_bounds_without_repair() {
        let f = Fixture::new();
        let p = plan();
        let store = RetainedMixStore::new(&f.0, "journal-fences", budget(100000)).unwrap();
        let raw = "{\"ordinal\":1}";
        assert!(store
            .publish_chunk_checkpoint(
                &p,
                2,
                raw,
                |_| panic!("gap refuses before space probe"),
                end()
            )
            .is_err());
        assert!(store
            .publish_chunk_checkpoint(
                &p,
                0,
                raw,
                |_| panic!("ordinal refuses before space probe"),
                end()
            )
            .is_err());
        let large = format!("{{\"x\":\"{}\"}}", "x".repeat(MAX_RETAINED_METADATA_BYTES));
        assert!(store
            .publish_chunk_checkpoint(
                &p,
                1,
                &large,
                |_| panic!("record refuses before allocation"),
                end()
            )
            .is_err());
        store
            .publish_chunk_checkpoint(&p, 1, raw, |_| Ok(u64::MAX), end())
            .unwrap();
        assert!(store
            .read_chunk_checkpoints(&p, end(), &mut || true)
            .is_err());
        let one = store
            .objects
            .root
            .join(ordinal_name(&prefix(&p).unwrap(), 1).unwrap());
        let alias = f.0.join("owned-marker-link");
        fs::hard_link(&one, &alias).unwrap();
        assert!(store
            .read_chunk_checkpoints(&p, end(), &mut || false)
            .is_err());
        let mut mode = fs::metadata(&one).unwrap().permissions();
        mode.set_readonly(false);
        fs::set_permissions(&one, mode).unwrap();
        fs::remove_file(alias).unwrap();
        assert!(store
            .read_chunk_checkpoints(&p, end(), &mut || false)
            .is_err());
        let mut mode = fs::metadata(&one).unwrap().permissions();
        mode.set_readonly(true);
        fs::set_permissions(&one, mode).unwrap();
        let bad = store
            .objects
            .root
            .join(format!("{}0002x.json", prefix(&p).unwrap()));
        fs::write(&bad, b"{}").unwrap();
        assert!(store
            .read_chunk_checkpoints(&p, end(), &mut || false)
            .is_err());
        assert_eq!(fs::read_to_string(one).unwrap(), raw);
        assert!(bad.exists());
    }

    #[test]
    #[ignore = "child entry for explicit owned process-kill journal fixture"]
    fn journal_crash_child_writer() {
        let path = PathBuf::from(
            std::env::var_os("DUBFLOW_TEST_JOURNAL_CHILD_ROOT")
                .expect("explicit owned fixture root required"),
        );
        assert_eq!(path.parent(), Some(std::env::temp_dir().as_path()));
        assert!(path
            .file_name()
            .unwrap()
            .to_string_lossy()
            .starts_with("dubflow-chunk-journal-"));
        plain(&path).unwrap();
        assert!(path.is_dir());
        let store = RetainedMixStore::new(&path, "journal-killed", budget(10000)).unwrap();
        store
            .publish_chunk_checkpoint(
                &plan(),
                1,
                "{\"native_checkpoint\":1,\"wide\":9007199254740993}\n",
                |_| Ok(u64::MAX),
                end(),
            )
            .unwrap();
        println!("NATIVE_JOURNAL_CHECKPOINT_READY");
        std::io::stdout().flush().unwrap();
        loop {
            let _held = &store;
            std::thread::sleep(Duration::from_millis(50));
        }
    }

    #[test]
    fn output_journal_survives_actual_killed_child_and_reopens_prior_marker_and_writer_without_replay(
    ) {
        use std::io::{BufRead, BufReader};
        use std::process::{Child, Command, Stdio};
        use std::sync::mpsc;
        struct OwnedChild(Child);
        impl Drop for OwnedChild {
            fn drop(&mut self) {
                if self.0.try_wait().ok().flatten().is_none() {
                    let _ = self.0.kill();
                    let until = Instant::now() + Duration::from_secs(5);
                    while Instant::now() < until {
                        if self.0.try_wait().ok().flatten().is_some() {
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(10));
                    }
                }
            }
        }
        let fixture = Fixture::new();
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args([
                "--ignored",
                "--exact",
                "review_assets::chunk_journal::tests::journal_crash_child_writer",
                "--nocapture",
                "--test-threads=1",
            ])
            .env("DUBFLOW_TEST_JOURNAL_CHILD_ROOT", &fixture.0)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW for the owned test child.
        }
        let mut child = OwnedChild(command.spawn().unwrap());
        let stdout = child.0.stdout.take().unwrap();
        let (tx, rx) = mpsc::channel();
        let reader = std::thread::spawn(move || {
            let mut total = 0usize;
            for line in BufReader::new(stdout).take(4097).lines() {
                let line = line.unwrap();
                total += line.len();
                assert!(total <= 4096, "owned child fixture output bound");
                if line.contains("NATIVE_JOURNAL_CHECKPOINT_READY") {
                    let _ = tx.send(());
                }
            }
        });
        let ready = rx.recv_timeout(Duration::from_secs(20));
        // TerminateProcess/SIGKILL: no Rust Drop/normal fixture exit can release
        // the writer or finish marker publication for this child.
        if child.0.try_wait().unwrap().is_none() {
            child.0.kill().unwrap();
        }
        let until = Instant::now() + Duration::from_secs(5);
        let status = loop {
            if let Some(status) = child.0.try_wait().unwrap() {
                break status;
            }
            assert!(Instant::now() < until, "killed owned child must reap");
            std::thread::sleep(Duration::from_millis(10));
        };
        reader.join().unwrap();
        ready.expect("owned child must reach sealed marker before kill");
        assert!(!status.success());
        let p = plan();
        let first = "{\"native_checkpoint\":1,\"wide\":9007199254740993}\n";
        let store =
            RetainedMixStore::open_existing(&fixture.0, "journal-killed", budget(10000)).unwrap();
        let marker = store
            .objects
            .root
            .join(ordinal_name(&prefix(&p).unwrap(), 1).unwrap());
        let before = fs::metadata(&marker).unwrap().modified().unwrap();
        assert_eq!(
            store
                .read_chunk_checkpoints(&p, end(), &mut || false)
                .unwrap(),
            vec![(1, first.into())]
        );
        assert_eq!(
            store
                .publish_chunk_checkpoint(
                    &p,
                    1,
                    first,
                    |_| panic!("recovered sealed progress must not rewrite"),
                    end()
                )
                .unwrap(),
            first
        );
        store
            .publish_chunk_checkpoint(&p, 2, "{\"native_checkpoint\":2}", |_| Ok(u64::MAX), end())
            .unwrap();
        assert_eq!(
            store
                .read_chunk_checkpoints(&p, end(), &mut || false)
                .unwrap()
                .len(),
            2
        );
        assert_eq!(fs::read_to_string(&marker).unwrap(), first);
        assert_eq!(fs::metadata(&marker).unwrap().modified().unwrap(), before);
        // Actual killed-process storage component only. No processing worker,
        // real media, full Review graph or installed-product crash is admitted.
    }
}
