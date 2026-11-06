/** Cài đặt → Tài nguyên máy: how many CPU threads one task may use. */

export type ThreadMode = "auto" | "all" | "custom";
export interface Machine { threads: number; recommended: number; memory_gb: number | null; gpu: string | null }
export interface Performance { mode: ThreadMode; threads: number }

export const PERFORMANCE_KEY = "dubflow.performance.v1";

/** The saved choice, or null on first run / unreadable storage (then the machine's default is saved). */
export function readPerformance(text: string | null): Performance | null {
  if (!text) return null;
  try {
    const value = JSON.parse(text) as Partial<Performance>;
    const mode = value.mode;
    if (mode !== "auto" && mode !== "all" && mode !== "custom") return null;
    const threads = Number(value.threads);
    return { mode, threads: Number.isInteger(threads) && threads > 0 ? threads : 1 };
  } catch {
    return null;
  }
}

/** First run: the machine's own suggestion, saved as "Tự động". */
export function firstRun(machine: Machine): Performance {
  return { mode: "auto", threads: clamp(machine.recommended, machine) };
}

export function clamp(threads: number, machine: Machine): number {
  return Math.min(Math.max(1, Math.round(Number.isFinite(threads) ? threads : 1)), Math.max(1, machine.threads));
}

/** The number the host passes on: 0 means every logical processor. */
export function hostThreads(choice: Performance, machine: Machine | null): number {
  if (choice.mode === "all") return 0;
  if (!machine) return choice.mode === "custom" ? Math.max(1, choice.threads) : 0;
  return choice.mode === "auto" ? clamp(machine.recommended, machine) : clamp(choice.threads, machine);
}

/** "Máy này: 12 luồng CPU · 16 GB RAM · NVIDIA GeForce RTX 3060 (12 GB)". */
export function describeMachine(machine: Machine): string {
  const parts = [`${machine.threads} luồng CPU`];
  if (machine.memory_gb) parts.push(`${machine.memory_gb} GB RAM`);
  parts.push(machine.gpu ? `GPU ${machine.gpu.replace(/^NVIDIA\s+/i, "NVIDIA ")}` : "không thấy GPU NVIDIA (render bằng CPU)");
  return `Máy này: ${parts.join(" · ")}`;
}

/** What the chosen mode means right now, under the picker. */
export function describeChoice(choice: Performance, machine: Machine | null): string {
  const total = machine?.threads;
  if (choice.mode === "all") return total ? `Dùng cả ${total} luồng: nhanh nhất, máy có thể chậm khi làm việc khác.` : "Dùng mọi luồng CPU của máy.";
  const threads = hostThreads(choice, machine);
  const of = total ? `/${total}` : "";
  return choice.mode === "auto"
    ? `Tự động: tối đa ${threads}${of} luồng, chừa lại cho Windows và việc khác.`
    : `Tối đa ${threads}${of} luồng cho mỗi tác vụ.`;
}
