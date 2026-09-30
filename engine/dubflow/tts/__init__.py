"""Public TTS adapter boundary for DubFlow."""

from .adapter import (
    AudioMetrics,
    DeterministicFixtureEngine,
    EngineCapabilities,
    EngineHealth,
    EngineSynthesis,
    LocalTtsAdapter,
    PCM_FORMAT,
    ResourceProfile,
    TARGET_LANGUAGE,
    TTS_CONTRACT_VERSION,
    TtsArtifact,
    TtsBackendError,
    TtsCheckpoint,
    TtsConfig,
    TtsDocument,
    TtsEngine,
    TtsError,
    TtsFailure,
    TtsInput,
    TtsProvenance,
    TtsRequest,
    TtsStageError,
    VoiceProfile,
    approved_default_voice,
    map_interval_to_samples,
    map_timepoint_to_sample,
    parse_tts_json,
    validate_tts_document,
)
from .production import (
    APPROVED_LICENSE_ID,
    BuiltinVietnameseTtsEngine,
    VOICE_MANIFEST_KEY,
    VOICE_PACK_RELATIVE_PATH,
    VOICE_PACK_SCHEMA_VERSION,
    VoicePack,
    load_production_voice,
)

__all__ = [name for name in globals() if not name.startswith("_")]
