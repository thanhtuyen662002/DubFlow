import type { QueueController, QueueJob } from "../queue/model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type TranslationEngine = "argos" | "hachimi";

/** Hachimi only translates Chinese, so it needs an explicit Chinese source; Auto is not enough. */
export function hachimiAllowed(sourceLanguage: string | null | undefined): boolean {
  return sourceLanguage === "zh";
}

/** The translator a job actually uses: a stored Hachimi choice counts only for a Chinese source. */
export function effectiveTranslator(job: Pick<QueueJob, "sourceLanguage" | "translationEngine">): TranslationEngine {
  return job.translationEngine === "hachimi" && hachimiAllowed(job.sourceLanguage) ? "hachimi" : "argos";
}

/** Saved defaults never carry Hachimi without an explicit Chinese source. */
export function translatorDefaults<T extends { sourceLanguage?: string; translationEngine?: string }>(defaults: T): T {
  if (defaults.translationEngine === undefined || hachimiAllowed(defaults.sourceLanguage)) return defaults;
  const { translationEngine: _dropped, ...rest } = defaults;
  return rest as T;
}

/**
 * Set a queued job's source language from the picker value. Hachimi survives only
 * while it stays in effect: leaving Chinese drops it, and a stale choice stored
 * with Auto/English is not revived by switching to Chinese.
 */
export function applySourceLanguage(queue: QueueController, jobId: string, value: string): void {
  const language = value === "zh" || value === "en" ? value : null;
  const before = queue.getSnapshot().jobs.find((candidate) => candidate.id === jobId);
  const hachimiInEffect = before !== undefined && effectiveTranslator(before) === "hachimi";
  queue.setSourceLanguage(jobId, language);
  if (before?.translationEngine === "hachimi" && !(hachimiInEffect && hachimiAllowed(language))) {
    queue.setTranslationEngine(jobId, "argos");
  }
}

/** The exact `start_job` arguments for a job; Auto stays "auto", never a guessed language. */
export function startJobRequest(job: QueueJob, outputRoot: string | null): Record<string, unknown> {
  const sourceLanguage = job.sourceLanguage ?? "auto";
  return {
    jobId: job.id, sourcePath: job.sourcePath, outputDir: job.outputDir ?? null, outputRoot: job.outputDir ? null : outputRoot,
    enableDubbing: job.dubbing.enabled, ttsVoiceId: job.dubbing.voiceId, burnIn: job.burnIn ?? true,
    sourceLanguage, subtitleBox: job.subtitleBox ?? false,
    translationEngine: effectiveTranslator(job),
    blurRegions: job.blurRegions ?? null,
    blurAuto: job.blurAuto ?? false,
    multiVoice: job.multiVoice ?? false, speakerVoices: job.speakerVoices ?? null,
    separateBackground: job.separateBackground ?? false,
  };
}

/**
 * The translator to apply: Argos unless Hachimi is requested for a Chinese source,
 * or null when the user declines Hachimi's licence acknowledgement.
 */
export function chooseTranslator(
  requested: string,
  sourceLanguage: string | null | undefined,
  acknowledge: () => boolean,
): TranslationEngine | null {
  if (requested !== "hachimi" || !hachimiAllowed(sourceLanguage)) return "argos";
  return acknowledge() ? "hachimi" : null;
}

/** Reviewed text for each cue that differs from its machine translation. */
export function reviewOverrides(
  cues: readonly { cue_id: string; machine_text: string }[],
  draftOf: (cueId: string) => string,
): Record<string, string> {
  const overrides: Record<string, string> = {};
  for (const cue of cues) {
    const text = draftOf(cue.cue_id).trim();
    if (text !== cue.machine_text.trim()) overrides[cue.cue_id] = text;
  }
  return overrides;
}

/**
 * Save reviewed text into a new output folder, then queue a revision of the
 * completed job there. A failed save leaves the queue and the original output untouched.
 */
export async function saveReviewRevision(
  invoke: Invoke,
  queue: QueueController,
  job: QueueJob,
  overrides: Record<string, string>,
): Promise<string> {
  if (!job.outputPath) throw new Error("Only a completed job with output can be reviewed");
  const outputDir = await invoke<string>("save_review", { outputPath: job.outputPath, overrides });
  return queue.reviseJob(job.id, outputDir);
}
