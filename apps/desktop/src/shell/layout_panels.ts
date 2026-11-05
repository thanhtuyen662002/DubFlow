/**
 * Every page's side panels become resizable and foldable (see panels.ts).
 * Called once after the pages have built their markup.
 */
import { mountPanel, refitPanels } from "./panels.ts";

/** The list turns into a drawer under this window width (styles.css). */
const DRAWER_BELOW = 1200;
/** The player or the main area keeps at least this much room. */
const MAIN_MIN_W = 380;
const MAIN_MIN_H = 220;

function width(element: Element | null): number {
  if (!element) return 0;
  const style = getComputedStyle(element);
  if (style.display === "none" || style.position === "absolute") return 0;
  return element.getBoundingClientRect().width;
}

/** A three-column page (list | player | details): one side may grow while the player keeps its room. */
function sideMax(screen: HTMLElement, other: string, hardMax: number): () => number {
  return () => {
    const total = screen.getBoundingClientRect().width || window.innerWidth;
    return Math.min(hardMax, total - width(screen.querySelector(`:scope > ${other}`)) - MAIN_MIN_W);
  };
}

function mountThreeColumns(prefix: string, screen: HTMLElement | null): void {
  if (!screen) return;
  const list = screen.querySelector<HTMLElement>(":scope > .queue-col");
  const details = screen.querySelector<HTMLElement>(":scope > .inspector");
  if (list) {
    mountPanel({
      key: `${prefix}.list`, panel: list, edge: "right", label: "danh sách",
      min: 200, max: sideMax(screen, ".inspector", 560), fallback: () => 284,
      // Under 1200 px the list is a drawer the "Danh sách" button opens; it only resizes there.
      foldable: () => window.innerWidth >= DRAWER_BELOW,
    });
  }
  if (details) {
    mountPanel({
      key: `${prefix}.details`, panel: details, edge: "left", label: "bảng chi tiết",
      min: 280, max: sideMax(screen, ".queue-col", 640), fallback: () => 344,
    });
  }
}

export function mountLayoutPanels(): void {
  const queueScreen = document.querySelector<HTMLElement>("#queue-content");
  mountThreeColumns("queue", queueScreen);
  mountThreeColumns("compilation", document.querySelector("#compilation-screen"));
  mountThreeColumns("docu", document.querySelector("#docu-screen"));

  // Chỉnh sửa: the tool panel beside the player and the timeline under both.
  const editPanel = document.querySelector<HTMLElement>("#edit-panel");
  if (queueScreen && editPanel) {
    mountPanel({
      key: "edit.panel", panel: editPanel, host: queueScreen, cssVar: "--edit-panel-w", edge: "left", label: "bảng công cụ",
      min: 280, max: () => Math.min(720, (queueScreen.getBoundingClientRect().width || window.innerWidth) - MAIN_MIN_W),
      fallback: () => (window.innerWidth <= 1000 ? 300 : 344),
    });
  }
  const timeline = document.querySelector<HTMLElement>("#edit-timeline");
  if (queueScreen && timeline) {
    mountPanel({
      key: "edit.timeline", panel: timeline, edge: "top", label: "dòng thời gian",
      min: 110, max: () => Math.max(110, (queueScreen.getBoundingClientRect().height || window.innerHeight) - MAIN_MIN_H),
      fallback: () => timeline.getBoundingClientRect().height || 170,
    });
  }

  // Phim tài liệu: the preview above the script.
  const docuPlayer = document.querySelector<HTMLElement>("#docu-player");
  const docuWorkspace = docuPlayer?.parentElement;
  if (docuPlayer && docuWorkspace) {
    mountPanel({
      key: "docu.preview", panel: docuPlayer, edge: "bottom", label: "khung xem trước",
      min: 140, max: () => Math.max(140, docuWorkspace.getBoundingClientRect().height - 160),
      fallback: () => 260,
    });
  }

  // Khám phá: the trend list beside the results.
  const trends = document.querySelector<HTMLElement>(".trend-panel");
  const discoverBody = trends?.parentElement;
  if (trends && discoverBody) {
    mountPanel({
      key: "discover.trends", panel: trends, host: discoverBody, cssVar: "--trend-w", edge: "right", label: "xu hướng",
      min: 180, max: () => Math.min(520, discoverBody.getBoundingClientRect().width - 420), fallback: () => 290, outset: 13,
    });
  }

  // Bán hàng: the template and preview column beside the form (side by side from 1200 px).
  const productSide = document.querySelector<HTMLElement>(".product-side");
  const studio = productSide?.parentElement;
  if (productSide && studio) {
    mountPanel({
      key: "product.side", panel: productSide, host: studio, cssVar: "--product-side-w", edge: "left", label: "mẫu và xem trước",
      min: 320, max: () => Math.min(720, studio.getBoundingClientRect().width - 460), fallback: () => 400, outset: 11,
      enabled: () => window.innerWidth >= DRAWER_BELOW,
    });
  }
  refitPanels();
}

export { refitPanels };
