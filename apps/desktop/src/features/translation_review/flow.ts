import type { QueueController, QueueJob } from "../queue/model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type TranslationEngine = "argos" | "hachimi";

/** The exact `start_job` arguments for a job; Auto stays "auto", never a guessed language. */
export function startJobRequest(job: QueueJob, outputRoot: string | null): Record<string, unknown> {
  const sourceLanguage = job.sourceLanguage ?? "auto";
  return {
    jobId: job.id, sourcePath: job.sourcePath, outputDir: job.outputDir ?? null, outputRoot: job.outputDir ? null : outputRoot,
    enableDubbing: job.dubbing.enabled, ttsVoiceId: job.dubbing.voiceId, burnIn: job.burnIn ?? true,
    sourceLanguage, subtitleBox: job.subtitleBox ?? false,
    // Hachimi only translates Chinese; an English source always uses Argos.
    translationEngine: job.translationEngine === "hachimi" && sourceLanguage !== "en" ? "hachimi" : "argos",
    blurRegions: job.blurRegions ?? null,
    blurAuto: job.blurAuto ?? false,
    multiVoice: job.multiVoice ?? false, speakerVoices: job.speakerVoices ?? null,
    separateBackground: job.separateBackground ?? false,
  };
}

/** The translator to apply, or null when the user declines Hachimi's licence acknowledgement. */
export function chooseTranslator(requested: string, acknowledge: () => boolean): TranslationEngine | null {
  if (requested !== "hachimi") return "argos";
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
