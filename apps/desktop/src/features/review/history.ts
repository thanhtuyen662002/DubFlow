export const REVIEW_HISTORY_LIMIT = 100;

type CueDraft = { cue_id: string; text: string };
type EditSession = { cueId: string; previousText: string };

/** In-memory drafts only; each entry restores one cue, never a whole review. */
export class ReviewDraftHistory {
  private originals = new Map<string, string>();
  private drafts = new Map<string, string>();
  private history: EditSession[] = [];
  private active: EditSession | null = null;

  constructor(cues: readonly CueDraft[] = []) {
    this.reset(cues);
  }

  /** Start a fresh review after loading a job, saving, or discarding. */
  reset(cues: readonly CueDraft[] = []): void {
    this.originals = new Map(cues.map((cue) => [cue.cue_id, cue.text]));
    this.drafts.clear();
    this.history = [];
    this.active = null;
  }

  draftOf(cueId: string): string {
    const text = this.drafts.get(cueId) ?? this.originals.get(cueId);
    if (text === undefined) throw new Error("unknown review cue: " + cueId);
    return text;
  }

  beginEdit(cueId: string): void {
    if (this.active?.cueId === cueId) return;
    this.endEdit();
    this.active = { cueId, previousText: this.draftOf(cueId) };
  }

  updateDraft(cueId: string, text: string): void {
    this.beginEdit(cueId);
    this.drafts.set(cueId, text);
  }

  endEdit(cueId?: string): void {
    if (!this.active || (cueId !== undefined && this.active.cueId !== cueId)) return;
    this.remember(this.active);
    this.active = null;
  }

  /** A machine-translation revert is separate from the preceding typing session. */
  replaceDraft(cueId: string, text: string): void {
    this.endEdit();
    const previousText = this.draftOf(cueId);
    this.drafts.set(cueId, text);
    this.remember({ cueId, previousText });
  }

  get canUndo(): boolean {
    return this.history.length > 0 || (this.active !== null
      && this.draftOf(this.active.cueId) !== this.active.previousText);
  }

  get isDirty(): boolean {
    return [...this.originals].some(([cueId, text]) => this.draftOf(cueId).trim() !== text.trim());
  }

  get canSave(): boolean {
    return this.isDirty && [...this.originals.keys()].every((cueId) => this.draftOf(cueId).trim() !== "");
  }

  undo(): string | null {
    this.endEdit();
    const previous = this.history.pop();
    if (!previous) return null;
    this.drafts.set(previous.cueId, previous.previousText);
    return previous.cueId;
  }

  private remember(session: EditSession): void {
    if (session.previousText === this.draftOf(session.cueId)) return;
    this.history.push(session);
    if (this.history.length > REVIEW_HISTORY_LIMIT) this.history.shift();
  }
}
