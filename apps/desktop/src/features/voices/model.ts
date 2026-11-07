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

/** The voices by region (Bắc, Trung, Nam, then any other), each region's voices in catalog order. */
export function voicesByRegion(catalog: VoiceCatalog): Array<{ region: string; voices: VoiceChoice[] }> {
  const order = ["Bắc", "Trung", "Nam"];
  const regions = [...new Set(catalog.voices.map((voice) => voice.accent))]
    .sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99));
  return regions.map((region) => ({ region, voices: catalog.voices.filter((voice) => voice.accent === region) }));
}

/** A voice <select>'s options grouped "Miền Bắc / Trung / Nam", so a regional voice is found at a glance. */
export function voiceOptionGroups(catalog: VoiceCatalog, selected: string | null = catalog.default_voice_id): HTMLOptGroupElement[] {
  return voicesByRegion(catalog).map(({ region, voices }) => {
    const group = document.createElement("optgroup");
    group.label = `Miền ${region} (${voices.length} giọng)`;
    group.append(...voices.map((voice) => {
      const option = document.createElement("option");
      option.value = voice.voice_id;
      option.textContent = `${voice.name} · ${voice.gender} · ${voice.style}`;
      option.selected = voice.voice_id === selected;
      return option;
    }));
    return group;
  });
}
