import { isVoiceId } from "../voices/model.ts";
import type { JobDefaults } from "./defaults.ts";

/**
 * Dubbing requests an agent leaves for the queue through DubFlow's MCP server (ADR-0058, Issue #998).
 * The host's `take_inbox` (inbox.rs) already checked each one; this is the frontend's own check of what came back.
 */
export type InboxOptions = {
  dub: boolean | null;
  voice_id: string | null;
  source_language: "auto" | "zh" | "en" | null;
  burn_in: boolean | null;
  blur_auto: boolean | null;
  separate_background: boolean | null;
  multi_voice: boolean | null;
  translation_engine: "argos" | "hachimi" | null;
};

export type InboxRequest = { id: string; source_path: string; title: string; options: InboxOptions };

/** The ids the MCP server gives (dubbing.py ID): an agent's video in Hàng đợi. */
export const AGENT_JOB_ID = /^mcp-[0-9a-z-]{8,64}$/;

export function isAgentJob(job: { id: string } | undefined): boolean {
  return !!job && AGENT_JOB_ID.test(job.id);
}

function flag(value: unknown): boolean | null | undefined {
  return value === null || value === undefined ? null : typeof value === "boolean" ? value : undefined;
}

function choice<T extends string>(value: unknown, allowed: readonly T[]): T | null | undefined {
  if (value === null || value === undefined) return null;
  return allowed.includes(value as T) ? value as T : undefined;
}

function parseRequest(value: unknown): InboxRequest | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  // An edit request (ADR-0059) is Chỉnh sửa's, not the queue's.
  if (item.kind !== undefined && item.kind !== "dubbing") return null;
  const raw = (item.options && typeof item.options === "object" ? item.options : {}) as Record<string, unknown>;
  if (typeof item.id !== "string" || !AGENT_JOB_ID.test(item.id) || typeof item.source_path !== "string" || !item.source_path.trim()) return null;
  const options: InboxOptions = {
    dub: flag(raw.dub) as boolean | null,
    voice_id: raw.voice_id === null || raw.voice_id === undefined ? null : isVoiceId(raw.voice_id) ? raw.voice_id : null,
    source_language: choice(raw.source_language, ["auto", "zh", "en"] as const) as InboxOptions["source_language"],
    burn_in: flag(raw.burn_in) as boolean | null,
    blur_auto: flag(raw.blur_auto) as boolean | null,
    separate_background: flag(raw.separate_background) as boolean | null,
    multi_voice: flag(raw.multi_voice) as boolean | null,
    translation_engine: choice(raw.translation_engine, ["argos", "hachimi"] as const) as InboxOptions["translation_engine"],
  };
  if (Object.values(options).some((option) => option === undefined)) return null;
  const title = typeof item.title === "string" ? item.title.replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, 200) : "";
  return { id: item.id, source_path: item.source_path, title, options };
}

export function parseInboxRequests(value: unknown): InboxRequest[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 20).map(parseRequest).filter((request): request is InboxRequest => request !== null);
}

/** What a request's job gets: each option the agent left out is the user's own default. */
export type AgentJobPlan = {
  dubbing: boolean;
  voiceId: string | null;
  sourceLanguage: "zh" | "en" | null;
  burnIn: boolean;
  blurAuto: boolean;
  separateBackground: boolean;
  multiVoice: boolean;
  translationEngine: "argos" | "hachimi";
  mastering: JobDefaults["mastering"] | null;
};

export function agentJobPlan(options: InboxOptions, defaults: JobDefaults): AgentJobPlan {
  const language = options.source_language === null ? defaults.sourceLanguage ?? null
    : options.source_language === "auto" ? null : options.source_language;
  return {
    dubbing: options.dub ?? defaults.dubbing,
    voiceId: options.voice_id ?? defaults.voiceId,
    sourceLanguage: language,
    burnIn: options.burn_in ?? defaults.burnIn,
    // Manual blur rectangles belong to one layout of the frame: an agent's video gets automatic blur or none.
    blurAuto: options.blur_auto ?? (defaults.blurAuto || !!defaults.blurRegions?.length),
    separateBackground: options.separate_background ?? defaults.separateBackground,
    multiVoice: options.multi_voice ?? defaults.multiVoice,
    translationEngine: options.translation_engine ?? defaults.translationEngine ?? "argos",
    mastering: defaults.mastering ?? null,
  };
}
