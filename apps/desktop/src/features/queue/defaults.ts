import { isBlurRegionList, type BlurRegion } from "../blur/region.ts";
import { translatorDefaults } from "../translation_review/flow.ts";

/** Options a newly added video starts with. */
export type JobDefaults = {
  dubbing: boolean;
  voiceId: string | null;
  burnIn: boolean;
  blurAuto: boolean;
  separateBackground: boolean;
  multiVoice: boolean;
  sourceLanguage?: "zh" | "en";
  translationEngine?: "hachimi";
  blurRegions?: BlurRegion[];
};

export const DEFAULTS_KEY = "dubflow.defaults.v2";
export const LEGACY_DEFAULTS_KEY = "dubflow.defaults.v1";

/** A first-time user gets a dubbed, subtitled video with the source captions covered. */
export const FIRST_RUN_DEFAULTS: JobDefaults = {
  dubbing: true, voiceId: null, burnIn: true, blurAuto: true, separateBackground: true, multiVoice: false,
};

function parse(serialized: string | null): Record<string, unknown> | null {
  if (!serialized) return null;
  try {
    const value = JSON.parse(serialized) as unknown;
    return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

/**
 * Stored defaults: the v2 record, else the v1 record migrated. v1 omitted
 * false flags, so a missing blurAuto / separateBackground there means "never
 * chosen" and takes the new default (on), unless manual blur regions were saved.
 */
export function readJobDefaults(v2: string | null, v1: string | null): JobDefaults | null {
  const current = parse(v2);
  const legacy = current ? null : parse(v1);
  const value = current ?? legacy;
  if (!value || typeof value.dubbing !== "boolean" || typeof value.burnIn !== "boolean") return null;
  const blurRegions = isBlurRegionList(value.blurRegions) ? value.blurRegions : undefined;
  const flag = (key: string, missing: boolean): boolean => typeof value[key] === "boolean" ? value[key] as boolean : missing;
  return translatorDefaults<JobDefaults>({
    dubbing: value.dubbing,
    voiceId: typeof value.voiceId === "string" ? value.voiceId : null,
    burnIn: value.burnIn,
    blurAuto: blurRegions ? false : flag("blurAuto", legacy !== null),
    separateBackground: flag("separateBackground", legacy !== null),
    multiVoice: flag("multiVoice", false),
    ...(value.sourceLanguage === "zh" || value.sourceLanguage === "en" ? { sourceLanguage: value.sourceLanguage } : {}),
    ...(value.translationEngine === "hachimi" ? { translationEngine: "hachimi" as const } : {}),
    ...(blurRegions ? { blurRegions } : {}),
  });
}

/** The v2 record for a job's current options, with every flag explicit. */
export function serializeJobDefaults(job: {
  dubbing: { enabled: boolean; voiceId: string | null };
  burnIn?: boolean; blurAuto?: boolean; separateBackground?: boolean; multiVoice?: boolean;
  sourceLanguage?: "zh" | "en"; translationEngine?: "hachimi"; blurRegions?: BlurRegion[];
}): string {
  const defaults: JobDefaults = translatorDefaults<JobDefaults>({
    dubbing: job.dubbing.enabled,
    voiceId: job.dubbing.voiceId,
    burnIn: job.burnIn ?? true,
    blurAuto: job.blurAuto === true,
    separateBackground: job.separateBackground === true,
    multiVoice: job.multiVoice === true,
    ...(job.sourceLanguage ? { sourceLanguage: job.sourceLanguage } : {}),
    ...(job.translationEngine ? { translationEngine: job.translationEngine } : {}),
    ...(job.blurRegions?.length ? { blurRegions: job.blurRegions } : {}),
  });
  return JSON.stringify(defaults);
}
