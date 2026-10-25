/** Links found in pasted text: one per line, or embedded in share messages
 * such as Douyin's "复制打开抖音… https://v.douyin.com/xyz/ …". */
export const MAX_LINKS = 50;
export const MAX_LINK_BYTES = 2048;

// Stop at whitespace, quotes, angle brackets and full-width punctuation that
// share messages put right after the link.
const LINK = /https?:\/\/[^\s<>"'，。！？、；：（）【】《》]+/giu;

export type ExtractedLinks = { links: string[]; tooLong: string[]; dropped: number };

export function extractLinks(text: string): ExtractedLinks {
  const encoder = new TextEncoder();
  const seen = new Set<string>();
  const links: string[] = [];
  const tooLong: string[] = [];
  let dropped = 0;
  for (const match of text.matchAll(LINK)) {
    // Trailing ASCII punctuation belongs to the sentence, not the link.
    const link = match[0].replace(/[.,;:!?)\]]+$/, "");
    if (seen.has(link)) continue;
    seen.add(link);
    if (encoder.encode(link).length > MAX_LINK_BYTES) {
      tooLong.push(link.slice(0, 80));
    } else if (links.length < MAX_LINKS) {
      links.push(link);
    } else {
      dropped += 1;
    }
  }
  return { links, tooLong, dropped };
}
