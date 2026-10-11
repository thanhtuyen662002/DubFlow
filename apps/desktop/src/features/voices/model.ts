export type VoiceChoice = {
  voice_id: string;
  name: string;
  gender: string;
  accent: string;
  style: string;
  description: string;
  voice_version: string;
  approved: true;
};

export type VoiceCatalog = { default_voice_id: string; voices: VoiceChoice[] };
export type DubbingOptions = { enabled: boolean; voiceId: string | null };

export function isVoiceId(value: unknown): value is string {
  return typeof value === "string" && /^[a-z0-9-]{1,96}$/.test(value);
}

export function isDubbingOptions(value: unknown): value is DubbingOptions {
  if (!value || typeof value !== "object") return false;
  const options = value as Partial<DubbingOptions>;
  return typeof options.enabled === "boolean" &&
    (options.voiceId === null ? !options.enabled : isVoiceId(options.voiceId));
}

export function parseVoiceCatalog(value: unknown): VoiceCatalog {
  if (!value || typeof value !== "object") throw new Error("Danh mục giọng không hợp lệ");
  const catalog = value as Partial<VoiceCatalog>;
  if (!isVoiceId(catalog.default_voice_id) || !Array.isArray(catalog.voices) ||
      catalog.voices.length < 1 || catalog.voices.length > 64) {
    throw new Error("Danh mục giọng không hợp lệ");
  }
  const ids = new Set<string>();
  for (const voice of catalog.voices) {
    if (!voice || !isVoiceId(voice.voice_id) || ids.has(voice.voice_id) || voice.approved !== true ||
        [voice.name, voice.gender, voice.accent, voice.style, voice.description, voice.voice_version]
          .some((field) => typeof field !== "string" || field.length < 1 || field.length > 160)) {
      throw new Error("Danh mục giọng không hợp lệ");
    }
    ids.add(voice.voice_id);
  }
  if (!ids.has(catalog.default_voice_id)) throw new Error("Giọng mặc định không có trong danh mục");
  return structuredClone(catalog as VoiceCatalog);
}

export function filterVoices(catalog: VoiceCatalog, filters: { gender?: string; accent?: string; style?: string }): VoiceChoice[] {
  return catalog.voices.filter((voice) =>
    (!filters.gender || voice.gender === filters.gender) &&
    (!filters.accent || voice.accent === filters.accent) &&
    (!filters.style || voice.style === filters.style));
}

export function voiceLabel(voice: VoiceChoice): string {
  return `${voice.name} · ${voice.gender} · miền ${voice.accent} · ${voice.style}`;
}

export function dubbingOptions(catalog: VoiceCatalog, enabled: boolean, voiceId: string | null): DubbingOptions {
  const selected = voiceId ?? catalog.default_voice_id;
  if (!catalog.voices.some((voice) => voice.voice_id === selected)) throw new Error("Giọng đã chọn không có trong danh mục");
  return { enabled, voiceId: selected };
}
