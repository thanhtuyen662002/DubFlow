/**
 * The fonts the editor burns text with (Phụ đề, Issue #414, and Chữ, Issue
 * #422). The engine (engine/dubflow/subtitle/style.py) and the host
 * (edit.rs SUBTITLE_FONTS) keep the same list.
 */

/** Stock Windows families covering every Vietnamese letter (Georgia, Trebuchet MS, Arial Black, Impact and Bahnschrift do not). */
export const SUBTITLE_FONTS = [
  "Arial", "Segoe UI", "Tahoma", "Verdana", "Times New Roman", "Calibri", "Cambria",
  "Palatino Linotype", "Constantia", "Corbel", "Candara", "Segoe UI Black",
] as const;
export type SubtitleFont = typeof SUBTITLE_FONTS[number];

/**
 * Font size → CSS em: libass sizes a font so that its Windows ascent plus
 * descent equals the ASS size, while CSS sizes the em square.
 */
export const EM_SCALE: Readonly<Record<SubtitleFont, number>> = {
  "Arial": 0.895, "Segoe UI": 0.752, "Tahoma": 0.828, "Verdana": 0.823, "Times New Roman": 0.903, "Calibri": 0.819, "Cambria": 0.853,
  "Palatino Linotype": 0.741, "Constantia": 0.819, "Corbel": 0.819, "Candara": 0.819, "Segoe UI Black": 0.752,
};

export function isFont(value: unknown): value is SubtitleFont {
  return typeof value === "string" && (SUBTITLE_FONTS as readonly string[]).includes(value);
}
