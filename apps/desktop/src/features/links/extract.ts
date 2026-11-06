/** Links found in pasted text: one per line, or embedded in share messages
 * such as Douyin's "复制打开抖音… https://v.douyin.com/xyz/ …". */
export const MAX_LINKS = 50;
export const MAX_LINK_BYTES = 2048;

// Stop at whitespace, quotes, angle brackets and full-width punctuation that
// share messages put right after the link, and where the next link starts
// when two were pasted back to back.
const LINK = /https?:\/\/(?:(?!https?:\/\/)[^\s<>"'，。！？、；：（）【】《》])+/giu;

// A Bilibili video id on its own ("BV" + 10 letters/digits), as people copy
// it from the page; only used when the text has no web address at all.
const BILIBILI_ID = /(?<![0-9A-Za-z])BV[0-9A-Za-z]{10}(?![0-9A-Za-z])/g;

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
  if (links.length === 0 && tooLong.length === 0) {
    for (const [id] of text.matchAll(BILIBILI_ID)) {
      const link = `https://www.bilibili.com/video/${id}/`;
      if (seen.has(link)) continue;
      seen.add(link);
      if (links.length < MAX_LINKS) links.push(link);
      else dropped += 1;
    }
  }
  return { links, tooLong, dropped };
}
