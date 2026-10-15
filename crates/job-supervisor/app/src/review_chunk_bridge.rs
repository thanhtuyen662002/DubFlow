//! Bounded private chunk-worker request/ack/result transport. Native callers
//! still supply admitted command/current/runtime/quota fences; no packet grants
//! processing, media, QC or graph authority. Original collect/protocol unchanged.
use super::*;
use std::io::{BufRead, BufReader};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

const FRAME: usize = 64 * 1024;
const WIRE: usize = 16 * 1024 * 1024;
const REQUESTS: u64 = 4096;

fn exact(v: &Value) -> SupervisorResult<String> {
    let mut raw = String::new();
    super::super::review_document::canonical(v, &mut raw)?;
    Ok(raw)
}
fn hash_pin(v: &str) -> bool {
    v.len() == 71
        && v.starts_with("sha256:")
        && v[7..]
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
#[derive(Clone)]
pub(crate) struct ChunkWorkerBinding(Value);
impl ChunkWorkerBinding {
    pub(crate) fn new(
        context: &str,
        plan: &str,
        chunk: &str,
        environment: &str,
    ) -> SupervisorResult<Self> {
        if ![context, plan, chunk, environment]
            .into_iter()
            .all(hash_pin)
        {
            return Err(invalid("chunk worker binding requires native full hashes"));
        }
        Ok(Self(
            json!({"context_sha256":context,"plan_sha256":plan,"chunk_identity":chunk,"runtime_environment_sha256":environment}),
        ))
    }
    pub(crate) fn descriptor(&self) -> Value {
        self.0.clone()
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum NativeChunkRequest {
    Fence,
    Capacity(u64),
}
enum Frame {
    Request(NativeChunkRequest),
    Result(String),
}
fn admit(raw: &[u8], binding: &ChunkWorkerBinding, sequence: u64) -> SupervisorResult<Frame> {
    if !(1..=REQUESTS + 1).contains(&sequence) {
        return Err(invalid("chunk worker sequence is outside its native bound"));
    }
    let text = std::str::from_utf8(raw).map_err(|_| invalid("chunk worker frame is not UTF8"))?;
    let value = decode(text, FRAME)?;
    if exact(&value)? != text
        || value["schema_version"].as_u64() != Some(1)
        || value["binding"] != binding.0
        || value["sequence"].as_u64() != Some(sequence)
    {
        return Err(invalid(
            "chunk worker changed canonical version/binding/sequence",
        ));
    }
    if value["kind"] == "review_chunk_native_request" {
        closed(
            &value,
            &[
                "schema_version",
                "kind",
                "binding",
                "sequence",
                "operation",
                "additional_bytes",
            ],
        )?;
        if sequence > REQUESTS {
            return Err(invalid("chunk worker exceeds native request bound"));
        }
        let additional = super::super::review_document::tick(&value["additional_bytes"], false)?;
        if additional > 512 * 1024 * 1024 {
            return Err(invalid(
                "chunk capacity exceeds bounded private reservation",
            ));
        }
        match value["operation"].as_str() {
            Some("fence") if additional == 0 => Ok(Frame::Request(NativeChunkRequest::Fence)),
            Some("capacity") => Ok(Frame::Request(NativeChunkRequest::Capacity(additional))),
            _ => Err(invalid("unknown chunk native callback operation")),
        }
    } else {
        closed(
            &value,
            &[
                "schema_version",
                "kind",
                "binding",
                "sequence",
                "result_json",
            ],
        )?;
        if value["kind"] != "review_chunk_worker_result" || sequence > REQUESTS + 1 {
            return Err(invalid("unknown chunk worker final frame"));
        }
        let result = value["result_json"]
            .as_str()
            .ok_or_else(|| invalid("chunk final result must preserve exact JSON bytes"))?;
        if !decode(result, FRAME)?.is_object() {
            return Err(invalid("chunk final result needs object root"));
        }
        Ok(Frame::Result(result.into()))
    }
}
fn acknowledgement(
    raw: &[u8],
    binding: &ChunkWorkerBinding,
    sequence: u64,
) -> SupervisorResult<Vec<u8>> {
    let digest = format!("sha256:{:x}", Sha256::digest(raw));
    Ok((exact(&json!({"schema_version":1,"kind":"review_chunk_native_ack","binding":binding.0,"sequence":sequence,
        "request_sha256":digest,"accepted":true,"runtime_environment_sha256":binding.0["runtime_environment_sha256"]}))? + "\n").into_bytes())
}
enum Event {
    Output(Vec<u8>),
    OutputEnd,
    Diagnostic(std::io::Result<Vec<u8>>),
    Ack(u64),
    Failure,
}

/// command must come from the independently admitted processing release. The
/// lightweight observe callback owns current/cancel/deadline observation; each
/// requested native callback must enforce its actual runtime/quota admission.
pub(crate) fn collect_chunk_worker(
    mut command: Command,
    binding: &ChunkWorkerBinding,
    deadline: Instant,
    observe: &mut impl FnMut(Instant) -> SupervisorResult<()>,
    native: &mut impl FnMut(NativeChunkRequest, Instant) -> SupervisorResult<()>,
) -> SupervisorResult<String> {
    if deadline
        .checked_duration_since(Instant::now())
        .is_none_or(|left| left.is_zero() || left > Duration::from_secs(900))
    {
        return Err(invalid(
            "chunk worker needs one bounded absolute native deadline",
        ));
    }
    observe(deadline)?;
    if Instant::now() >= deadline {
        return Err(invalid("chunk observation exhausted deadline before spawn"));
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
    let mut process = AdapterProcess {
        child: command.spawn()?,
        tree: None,
    };
    process.tree = Some(super::super::WorkerTree::attach(&process.child)?);
    if Instant::now() >= deadline {
        return Err(invalid(
            "chunk process admission exhausted deadline before byte gate",
        ));
    }
    let stdout = process.child.stdout.take().unwrap();
    let mut stderr = process.child.stderr.take().unwrap();
    let mut stdin = process.child.stdin.take().unwrap();
    let stop = Arc::new(AtomicBool::new(false));
    let (send, receive) = mpsc::sync_channel(16);
    let (ack, responses) = mpsc::sync_channel::<(u64, Vec<u8>)>(1);
    let output = send.clone();
    let reader = std::thread::spawn(move || {
        let mut source = BufReader::new(stdout);
        let mut total = 0usize;
        loop {
            let mut frame = Vec::new();
            match (&mut source)
                .take(FRAME as u64 + 1)
                .read_until(b'\n', &mut frame)
            {
                Ok(0) => {
                    let _ = output.send(Event::OutputEnd);
                    return;
                }
                Ok(n) => {
                    total = total.saturating_add(n);
                    if n > FRAME || total > WIRE || frame.last() != Some(&b'\n') {
                        let _ = output.send(Event::Failure);
                        return;
                    }
                }
                Err(_) => {
                    let _ = output.send(Event::Failure);
                    return;
                }
            }
            frame.pop();
            if output.send(Event::Output(frame)).is_err() {
                return;
            }
        }
    });
    let diagnostics = send.clone();
    let error_reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        let read = (&mut stderr)
            .take(4097)
            .read_to_end(&mut bytes)
            .map(|_| bytes);
        let _ = diagnostics.send(Event::Diagnostic(read));
    });
    let writes = send.clone();
    let stopping = stop.clone();
    let writer = std::thread::spawn(move || {
        // WorkerTree is already attached before this byte permits any work.
        if stdin.write_all(&[1]).is_err() {
            let _ = writes.send(Event::Failure);
            return;
        }
        while !stopping.load(Ordering::Relaxed) {
            match responses.recv_timeout(Duration::from_millis(20)) {
                Ok((sequence, raw)) => {
                    if stdin.write_all(&raw).and_then(|_| stdin.flush()).is_err() {
                        let _ = writes.send(Event::Failure);
                        return;
                    }
                    if writes.send(Event::Ack(sequence)).is_err() {
                        return;
                    }
                }
                Err(mpsc::RecvTimeoutError::Timeout) => (),
                Err(mpsc::RecvTimeoutError::Disconnected) => return,
            }
        }
    });
    drop(send);
    let result = (|| {
        let mut calls = 0u64;
        let mut written = 0u64;
        let mut ended = false;
        let mut clean_diagnostic = false;
        let mut final_result = None;
        loop {
            if Instant::now() >= deadline {
                return Err(invalid("chunk worker exceeded absolute native deadline"));
            }
            observe(deadline)?;
            if Instant::now() >= deadline {
                return Err(invalid("chunk observation exhausted native deadline"));
            }
            match receive.recv_timeout(Duration::from_millis(20)) {
                Ok(Event::Output(raw)) => {
                    if final_result.is_some() {
                        return Err(invalid("chunk worker emitted bytes after final result"));
                    }
                    match admit(&raw, binding, calls + 1)? {
                        Frame::Request(request) => {
                            native(request, deadline)?;
                            observe(deadline)?;
                            if Instant::now() >= deadline {
                                return Err(invalid("chunk callback exceeded native deadline"));
                            }
                            calls += 1;
                            ack.try_send((calls, acknowledgement(&raw, binding, calls)?)).map_err(|_| invalid("chunk worker pipelined requests or blocked native acknowledgement"))?;
                        }
                        Frame::Result(raw) => final_result = Some(raw),
                    }
                }
                Ok(Event::OutputEnd) => ended = true,
                Ok(Event::Diagnostic(bytes)) => {
                    let bytes = bytes?;
                    if bytes.len() > 4096 {
                        return Err(invalid(
                            "chunk worker diagnostics exceeded their native byte bound",
                        ));
                    }
                    if !bytes.is_empty() {
                        return Err(invalid(&format!(
                            "chunk worker emitted diagnostics ({} bytes): {}",
                            bytes.len(),
                            String::from_utf8_lossy(&bytes)
                        )));
                    }
                    clean_diagnostic = true;
                }
                Ok(Event::Ack(sequence)) => {
                    if sequence != written + 1 {
                        return Err(invalid("chunk acknowledgement order changed"));
                    }
                    written = sequence;
                }
                Ok(Event::Failure) => {
                    return Err(invalid(
                        "chunk worker pipe failed, truncated or exceeded byte bounds",
                    ))
                }
                Err(mpsc::RecvTimeoutError::Timeout) => (),
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    return Err(invalid(
                        "chunk worker channel ended before complete admission",
                    ))
                }
            }
            if let Some(status) = process.child.try_wait()? {
                if !status.success() {
                    return Err(invalid("chunk worker exited unsuccessfully"));
                }
                if ended && clean_diagnostic && written == calls {
                    observe(deadline)?;
                    if Instant::now() >= deadline {
                        return Err(invalid("chunk completion exceeded native deadline"));
                    }
                    return final_result
                        .ok_or_else(|| invalid("chunk worker omitted final result"));
                }
            }
        }
    })();
    stop.store(true, Ordering::Relaxed);
    drop(ack);
    drop(receive);
    drop(process); // Terminate/reap owned descendants before joining pipe workers.
    let cleanup = Instant::now() + Duration::from_secs(2);
    let workers = [reader, error_reader, writer];
    for worker in &workers {
        while !worker.is_finished() && Instant::now() < cleanup {
            std::thread::sleep(Duration::from_millis(5));
        }
    }
    if workers.iter().any(|w| !w.is_finished()) {
        return Err(invalid("contained chunk pipe workers did not join"));
    }
    for worker in workers {
        worker
            .join()
            .map_err(|_| invalid("chunk pipe worker panicked"))?;
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    fn binding() -> ChunkWorkerBinding {
        ChunkWorkerBinding::new(
            &format!("sha256:{}", "a".repeat(64)),
            &format!("sha256:{}", "b".repeat(64)),
            &format!("sha256:{}", "c".repeat(64)),
            &format!("sha256:{}", "d".repeat(64)),
        )
        .unwrap()
    }
    fn request(binding: &ChunkWorkerBinding) -> Value {
        json!({"schema_version":1,"kind":"review_chunk_native_request","binding":binding.descriptor(),
            "sequence":1,"operation":"capacity","additional_bytes":"536870912"})
    }
    #[test]
    fn private_chunk_transport_refuses_cross_request_sequence_operation_and_noncanonical_capacity_before_callback(
    ) {
        let binding = binding();
        let original = request(&binding);
        assert!(matches!(
            admit(exact(&original).unwrap().as_bytes(), &binding, 1).unwrap(),
            Frame::Request(NativeChunkRequest::Capacity(536870912))
        ));
        for (field, value) in [
            ("schema_version", json!(true)),
            ("kind", json!("foreign")),
            ("sequence", json!(2)),
            ("binding", json!({})),
            ("operation", json!("publish")),
            ("additional_bytes", json!(536870912)),
            ("additional_bytes", json!("536870913")),
            ("additional_bytes", json!("01")),
            ("unknown", Value::Null),
        ] {
            let mut bad = original.clone();
            bad[field] = value;
            assert!(
                admit(exact(&bad).unwrap().as_bytes(), &binding, 1).is_err(),
                "{field}"
            );
        }
        let mut fence = original.clone();
        fence["operation"] = json!("fence");
        assert!(admit(exact(&fence).unwrap().as_bytes(), &binding, 1).is_err());
        fence["additional_bytes"] = json!("0");
        assert!(matches!(
            admit(exact(&fence).unwrap().as_bytes(), &binding, 1).unwrap(),
            Frame::Request(NativeChunkRequest::Fence)
        ));
        assert!(admit((exact(&fence).unwrap() + "\n").as_bytes(), &binding, 1).is_err());
        assert!(admit(exact(&fence).unwrap().as_bytes(), &binding, 0).is_err());
        assert!(admit(exact(&fence).unwrap().as_bytes(), &binding, REQUESTS + 2).is_err());
    }
    #[test]
    fn native_ack_hashes_exact_request_and_final_transport_preserves_wide_json_without_media_grant()
    {
        let binding = binding();
        let contract: Value = serde_json::from_str(include_str!(
            "../../../../contracts/review/native-chunk-worker-bridge-v1.json"
        ))
        .unwrap();
        let fixture = &contract["examples"][0];
        let request_json = fixture["request_json"].as_str().unwrap();
        assert!(matches!(
            admit(request_json.as_bytes(), &binding, 1).unwrap(),
            Frame::Request(NativeChunkRequest::Capacity(0))
        ));
        assert_eq!(
            acknowledgement(request_json.as_bytes(), &binding, 1).unwrap(),
            (fixture["ack_json"].as_str().unwrap().to_owned() + "\n").into_bytes()
        );
        match admit(
            fixture["result_json"].as_str().unwrap().as_bytes(),
            &binding,
            2,
        )
        .unwrap()
        {
            Frame::Result(raw) => assert_eq!(raw, "{\"wide\":9007199254740993}"),
            _ => panic!("golden result must preserve raw nested bytes"),
        }
        let raw = exact(&request(&binding)).unwrap();
        let bytes = acknowledgement(raw.as_bytes(), &binding, 1).unwrap();
        assert_eq!(bytes.last(), Some(&b'\n'));
        let ack = decode(
            std::str::from_utf8(&bytes[..bytes.len() - 1]).unwrap(),
            FRAME,
        )
        .unwrap();
        assert_eq!(
            ack["request_sha256"],
            format!("sha256:{:x}", Sha256::digest(raw.as_bytes()))
        );
        assert_eq!(ack["binding"], binding.descriptor());
        assert_eq!(ack["accepted"], true);
        let result = "{\"wide\":9007199254740993}";
        let packet = json!({"schema_version":1,"kind":"review_chunk_worker_result","binding":binding.descriptor(),"sequence":1,"result_json":result});
        match admit(exact(&packet).unwrap().as_bytes(), &binding, 1).unwrap() {
            Frame::Result(saved) => assert_eq!(saved, result),
            _ => panic!("transport returns unadmitted result bytes only"),
        }
        let mut bad = packet;
        bad["result_json"] = json!("[]");
        assert!(admit(exact(&bad).unwrap().as_bytes(), &binding, 1).is_err());
    }
    #[test]
    fn chunk_expired_or_current_observation_refusal_precedes_any_process_creation() {
        let binding = binding();
        let mut calls = 0;
        let mut native = |_: NativeChunkRequest, _: Instant| {
            calls += 1;
            Ok(())
        };
        assert!(collect_chunk_worker(
            Command::new("never-spawn-this-fixture"),
            &binding,
            Instant::now(),
            &mut |_| panic!("expired transport must refuse before observation"),
            &mut native
        )
        .is_err());
        let refused = collect_chunk_worker(
            Command::new("never-spawn-this-fixture"),
            &binding,
            Instant::now() + Duration::from_secs(1),
            &mut |_| Err(invalid("explicit native current/cancel refusal")),
            &mut native,
        )
        .unwrap_err();
        assert!(refused
            .to_string()
            .contains("explicit native current/cancel refusal"));
        assert_eq!(calls, 0);
    }
}
