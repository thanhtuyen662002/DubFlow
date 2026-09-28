//! Dependency-free reference implementation of the DubFlow worker protocol.
//!
//! The protocol is deliberately small and strict.  It is a line-delimited
//! UTF-8 JSON stream, but this crate does not depend on a JSON library: the
//! parser below validates the closed envelope and payload shapes directly so
//! the supervisor can keep the same guarantees in a minimal runtime.

use std::collections::HashSet;
use std::fmt;
use std::str;

pub const SCHEMA_VERSION: u32 = 1;
pub const MAX_LINE_BYTES: usize = 64 * 1024;
pub const MAX_ID_LENGTH: usize = 128;
pub const MAX_PROGRESS_BUFFER: usize = 128;
const MAX_JSON_DEPTH: usize = 128;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ProtocolError {
    InvalidType(&'static str),
    InvalidValue(String),
    InvalidString(&'static str),
    InvalidJson(String),
    InvalidUtf8,
    LineTooLarge,
    DuplicateField(String),
    UnknownField(String),
    MissingField(&'static str),
    UnsupportedVersion(u32),
    UnknownMessageType(String),
    SequenceGap { expected: u64, received: u64 },
    SequenceOverflow,
    StreamTerminal,
    HeartbeatRegression,
    HeartbeatTimeout { elapsed_ms: u64, timeout_ms: u64 },
    InvalidBufferMessage,
    CancellationTooLate,
    CheckpointNotPending,
    UnsafeCancellation,
    RetryExhausted,
    RetryConditionUnchanged,
}

impl fmt::Display for ProtocolError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidType(name) => write!(f, "{name} has an invalid type"),
            Self::InvalidValue(detail) => write!(f, "invalid protocol value: {detail}"),
            Self::InvalidString(name) => write!(f, "{name} must be a non-empty bounded string"),
            Self::InvalidJson(detail) => write!(f, "malformed JSON: {detail}"),
            Self::InvalidUtf8 => write!(f, "worker output is not UTF-8"),
            Self::LineTooLarge => write!(f, "worker line exceeds 64 KiB"),
            Self::DuplicateField(name) => write!(f, "duplicate JSON member: {name}"),
            Self::UnknownField(name) => write!(f, "unknown JSON member: {name}"),
            Self::MissingField(name) => write!(f, "missing required member: {name}"),
            Self::UnsupportedVersion(version) => write!(f, "unsupported worker protocol version: {version}"),
            Self::UnknownMessageType(value) => write!(f, "unknown worker message type: {value}"),
            Self::SequenceGap { expected, received } => {
                write!(f, "expected sequence {expected}, received {received}")
            }
            Self::SequenceOverflow => write!(f, "sequence cannot advance past u64::MAX"),
            Self::StreamTerminal => write!(f, "messages after shutdown are forbidden"),
            Self::HeartbeatRegression => write!(f, "worker monotonic time moved backwards"),
            Self::HeartbeatTimeout { elapsed_ms, timeout_ms } => {
                write!(f, "heartbeat deadline exceeded: {elapsed_ms}ms > {timeout_ms}ms")
            }
            Self::InvalidBufferMessage => write!(f, "only progress messages may enter the progress buffer"),
            Self::CancellationTooLate => write!(f, "cancellation arrived after a terminal state"),
            Self::CheckpointNotPending => write!(f, "a cancellation checkpoint was not requested"),
            Self::UnsafeCancellation => write!(f, "cancellation requires a safe checkpoint"),
            Self::RetryExhausted => write!(f, "retry budget is exhausted"),
            Self::RetryConditionUnchanged => write!(f, "retry condition did not materially change"),
        }
    }
}

impl std::error::Error for ProtocolError {}

pub type Result<T> = std::result::Result<T, ProtocolError>;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum MessageType {
    Command,
    Progress,
    Checkpoint,
    Heartbeat,
    Cancel,
    Failure,
    Shutdown,
}

impl MessageType {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Command => "command",
            Self::Progress => "progress",
            Self::Checkpoint => "checkpoint",
            Self::Heartbeat => "heartbeat",
            Self::Cancel => "cancel",
            Self::Failure => "failure",
            Self::Shutdown => "shutdown",
        }
    }

    fn parse(value: String) -> Result<Self> {
        match value.as_str() {
            "command" => Ok(Self::Command),
            "progress" => Ok(Self::Progress),
            "checkpoint" => Ok(Self::Checkpoint),
            "heartbeat" => Ok(Self::Heartbeat),
            "cancel" => Ok(Self::Cancel),
            "failure" => Ok(Self::Failure),
            "shutdown" => Ok(Self::Shutdown),
            _ => Err(ProtocolError::UnknownMessageType(value)),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum ShutdownStatus {
    Completed,
    Cancelled,
    Failed,
}

impl ShutdownStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Completed => "completed",
            Self::Cancelled => "cancelled",
            Self::Failed => "failed",
        }
    }

    fn parse(value: String) -> Result<Self> {
        match value.as_str() {
            "completed" => Ok(Self::Completed),
            "cancelled" => Ok(Self::Cancelled),
            "failed" => Ok(Self::Failed),
            _ => Err(ProtocolError::InvalidValue(format!("invalid shutdown status {value:?}"))),
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum Payload {
    Command { command: String, args_json: String },
    Progress {
        fraction: f64,
        detail: Option<String>,
        units_done: Option<u64>,
        units_total: Option<u64>,
    },
    Checkpoint {
        checkpoint_id: String,
        reusable: bool,
        artifact_hash: Option<String>,
    },
    Heartbeat { monotonic_ms: u64 },
    Cancel { reason: String },
    Failure {
        code: String,
        retryable: bool,
        attempt: u8,
        condition: String,
    },
    Shutdown { status: ShutdownStatus },
}

impl Payload {
    fn message_type(&self) -> MessageType {
        match self {
            Self::Command { .. } => MessageType::Command,
            Self::Progress { .. } => MessageType::Progress,
            Self::Checkpoint { .. } => MessageType::Checkpoint,
            Self::Heartbeat { .. } => MessageType::Heartbeat,
            Self::Cancel { .. } => MessageType::Cancel,
            Self::Failure { .. } => MessageType::Failure,
            Self::Shutdown { .. } => MessageType::Shutdown,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Envelope {
    pub schema_version: u32,
    pub message_type: MessageType,
    pub message_id: String,
    pub job_id: String,
    pub stage_id: String,
    pub sequence: u64,
    pub payload: Payload,
}

impl Envelope {
    pub fn new(
        message_type: MessageType,
        message_id: impl Into<String>,
        job_id: impl Into<String>,
        stage_id: impl Into<String>,
        sequence: u64,
        payload: Payload,
    ) -> Result<Self> {
        let envelope = Self {
            schema_version: SCHEMA_VERSION,
            message_type,
            message_id: message_id.into(),
            job_id: job_id.into(),
            stage_id: stage_id.into(),
            sequence,
            payload,
        };
        envelope.validate()?;
        Ok(envelope)
    }

    pub fn from_line(line: &[u8]) -> Result<Self> {
        if line.len() > MAX_LINE_BYTES {
            return Err(ProtocolError::LineTooLarge);
        }
        let mut content = line;
        if content.last() == Some(&b'\n') {
            content = &content[..content.len() - 1];
        }
        if content.is_empty() || content.iter().any(|byte| *byte == b'\r' || *byte == b'\n') {
            return Err(ProtocolError::InvalidJson("one JSON object is required per line".into()));
        }
        let text = str::from_utf8(content).map_err(|_| ProtocolError::InvalidUtf8)?;
        Self::from_json(text)
    }

    pub fn from_json(input: &str) -> Result<Self> {
        if input.as_bytes().len() > MAX_LINE_BYTES {
            return Err(ProtocolError::LineTooLarge);
        }
        if input.bytes().any(|byte| byte == b'\r' || byte == b'\n') {
            return Err(ProtocolError::InvalidJson("one JSON object is required per line".into()));
        }
        let mut cursor = Cursor::new(input);
        cursor.skip_ws();
        cursor.expect_byte(b'{')?;
        let mut schema_version: Option<u32> = None;
        let mut message_type: Option<MessageType> = None;
        let mut message_id: Option<String> = None;
        let mut job_id: Option<String> = None;
        let mut stage_id: Option<String> = None;
        let mut sequence: Option<u64> = None;
        let mut payload_range: Option<(usize, usize)> = None;
        if cursor.try_byte(b'}')? {
            return Err(ProtocolError::InvalidJson("envelope object is empty".into()));
        }
        loop {
            let key = cursor.parse_string()?;
            cursor.expect_byte(b':')?;
            match key.as_str() {
                "schema_version" => {
                    ensure_not_set(&schema_version, &key)?;
                    let value = cursor.parse_u64()?;
                    let value = u32::try_from(value)
                        .map_err(|_| ProtocolError::InvalidValue("schema_version exceeds u32".into()))?;
                    schema_version = Some(value);
                }
                "message_type" => {
                    ensure_not_set(&message_type, &key)?;
                    message_type = Some(MessageType::parse(cursor.parse_string()?)?);
                }
                "message_id" => {
                    ensure_not_set(&message_id, &key)?;
                    message_id = Some(cursor.parse_bounded_string("message_id", MAX_ID_LENGTH)?);
                }
                "job_id" => {
                    ensure_not_set(&job_id, &key)?;
                    job_id = Some(cursor.parse_bounded_string("job_id", MAX_ID_LENGTH)?);
                }
                "stage_id" => {
                    ensure_not_set(&stage_id, &key)?;
                    stage_id = Some(cursor.parse_bounded_string("stage_id", MAX_ID_LENGTH)?);
                }
                "sequence" => {
                    ensure_not_set(&sequence, &key)?;
                    sequence = Some(cursor.parse_u64()?);
                }
                "payload" => {
                    ensure_not_set(&payload_range, &key)?;
                    payload_range = Some(cursor.parse_raw_value()?);
                }
                _ => return Err(ProtocolError::UnknownField(key)),
            }
            if cursor.try_byte(b'}')? {
                break;
            }
            cursor.expect_byte(b',')?;
        }
        cursor.finish()?;
        let schema_version = schema_version.ok_or(ProtocolError::MissingField("schema_version"))?;
        if schema_version != SCHEMA_VERSION {
            return Err(ProtocolError::UnsupportedVersion(schema_version));
        }
        let message_type = message_type.ok_or(ProtocolError::MissingField("message_type"))?;
        let payload_range = payload_range.ok_or(ProtocolError::MissingField("payload"))?;
        let payload = parse_payload(input, payload_range, message_type)?;
        let envelope = Self {
            schema_version,
            message_type,
            message_id: message_id.ok_or(ProtocolError::MissingField("message_id"))?,
            job_id: job_id.ok_or(ProtocolError::MissingField("job_id"))?,
            stage_id: stage_id.ok_or(ProtocolError::MissingField("stage_id"))?,
            sequence: sequence.ok_or(ProtocolError::MissingField("sequence"))?,
            payload,
        };
        envelope.validate()?;
        Ok(envelope)
    }

    pub fn to_json(&self) -> Result<String> {
        self.validate()?;
        let mut output = String::with_capacity(256);
        output.push_str("{\"schema_version\":");
        output.push_str(&self.schema_version.to_string());
        output.push_str(",\"message_type\":");
        push_json_string(&mut output, self.message_type.as_str());
        output.push_str(",\"message_id\":");
        push_json_string(&mut output, &self.message_id);
        output.push_str(",\"job_id\":");
        push_json_string(&mut output, &self.job_id);
        output.push_str(",\"stage_id\":");
        push_json_string(&mut output, &self.stage_id);
        output.push_str(",\"sequence\":");
        output.push_str(&self.sequence.to_string());
        output.push_str(",\"payload\":");
        write_payload(&mut output, &self.payload);
        output.push('}');
        if output.as_bytes().len() + 1 > MAX_LINE_BYTES {
            return Err(ProtocolError::LineTooLarge);
        }
        Ok(output)
    }

    pub fn to_line(&self) -> Result<Vec<u8>> {
        let mut output = self.to_json()?.into_bytes();
        output.push(b'\n');
        Ok(output)
    }

    fn validate(&self) -> Result<()> {
        if self.schema_version != SCHEMA_VERSION {
            return Err(ProtocolError::UnsupportedVersion(self.schema_version));
        }
        validate_string(&self.message_id, "message_id", MAX_ID_LENGTH)?;
        validate_string(&self.job_id, "job_id", MAX_ID_LENGTH)?;
        validate_string(&self.stage_id, "stage_id", MAX_ID_LENGTH)?;
        if self.sequence == 0 {
            return Err(ProtocolError::InvalidValue("sequence must be positive".into()));
        }
        if self.payload.message_type() != self.message_type {
            return Err(ProtocolError::InvalidValue("message_type does not match payload".into()));
        }
        validate_payload(&self.payload)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StreamValidator {
    heartbeat_timeout_ms: u64,
    started_ms: u64,
    expected_sequence: u64,
    last_heartbeat_ms: u64,
    terminal: bool,
}

impl StreamValidator {
    pub fn new(heartbeat_timeout_ms: u64, started_ms: u64) -> Result<Self> {
        if heartbeat_timeout_ms == 0 {
            return Err(ProtocolError::InvalidValue("heartbeat timeout must be positive".into()));
        }
        Ok(Self {
            heartbeat_timeout_ms,
            started_ms,
            expected_sequence: 1,
            last_heartbeat_ms: started_ms,
            terminal: false,
        })
    }

    pub fn accept(&mut self, envelope: &Envelope) -> Result<()> {
        if self.terminal {
            return Err(ProtocolError::StreamTerminal);
        }
        envelope.validate()?;
        if envelope.sequence != self.expected_sequence {
            return Err(ProtocolError::SequenceGap {
                expected: self.expected_sequence,
                received: envelope.sequence,
            });
        }
        if let Payload::Heartbeat { monotonic_ms } = &envelope.payload {
            if *monotonic_ms < self.last_heartbeat_ms {
                return Err(ProtocolError::HeartbeatRegression);
            }
        }
        if self.expected_sequence == u64::MAX {
            return Err(ProtocolError::SequenceOverflow);
        }
        self.expected_sequence += 1;
        if let Payload::Heartbeat { monotonic_ms } = &envelope.payload {
            self.last_heartbeat_ms = *monotonic_ms;
        }
        if envelope.message_type == MessageType::Shutdown {
            self.terminal = true;
        }
        Ok(())
    }

    pub fn check_heartbeat(&self, now_ms: u64) -> Result<()> {
        let elapsed = now_ms.saturating_sub(self.last_heartbeat_ms);
        if elapsed > self.heartbeat_timeout_ms {
            return Err(ProtocolError::HeartbeatTimeout {
                elapsed_ms: elapsed,
                timeout_ms: self.heartbeat_timeout_ms,
            });
        }
        Ok(())
    }

    pub fn expected_sequence(&self) -> u64 {
        self.expected_sequence
    }

    pub fn last_heartbeat_ms(&self) -> u64 {
        self.last_heartbeat_ms
    }

    pub fn terminal(&self) -> bool {
        self.terminal
    }

    pub fn started_ms(&self) -> u64 {
        self.started_ms
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ProgressBuffer {
    max_items: usize,
    items: Vec<Envelope>,
    dropped_count: u64,
    coalesced_count: u64,
}

impl ProgressBuffer {
    pub fn new(max_items: usize) -> Result<Self> {
        if max_items == 0 || max_items > MAX_PROGRESS_BUFFER {
            return Err(ProtocolError::InvalidValue(format!(
                "progress buffer must contain 1..={MAX_PROGRESS_BUFFER} items"
            )));
        }
        Ok(Self {
            max_items,
            items: Vec::with_capacity(max_items.min(MAX_PROGRESS_BUFFER)),
            dropped_count: 0,
            coalesced_count: 0,
        })
    }

    pub fn push(&mut self, envelope: Envelope) -> Result<()> {
        if envelope.message_type != MessageType::Progress {
            return Err(ProtocolError::InvalidBufferMessage);
        }
        if let Some(index) = self.items.iter().rposition(|item| item.stage_id == envelope.stage_id) {
            self.items[index] = envelope;
            self.coalesced_count += 1;
            return Ok(());
        }
        if self.items.len() >= self.max_items {
            self.items.remove(0);
            self.dropped_count += 1;
        }
        self.items.push(envelope);
        Ok(())
    }

    pub fn drain(&mut self) -> Vec<Envelope> {
        std::mem::take(&mut self.items)
    }

    pub fn len(&self) -> usize {
        self.items.len()
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }

    pub fn dropped_count(&self) -> u64 {
        self.dropped_count
    }

    pub fn coalesced_count(&self) -> u64 {
        self.coalesced_count
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CancellationState {
    Running,
    Requested,
    Checkpointed,
    Completed,
    Failed,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CancellationController {
    state: CancellationState,
    reason: Option<String>,
    checkpoint_id: Option<String>,
}

impl Default for CancellationController {
    fn default() -> Self {
        Self {
            state: CancellationState::Running,
            reason: None,
            checkpoint_id: None,
        }
    }
}

impl CancellationController {
    pub fn state(&self) -> CancellationState {
        self.state
    }

    pub fn reason(&self) -> Option<&str> {
        self.reason.as_deref()
    }

    pub fn checkpoint_id(&self) -> Option<&str> {
        self.checkpoint_id.as_deref()
    }

    pub fn request(&mut self, reason: impl Into<String>) -> Result<()> {
        let reason = reason.into();
        validate_string(&reason, "cancel.reason", 4096)?;
        if matches!(self.state, CancellationState::Completed | CancellationState::Failed) {
            return Err(ProtocolError::CancellationTooLate);
        }
        self.state = CancellationState::Requested;
        self.reason = Some(reason);
        Ok(())
    }

    pub fn checkpoint(&mut self, checkpoint_id: impl Into<String>) -> Result<()> {
        let checkpoint_id = checkpoint_id.into();
        validate_string(&checkpoint_id, "checkpoint_id", 256)?;
        if self.state != CancellationState::Requested {
            return Err(ProtocolError::CheckpointNotPending);
        }
        self.state = CancellationState::Checkpointed;
        self.checkpoint_id = Some(checkpoint_id);
        Ok(())
    }

    pub fn complete(&mut self) -> Result<()> {
        if self.state != CancellationState::Checkpointed {
            return Err(ProtocolError::UnsafeCancellation);
        }
        self.state = CancellationState::Completed;
        Ok(())
    }

    pub fn fail(&mut self) {
        self.state = CancellationState::Failed;
    }
}

/// Bounded retry bookkeeping.  A retry is accepted only when its condition
/// differs from the previous attempt and the finite attempt budget remains.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RetryBudget {
    max_attempts: u8,
    attempts: u8,
    last_condition: Option<String>,
}

impl RetryBudget {
    pub fn new(max_attempts: u8) -> Result<Self> {
        if max_attempts == 0 {
            return Err(ProtocolError::InvalidValue("retry budget must be positive".into()));
        }
        Ok(Self {
            max_attempts,
            attempts: 0,
            last_condition: None,
        })
    }

    pub fn record_retry(&mut self, condition: impl Into<String>) -> Result<u8> {
        let condition = condition.into();
        validate_string(&condition, "failure.condition", 4096)?;
        if self.attempts >= self.max_attempts {
            return Err(ProtocolError::RetryExhausted);
        }
        if self.last_condition.as_deref() == Some(condition.as_str()) {
            return Err(ProtocolError::RetryConditionUnchanged);
        }
        self.attempts += 1;
        self.last_condition = Some(condition);
        Ok(self.attempts)
    }

    pub fn attempts(&self) -> u8 {
        self.attempts
    }

    pub fn max_attempts(&self) -> u8 {
        self.max_attempts
    }
}

fn ensure_not_set<T>(slot: &Option<T>, name: &str) -> Result<()> {
    if slot.is_some() {
        Err(ProtocolError::DuplicateField(name.to_owned()))
    } else {
        Ok(())
    }
}

fn validate_string(value: &str, name: &'static str, max_chars: usize) -> Result<()> {
    if value.is_empty() || value.chars().count() > max_chars {
        return Err(ProtocolError::InvalidString(name));
    }
    Ok(())
}

fn validate_payload(payload: &Payload) -> Result<()> {
    match payload {
        Payload::Command { command, args_json } => {
            validate_string(command, "command", 128)?;
            validate_raw_object(args_json)?;
        }
        Payload::Progress {
            fraction,
            detail,
            units_done: _,
            units_total: _,
        } => {
            if !fraction.is_finite() || *fraction < 0.0 || *fraction > 1.0 {
                return Err(ProtocolError::InvalidValue("progress fraction must be finite and between 0 and 1".into()));
            }
            if let Some(value) = detail {
                validate_string(value, "progress.detail", 4096)?;
            }
        }
        Payload::Checkpoint {
            checkpoint_id,
            reusable: _,
            artifact_hash,
        } => {
            validate_string(checkpoint_id, "checkpoint_id", 256)?;
            if let Some(value) = artifact_hash {
                validate_string(value, "artifact_hash", 256)?;
            }
        }
        Payload::Heartbeat { .. } => {}
        Payload::Cancel { reason } => validate_string(reason, "cancel.reason", 4096)?,
        Payload::Failure {
            code,
            retryable: _,
            attempt,
            condition,
        } => {
            validate_string(code, "failure.code", 128)?;
            if *attempt == 0 {
                return Err(ProtocolError::InvalidValue("failure attempt must be positive".into()));
            }
            validate_string(condition, "failure.condition", 4096)?;
        }
        Payload::Shutdown { .. } => {}
    }
    Ok(())
}

fn validate_raw_object(value: &str) -> Result<()> {
    let mut cursor = Cursor::new(value);
    cursor.skip_ws();
    if cursor.peek() != Some(b'{') {
        return Err(ProtocolError::InvalidType("command.args"));
    }
    cursor.parse_raw_value()?;
    cursor.finish()
}

fn push_json_string(output: &mut String, value: &str) {
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\u{08}' => output.push_str("\\b"),
            '\u{0c}' => output.push_str("\\f"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            character if character <= '\u{1f}' => {
                output.push_str(&format!("\\u{:04x}", character as u32));
            }
            character => output.push(character),
        }
    }
    output.push('"');
}

fn write_payload(output: &mut String, payload: &Payload) {
    match payload {
        Payload::Command { command, args_json } => {
            output.push_str("{\"command\":");
            push_json_string(output, command);
            output.push_str(",\"args\":");
            output.push_str(args_json);
            output.push('}');
        }
        Payload::Progress {
            fraction,
            detail,
            units_done,
            units_total,
        } => {
            output.push_str("{\"fraction\":");
            output.push_str(&fraction.to_string());
            if let Some(value) = detail {
                output.push_str(",\"detail\":");
                push_json_string(output, value);
            }
            if let Some(value) = units_done {
                output.push_str(",\"units_done\":");
                output.push_str(&value.to_string());
            }
            if let Some(value) = units_total {
                output.push_str(",\"units_total\":");
                output.push_str(&value.to_string());
            }
            output.push('}');
        }
        Payload::Checkpoint {
            checkpoint_id,
            reusable,
            artifact_hash,
        } => {
            output.push_str("{\"checkpoint_id\":");
            push_json_string(output, checkpoint_id);
            output.push_str(",\"reusable\":");
            output.push_str(if *reusable { "true" } else { "false" });
            if let Some(value) = artifact_hash {
                output.push_str(",\"artifact_hash\":");
                push_json_string(output, value);
            }
            output.push('}');
        }
        Payload::Heartbeat { monotonic_ms } => {
            output.push_str("{\"monotonic_ms\":");
            output.push_str(&monotonic_ms.to_string());
            output.push('}');
        }
        Payload::Cancel { reason } => {
            output.push_str("{\"reason\":");
            push_json_string(output, reason);
            output.push('}');
        }
        Payload::Failure {
            code,
            retryable,
            attempt,
            condition,
        } => {
            output.push_str("{\"code\":");
            push_json_string(output, code);
            output.push_str(",\"retryable\":");
            output.push_str(if *retryable { "true" } else { "false" });
            output.push_str(",\"attempt\":");
            output.push_str(&attempt.to_string());
            output.push_str(",\"condition\":");
            push_json_string(output, condition);
            output.push('}');
        }
        Payload::Shutdown { status } => {
            output.push_str("{\"status\":");
            push_json_string(output, status.as_str());
            output.push('}');
        }
    }
}

fn parse_payload(input: &str, range: (usize, usize), message_type: MessageType) -> Result<Payload> {
    let raw = &input[range.0..range.1];
    let mut cursor = Cursor::new(raw);
    cursor.skip_ws();
    cursor.expect_byte(b'{')?;
    let mut command: Option<String> = None;
    let mut args_json: Option<String> = None;
    let mut fraction: Option<f64> = None;
    let mut detail: Option<String> = None;
    let mut units_done: Option<u64> = None;
    let mut units_total: Option<u64> = None;
    let mut checkpoint_id: Option<String> = None;
    let mut reusable: Option<bool> = None;
    let mut artifact_hash: Option<String> = None;
    let mut monotonic_ms: Option<u64> = None;
    let mut reason: Option<String> = None;
    let mut code: Option<String> = None;
    let mut retryable: Option<bool> = None;
    let mut attempt: Option<u8> = None;
    let mut condition: Option<String> = None;
    let mut status: Option<ShutdownStatus> = None;
    let mut seen = HashSet::<String>::new();
    if cursor.try_byte(b'}')? {
        return Err(ProtocolError::InvalidJson("payload object is empty".into()));
    }
    loop {
        let key = cursor.parse_string()?;
        if !seen.insert(key.clone()) {
            return Err(ProtocolError::DuplicateField(key));
        }
        cursor.expect_byte(b':')?;
        match (message_type, key.as_str()) {
            (MessageType::Command, "command") => command = Some(cursor.parse_bounded_string("command", 128)?),
            (MessageType::Command, "args") => {
                let (start, end) = cursor.parse_raw_value()?;
                let value = &raw[start..end];
                let mut args = Cursor::new(value);
                args.skip_ws();
                if args.peek() != Some(b'{') {
                    return Err(ProtocolError::InvalidType("command.args"));
                }
                args.parse_raw_value()?;
                args.finish()?;
                args_json = Some(value.to_owned());
            }
            (MessageType::Progress, "fraction") => fraction = Some(cursor.parse_f64()? ),
            (MessageType::Progress, "detail") => detail = Some(cursor.parse_bounded_string("progress.detail", 4096)?),
            (MessageType::Progress, "units_done") => units_done = Some(cursor.parse_u64()? ),
            (MessageType::Progress, "units_total") => units_total = Some(cursor.parse_u64()? ),
            (MessageType::Checkpoint, "checkpoint_id") => checkpoint_id = Some(cursor.parse_bounded_string("checkpoint_id", 256)?),
            (MessageType::Checkpoint, "reusable") => reusable = Some(cursor.parse_bool()? ),
            (MessageType::Checkpoint, "artifact_hash") => artifact_hash = Some(cursor.parse_bounded_string("artifact_hash", 256)?),
            (MessageType::Heartbeat, "monotonic_ms") => monotonic_ms = Some(cursor.parse_u64()? ),
            (MessageType::Cancel, "reason") => reason = Some(cursor.parse_bounded_string("cancel.reason", 4096)?),
            (MessageType::Failure, "code") => code = Some(cursor.parse_bounded_string("failure.code", 128)?),
            (MessageType::Failure, "retryable") => retryable = Some(cursor.parse_bool()? ),
            (MessageType::Failure, "attempt") => {
                let value = cursor.parse_u64()?;
                attempt = Some(u8::try_from(value).map_err(|_| ProtocolError::InvalidValue("failure attempt exceeds u8".into()))?);
            }
            (MessageType::Failure, "condition") => condition = Some(cursor.parse_bounded_string("failure.condition", 4096)?),
            (MessageType::Shutdown, "status") => status = Some(ShutdownStatus::parse(cursor.parse_string()?)? ),
            (_, unknown) => return Err(ProtocolError::UnknownField(unknown.to_owned())),
        }
        if cursor.try_byte(b'}')? {
            break;
        }
        cursor.expect_byte(b',')?;
    }
    cursor.finish()?;
    match message_type {
        MessageType::Command => Ok(Payload::Command {
            command: command.ok_or(ProtocolError::MissingField("command"))?,
            args_json: args_json.ok_or(ProtocolError::MissingField("args"))?,
        }),
        MessageType::Progress => Ok(Payload::Progress {
            fraction: fraction.ok_or(ProtocolError::MissingField("fraction"))?,
            detail,
            units_done,
            units_total,
        }),
        MessageType::Checkpoint => Ok(Payload::Checkpoint {
            checkpoint_id: checkpoint_id.ok_or(ProtocolError::MissingField("checkpoint_id"))?,
            reusable: reusable.ok_or(ProtocolError::MissingField("reusable"))?,
            artifact_hash,
        }),
        MessageType::Heartbeat => Ok(Payload::Heartbeat {
            monotonic_ms: monotonic_ms.ok_or(ProtocolError::MissingField("monotonic_ms"))?,
        }),
        MessageType::Cancel => Ok(Payload::Cancel {
            reason: reason.ok_or(ProtocolError::MissingField("reason"))?,
        }),
        MessageType::Failure => Ok(Payload::Failure {
            code: code.ok_or(ProtocolError::MissingField("code"))?,
            retryable: retryable.ok_or(ProtocolError::MissingField("retryable"))?,
            attempt: attempt.ok_or(ProtocolError::MissingField("attempt"))?,
            condition: condition.ok_or(ProtocolError::MissingField("condition"))?,
        }),
        MessageType::Shutdown => Ok(Payload::Shutdown {
            status: status.ok_or(ProtocolError::MissingField("status"))?,
        }),
    }
}

struct Cursor<'a> {
    input: &'a [u8],
    pos: usize,
}

impl<'a> Cursor<'a> {
    fn new(input: &'a str) -> Self {
        Self {
            input: input.as_bytes(),
            pos: 0,
        }
    }

    fn peek(&mut self) -> Option<u8> {
        self.skip_ws();
        self.input.get(self.pos).copied()
    }

    fn skip_ws(&mut self) {
        while matches!(self.input.get(self.pos), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.pos += 1;
        }
    }

    fn try_byte(&mut self, expected: u8) -> Result<bool> {
        self.skip_ws();
        if self.input.get(self.pos) == Some(&expected) {
            self.pos += 1;
            Ok(true)
        } else {
            Ok(false)
        }
    }

    fn expect_byte(&mut self, expected: u8) -> Result<()> {
        self.skip_ws();
        if self.input.get(self.pos) == Some(&expected) {
            self.pos += 1;
            Ok(())
        } else {
            Err(ProtocolError::InvalidJson(format!(
                "expected {:?} at byte {}",
                expected as char, self.pos
            )))
        }
    }

    fn finish(&mut self) -> Result<()> {
        self.skip_ws();
        if self.pos == self.input.len() {
            Ok(())
        } else {
            Err(ProtocolError::InvalidJson("trailing JSON bytes".into()))
        }
    }

    fn parse_string(&mut self) -> Result<String> {
        self.skip_ws();
        if self.input.get(self.pos) != Some(&b'"') {
            return Err(ProtocolError::InvalidType("string"));
        }
        self.pos += 1;
        let mut output = String::new();
        while self.pos < self.input.len() {
            let byte = self.input[self.pos];
            self.pos += 1;
            match byte {
                b'"' => return Ok(output),
                b'\\' => {
                    let escape = *self.input.get(self.pos).ok_or_else(|| ProtocolError::InvalidJson("unterminated escape".into()))?;
                    self.pos += 1;
                    match escape {
                        b'"' => output.push('"'),
                        b'\\' => output.push('\\'),
                        b'/' => output.push('/'),
                        b'b' => output.push('\u{08}'),
                        b'f' => output.push('\u{0c}'),
                        b'n' => output.push('\n'),
                        b'r' => output.push('\r'),
                        b't' => output.push('\t'),
                        b'u' => output.push(self.parse_unicode_escape()?),
                        _ => return Err(ProtocolError::InvalidJson("unknown string escape".into())),
                    }
                }
                byte if byte < 0x20 => return Err(ProtocolError::InvalidJson("control byte in string".into())),
                _byte => {
                    let start = self.pos - 1;
                    let tail = str::from_utf8(&self.input[start..])
                        .map_err(|_| ProtocolError::InvalidUtf8)?;
                    let character = tail
                        .chars()
                        .next()
                        .ok_or_else(|| ProtocolError::InvalidJson("invalid UTF-8 string".into()))?;
                    if character.len_utf8() > 1 {
                        self.pos = start + character.len_utf8();
                    }
                    output.push(character);
                }
            }
        }
        Err(ProtocolError::InvalidJson("unterminated string".into()))
    }

    fn parse_unicode_escape(&mut self) -> Result<char> {
        let high = self.parse_hex_quad()?;
        if (0xd800..=0xdbff).contains(&high) {
            if self.input.get(self.pos) != Some(&b'\\') || self.input.get(self.pos + 1) != Some(&b'u') {
                return Err(ProtocolError::InvalidJson("unpaired high surrogate".into()));
            }
            self.pos += 2;
            let low = self.parse_hex_quad()?;
            if !(0xdc00..=0xdfff).contains(&low) {
                return Err(ProtocolError::InvalidJson("invalid low surrogate".into()));
            }
            let codepoint = 0x1_0000 + ((high - 0xd800) << 10) + (low - 0xdc00);
            char::from_u32(codepoint).ok_or_else(|| ProtocolError::InvalidJson("invalid Unicode scalar".into()))
        } else if (0xdc00..=0xdfff).contains(&high) {
            Err(ProtocolError::InvalidJson("unpaired low surrogate".into()))
        } else {
            char::from_u32(high).ok_or_else(|| ProtocolError::InvalidJson("invalid Unicode scalar".into()))
        }
    }

    fn parse_hex_quad(&mut self) -> Result<u32> {
        if self.pos + 4 > self.input.len() {
            return Err(ProtocolError::InvalidJson("short Unicode escape".into()));
        }
        let mut value = 0u32;
        for _ in 0..4 {
            value = (value << 4) | hex_value(self.input[self.pos]).ok_or_else(|| ProtocolError::InvalidJson("invalid Unicode escape".into()))?;
            self.pos += 1;
        }
        Ok(value)
    }

    fn parse_bounded_string(&mut self, name: &'static str, max_chars: usize) -> Result<String> {
        let value = self.parse_string()?;
        validate_string(&value, name, max_chars)?;
        Ok(value)
    }

    fn parse_bool(&mut self) -> Result<bool> {
        self.skip_ws();
        if self.input.get(self.pos..self.pos + 4) == Some(b"true") {
            self.pos += 4;
            Ok(true)
        } else if self.input.get(self.pos..self.pos + 5) == Some(b"false") {
            self.pos += 5;
            Ok(false)
        } else {
            Err(ProtocolError::InvalidType("boolean"))
        }
    }

    fn parse_u64(&mut self) -> Result<u64> {
        let token = self.parse_number_token()?;
        if token.starts_with('-') || token.chars().any(|character| matches!(character, '.' | 'e' | 'E')) {
            return Err(ProtocolError::InvalidType("integer"));
        }
        token
            .parse::<u64>()
            .map_err(|_| ProtocolError::InvalidValue("integer exceeds u64".into()))
    }

    fn parse_f64(&mut self) -> Result<f64> {
        let token = self.parse_number_token()?;
        let value = token
            .parse::<f64>()
            .map_err(|_| ProtocolError::InvalidValue("invalid number".into()))?;
        if !value.is_finite() {
            return Err(ProtocolError::InvalidValue("number must be finite".into()));
        }
        Ok(value)
    }

    fn parse_number_token(&mut self) -> Result<String> {
        self.skip_ws();
        let start = self.pos;
        if self.input.get(self.pos) == Some(&b'-') {
            self.pos += 1;
        }
        match self.input.get(self.pos) {
            Some(b'0') => self.pos += 1,
            Some(byte @ b'1'..=b'9') => {
                let _ = byte;
                self.pos += 1;
                while matches!(self.input.get(self.pos), Some(b'0'..=b'9')) {
                    self.pos += 1;
                }
            }
            _ => return Err(ProtocolError::InvalidType("number")),
        }
        if self.input.get(self.pos) == Some(&b'.') {
            self.pos += 1;
            let fraction_start = self.pos;
            while matches!(self.input.get(self.pos), Some(b'0'..=b'9')) {
                self.pos += 1;
            }
            if self.pos == fraction_start {
                return Err(ProtocolError::InvalidJson("fraction requires digits".into()));
            }
        }
        if matches!(self.input.get(self.pos), Some(b'e' | b'E')) {
            self.pos += 1;
            if matches!(self.input.get(self.pos), Some(b'+' | b'-')) {
                self.pos += 1;
            }
            let exponent_start = self.pos;
            while matches!(self.input.get(self.pos), Some(b'0'..=b'9')) {
                self.pos += 1;
            }
            if self.pos == exponent_start {
                return Err(ProtocolError::InvalidJson("exponent requires digits".into()));
            }
        }
        Ok(String::from_utf8(self.input[start..self.pos].to_vec()).expect("number token is ASCII"))
    }

    fn parse_raw_value(&mut self) -> Result<(usize, usize)> {
        self.skip_ws();
        let start = self.pos;
        self.parse_value(0)?;
        Ok((start, self.pos))
    }

    fn parse_value(&mut self, depth: usize) -> Result<()> {
        if depth > MAX_JSON_DEPTH {
            return Err(ProtocolError::InvalidJson("JSON nesting is too deep".into()));
        }
        self.skip_ws();
        match self.input.get(self.pos).copied() {
            Some(b'"') => {
                self.parse_string()?;
                Ok(())
            }
            Some(b'{') => self.parse_object(depth + 1),
            Some(b'[') => self.parse_array(depth + 1),
            Some(b't') => {
                self.expect_literal(b"true")
            }
            Some(b'f') => {
                self.expect_literal(b"false")
            }
            Some(b'n') => {
                self.expect_literal(b"null")
            }
            Some(b'-' | b'0'..=b'9') => {
                self.parse_number_token()?;
                Ok(())
            }
            _ => Err(ProtocolError::InvalidJson("expected a JSON value".into())),
        }
    }

    fn parse_object(&mut self, depth: usize) -> Result<()> {
        self.expect_byte(b'{')?;
        let mut keys = HashSet::<String>::new();
        if self.try_byte(b'}')? {
            return Ok(());
        }
        loop {
            let key = self.parse_string()?;
            if !keys.insert(key.clone()) {
                return Err(ProtocolError::DuplicateField(key));
            }
            self.expect_byte(b':')?;
            self.parse_value(depth)?;
            if self.try_byte(b'}')? {
                return Ok(());
            }
            self.expect_byte(b',')?;
        }
    }

    fn parse_array(&mut self, depth: usize) -> Result<()> {
        self.expect_byte(b'[')?;
        if self.try_byte(b']')? {
            return Ok(());
        }
        loop {
            self.parse_value(depth)?;
            if self.try_byte(b']')? {
                return Ok(());
            }
            self.expect_byte(b',')?;
        }
    }

    fn expect_literal(&mut self, literal: &[u8]) -> Result<()> {
        self.skip_ws();
        if self.input.get(self.pos..self.pos + literal.len()) == Some(literal) {
            self.pos += literal.len();
            Ok(())
        } else {
            Err(ProtocolError::InvalidJson("invalid JSON literal".into()))
        }
    }
}

fn hex_value(value: u8) -> Option<u32> {
    match value {
        b'0'..=b'9' => Some((value - b'0') as u32),
        b'a'..=b'f' => Some((value - b'a' + 10) as u32),
        b'A'..=b'F' => Some((value - b'A' + 10) as u32),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const COMMAND: &str = r#"{"schema_version":1,"message_type":"command","message_id":"m1","job_id":"job","stage_id":"stage","sequence":1,"payload":{"command":"analyze","args":{"chunk":"0"}}}"#;

    fn envelope(message_type: MessageType, sequence: u64, payload: Payload) -> Envelope {
        Envelope::new(message_type, format!("m{sequence}"), "job", "stage", sequence, payload).unwrap()
    }

    #[test]
    fn strict_json_round_trip_and_reordered_fields() {
        let parsed = Envelope::from_json(COMMAND).unwrap();
        assert_eq!(parsed.message_type, MessageType::Command);
        assert_eq!(parsed.sequence, 1);
        let line = parsed.to_line().unwrap();
        assert_eq!(Envelope::from_line(&line).unwrap(), parsed);
        let reordered = r#"{"payload":{"args":{"chunk":"0"},"command":"analyze"},"sequence":1,"stage_id":"stage","job_id":"job","message_id":"m1","message_type":"command","schema_version":1}"#;
        assert_eq!(Envelope::from_json(reordered).unwrap(), parsed);
    }

    #[test]
    fn malformed_unknown_duplicate_and_numeric_fields_are_rejected() {
        assert!(matches!(Envelope::from_json(&COMMAND.replace("\"sequence\":1", "\"sequence\":1,\"sequence\":2")), Err(ProtocolError::DuplicateField(_))));
        assert!(matches!(Envelope::from_json(&COMMAND.replace("\"payload\":", "\"extra\":0,\"payload\":")), Err(ProtocolError::UnknownField(_))));
        assert!(matches!(Envelope::from_json(&COMMAND.replace("\"sequence\":1", "\"sequence\":\"1\"")), Err(ProtocolError::InvalidType(_))));
        assert!(matches!(Envelope::from_json(&COMMAND.replace("\"schema_version\":1", "\"schema_version\":2")), Err(ProtocolError::UnsupportedVersion(2))));
        assert!(matches!(Envelope::from_json(&COMMAND.replace("\"args\":{\"chunk\":\"0\"}", "\"args\":{\"x\":1,\"x\":2}")), Err(ProtocolError::DuplicateField(_))));
    }

    #[test]
    fn stream_enforces_sequence_heartbeats_and_terminal_state() {
        let heartbeat = envelope(MessageType::Heartbeat, 1, Payload::Heartbeat { monotonic_ms: 10 });
        let shutdown = envelope(MessageType::Shutdown, 2, Payload::Shutdown { status: ShutdownStatus::Completed });
        let mut stream = StreamValidator::new(100, 0).unwrap();
        stream.accept(&heartbeat).unwrap();
        let regression = envelope(MessageType::Heartbeat, 2, Payload::Heartbeat { monotonic_ms: 9 });
        assert!(matches!(stream.accept(&regression), Err(ProtocolError::HeartbeatRegression)));
        assert_eq!(stream.expected_sequence(), 2);
        assert!(stream.check_heartbeat(110).is_ok());
        assert!(matches!(stream.check_heartbeat(111), Err(ProtocolError::HeartbeatTimeout { .. })));
        stream.accept(&shutdown).unwrap();
        assert!(stream.terminal());
        assert!(matches!(stream.accept(&heartbeat), Err(ProtocolError::StreamTerminal)));
        let gap = envelope(MessageType::Heartbeat, 4, Payload::Heartbeat { monotonic_ms: 20 });
        let mut fresh = StreamValidator::new(100, 0).unwrap();
        assert!(matches!(fresh.accept(&gap), Err(ProtocolError::SequenceGap { .. })));
    }

    #[test]
    fn progress_is_bounded_and_coalesced() {
        assert!(ProgressBuffer::new(MAX_PROGRESS_BUFFER + 1).is_err());
        let mut buffer = ProgressBuffer::new(2).unwrap();
        buffer.push(envelope(MessageType::Progress, 1, Payload::Progress { fraction: 0.1, detail: None, units_done: None, units_total: None })).unwrap();
        buffer.push(Envelope::new(MessageType::Progress, "m2", "job", "other", 2, Payload::Progress { fraction: 0.2, detail: None, units_done: None, units_total: None }).unwrap()).unwrap();
        buffer.push(envelope(MessageType::Progress, 3, Payload::Progress { fraction: 0.3, detail: None, units_done: None, units_total: None })).unwrap();
        assert_eq!(buffer.len(), 2);
        assert_eq!(buffer.coalesced_count(), 1);
        buffer.push(Envelope::new(MessageType::Progress, "m4", "job", "third", 4, Payload::Progress { fraction: 0.4, detail: None, units_done: None, units_total: None }).unwrap()).unwrap();
        assert_eq!(buffer.dropped_count(), 1);
        assert_eq!(buffer.drain().len(), 2);
    }

    #[test]
    fn cancellation_requires_checkpoint_and_retry_changes_condition() {
        let mut cancel = CancellationController::default();
        assert!(matches!(cancel.complete(), Err(ProtocolError::UnsafeCancellation)));
        cancel.request("user").unwrap();
        cancel.checkpoint("safe-1").unwrap();
        cancel.complete().unwrap();
        assert_eq!(cancel.state(), CancellationState::Completed);
        assert!(matches!(cancel.request("late"), Err(ProtocolError::CancellationTooLate)));

        let mut retry = RetryBudget::new(2).unwrap();
        assert_eq!(retry.record_retry("network-reset").unwrap(), 1);
        assert!(matches!(retry.record_retry("network-reset"), Err(ProtocolError::RetryConditionUnchanged)));
        assert_eq!(retry.record_retry("worker-restarted").unwrap(), 2);
        assert!(matches!(retry.record_retry("third"), Err(ProtocolError::RetryExhausted)));
    }

    #[test]
    fn all_payloads_round_trip() {
        let payloads = [
            (MessageType::Progress, Payload::Progress { fraction: 0.5, detail: Some("half".into()), units_done: Some(1), units_total: Some(2) }),
            (MessageType::Checkpoint, Payload::Checkpoint { checkpoint_id: "cp".into(), reusable: true, artifact_hash: Some("sha".into()) }),
            (MessageType::Heartbeat, Payload::Heartbeat { monotonic_ms: 12 }),
            (MessageType::Cancel, Payload::Cancel { reason: "stop".into() }),
            (MessageType::Failure, Payload::Failure { code: "E_IO".into(), retryable: true, attempt: 1, condition: "changed".into() }),
            (MessageType::Shutdown, Payload::Shutdown { status: ShutdownStatus::Failed }),
        ];
        for (index, (kind, payload)) in payloads.into_iter().enumerate() {
            let value = envelope(kind, (index + 1) as u64, payload);
            assert_eq!(Envelope::from_json(&value.to_json().unwrap()).unwrap(), value);
        }
    }

    #[test]
    fn shared_jsonl_fixture_is_accepted_by_rust_reference() {
        let fixture = include_str!("../../../../tests/worker_protocol/fixtures/valid.jsonl");
        let mut stream = StreamValidator::new(100, 0).unwrap();
        let mut count = 0;
        for line in fixture.lines() {
            let envelope = Envelope::from_line(line.as_bytes()).unwrap();
            stream.accept(&envelope).unwrap();
            count += 1;
        }
        assert_eq!(count, 7);
        assert!(stream.terminal());
        assert_eq!(stream.expected_sequence(), 8);
    }
}
