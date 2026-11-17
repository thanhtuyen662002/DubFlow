/**
 * Thumbnail (Issue #901), in Chỉnh sửa → Đăng & Shorts: a 1280×720 picture
 * drawn here from the frame under the playhead or a picture of the user's,
 * with text dragged into place. Saving writes a PNG or JPEG; nothing here
 * renders the video or sends anything off the machine.
 */

import {
  MAX_TEXT_SIZE, MAX_THUMB_TEXTS, MIN_TEXT_SIZE, SUBTITLE_FONTS, THUMB_HEIGHT, THUMB_TEMPLATES, THUMB_WIDTH,
  applyTemplate, coverRect, dragPan, emptyThumb, hasThumb, makeText, moveText, normalizeThumb, textAt, textBox, thumbName, wrapLines,
  type Rect, type ThumbDoc, type ThumbText,
} from "./thumbnail.ts";
import { isFont } from "./fonts.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export type ThumbContext = {
  /** The video's file name, for the suggested thumbnail name. */
  name: string;
  /** The main video under the playhead (its file and the second in it), or null when the playhead is on an inserted clip. */
  frame: { path: string; time: number } | null;
  /** Where exports go (Cài đặt's results folder, else beside the video). */
  folder: string;
};

export type ThumbHost = {
  context: () => ThumbContext | null;
  /** The thumbnail changed: keep it with the draft (never a render). */
  changed: () => void;
  /** Writes the finished image as a new file; its path. */
  save: (bytes: ArrayBuffer, folder: string, name: string, format: "png" | "jpeg") => Promise<string>;
};

export type ThumbnailView = {
  begin: (saved: unknown) => void;
  /** The thumbnail to keep with the draft, or null when there is none. */
  document: () => ThumbDoc | null;
  end: () => void;
};

const $ = <T extends HTMLElement = HTMLElement>(selector: string): T => document.querySelector(selector) as T;
const MAX_TEXT_WIDTH = THUMB_WIDTH * 0.9;

export function mountThumbnail(invoke: Invoke, host: ThumbHost): ThumbnailView {
  const section = $<HTMLDetailsElement>("#thumb-section");
  const canvas = $<HTMLCanvasElement>("#thumb-canvas");
  const context = canvas.getContext("2d");
  const state = $("#thumb-state");
  const zoom = $<HTMLInputElement>("#thumb-zoom");
  const darken = $<HTMLInputElement>("#thumb-darken");
  const template = $<HTMLSelectElement>("#thumb-template");
  const chips = $("#thumb-chips");
  const fields = $<HTMLFieldSetElement>("#thumb-text-fields");
  const textInput = $<HTMLTextAreaElement>("#thumb-text");
  const font = $<HTMLSelectElement>("#thumb-font");
  const size = $<HTMLInputElement>("#thumb-size");
  const color = $<HTMLInputElement>("#thumb-color");
  const stroke = $<HTMLInputElement>("#thumb-stroke");
  const strokeWidth = $<HTMLInputElement>("#thumb-stroke-width");
  const shadow = $<HTMLInputElement>("#thumb-shadow");
  const band = $<HTMLInputElement>("#thumb-band");
  const bandColor = $<HTMLInputElement>("#thumb-band-color");
  const format = $<HTMLSelectElement>("#thumb-format");
  const nameInput = $<HTMLInputElement>("#thumb-name");
  const folderLabel = $("#thumb-folder");
  const saveButton = $<HTMLButtonElement>("#thumb-save");
  const result = $("#thumb-result");

  let doc: ThumbDoc = emptyThumb();
  let selected: string | null = null;
  let picture: HTMLImageElement | null = null;
  let pictureKey = "";
  let folder = "";
  let saved = "";
  let exporting = false;
  let loading = 0;
  let boxes: Array<{ id: string; box: Rect }> = [];

  for (const family of SUBTITLE_FONTS) font.append(new Option(family, family));
  for (const item of THUMB_TEMPLATES) template.append(new Option(item.label, item.id));
  size.min = String(MIN_TEXT_SIZE);
  size.max = String(MAX_TEXT_SIZE);

  function note(text: string): void {
    state.textContent = text;
  }

  function changed(): void {
    host.changed();
    draw();
    renderFields();
  }

  const fontOf = (text: ThumbText): string => `${text.bold ? "bold " : ""}${text.size}px "${text.font}", "Segoe UI", sans-serif`;

  function draw(): void {
    if (!context) return;
    context.save();
    context.clearRect(0, 0, THUMB_WIDTH, THUMB_HEIGHT);
    context.fillStyle = "#111827";
    context.fillRect(0, 0, THUMB_WIDTH, THUMB_HEIGHT);
    if (picture) {
      const rect = coverRect(picture.naturalWidth, picture.naturalHeight, doc.zoom, doc.panX, doc.panY);
      context.drawImage(picture, rect.x, rect.y, rect.width, rect.height);
    }
    if (doc.darken > 0) {
      context.fillStyle = `rgba(0,0,0,${doc.darken})`;
      context.fillRect(0, 0, THUMB_WIDTH, THUMB_HEIGHT);
    }
    boxes = [];
    for (const text of doc.texts) {
      if (!text.text.trim()) continue;
      context.font = fontOf(text);
      const lines = wrapLines(text.text, MAX_TEXT_WIDTH, (line) => context.measureText(line).width);
      const widths = lines.map((line) => context.measureText(line).width);
      const box = textBox(text, widths);
      boxes.push({ id: text.id, box });
      if (text.band) {
        context.fillStyle = text.band;
        context.fillRect(box.x, box.y, box.width, box.height);
      }
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.lineJoin = "round";
      const lineHeight = text.size * 1.15;
      const top = text.y * THUMB_HEIGHT - (lines.length - 1) * lineHeight / 2;
      lines.forEach((line, index) => {
        const y = top + index * lineHeight;
        const x = text.x * THUMB_WIDTH;
        context.save();
        if (text.shadow) {
          context.shadowColor = "rgba(0,0,0,0.75)";
          context.shadowBlur = text.size * 0.18;
          context.shadowOffsetX = text.size * 0.05;
          context.shadowOffsetY = text.size * 0.06;
        }
        if (text.strokeWidth > 0) {
          context.strokeStyle = text.stroke;
          context.lineWidth = text.strokeWidth * 2;
          context.strokeText(line, x, y);
          context.shadowColor = "transparent";
        }
        context.fillStyle = text.color;
        context.fillText(line, x, y);
        context.restore();
      });
    }
    // The selected text's frame: the window only, never in the saved image (drawn after; export redraws without it).
    const chosen = boxes.find((item) => item.id === selected);
    if (chosen && !exporting) {
      context.strokeStyle = "#38bdf8";
      context.lineWidth = 3;
      context.setLineDash([12, 8]);
      context.strokeRect(chosen.box.x - 6, chosen.box.y - 6, chosen.box.width + 12, chosen.box.height + 12);
    }
    context.restore();
  }

  function renderChips(): void {
    chips.replaceChildren(...doc.texts.map((text, index) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = text.text.trim().split("\n")[0].slice(0, 24) || `Chữ ${index + 1}`;
      chip.setAttribute("aria-pressed", String(text.id === selected));
      chip.addEventListener("click", () => select(text.id));
      return chip;
    }));
    $<HTMLButtonElement>("#thumb-add-text").disabled = doc.texts.length >= MAX_THUMB_TEXTS;
  }

  function renderFields(): void {
    renderChips();
    zoom.value = String(Math.round(doc.zoom * 100));
    darken.value = String(Math.round(doc.darken * 100));
    format.value = doc.format;
    const text = doc.texts.find((item) => item.id === selected);
    fields.hidden = !text;
    if (!text) return;
    if (document.activeElement !== textInput) textInput.value = text.text;
    font.value = text.font;
    size.value = String(text.size);
    color.value = text.color;
    stroke.value = text.stroke;
    strokeWidth.value = String(text.strokeWidth);
    shadow.checked = text.shadow;
    band.checked = !!text.band;
    bandColor.value = text.band ?? "#e11d48";
    bandColor.disabled = !text.band;
  }

  function select(id: string | null): void {
    selected = id;
    draw();
    renderFields();
  }

  function updateText(change: Partial<ThumbText>): void {
    doc = { ...doc, texts: doc.texts.map((text) => text.id === selected ? { ...text, ...change } : text) };
    changed();
  }

  /** Loads the background's picture (a frame read once per moment, or the user's image). */
  async function loadPicture(): Promise<void> {
    const background = doc.background;
    const key = background ? `${background.kind}|${background.path}|${background.kind === "frame" ? background.time.toFixed(3) : ""}` : "";
    if (key === pictureKey) return;
    pictureKey = key;
    release();
    picture = null;
    draw();
    if (!background) return;
    const ticket = ++loading;
    note("Đang lấy hình nền…");
    try {
      const bytes = await invoke<ArrayBuffer>("thumbnail_frame", { path: background.path, time: background.kind === "frame" ? background.time : 0 });
      const url = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
      const image = new Image();
      await new Promise<void>((resolve, reject) => {
        image.onload = () => resolve();
        image.onerror = () => reject(new Error("Không đọc được hình"));
        image.src = url;
      });
      if (ticket !== loading) return URL.revokeObjectURL(url);
      release();
      picture = image;
      note("");
      draw();
    } catch (error) {
      if (ticket !== loading) return;
      note(`Không lấy được hình nền: ${String(error).slice(0, 160)}. Chọn lại khung hình hoặc ảnh khác.`);
    }
  }

  function release(): void {
    const old = picture as HTMLImageElement | null;
    if (old?.src.startsWith("blob:")) URL.revokeObjectURL(old.src);
  }

  function setBackground(background: ThumbDoc["background"]): void {
    doc = { ...doc, background, zoom: 1, panX: 0, panY: 0 };
    changed();
    void loadPicture();
  }

  $("#thumb-frame").addEventListener("click", () => {
    const frame = host.context()?.frame;
    if (!frame) return note("Đầu phát đang ở đoạn chèn; đưa đầu phát về video chính để lấy khung hình.");
    setBackground({ kind: "frame", path: frame.path, time: frame.time });
  });
  $("#thumb-image").addEventListener("click", () => {
    void invoke<string[]>("pick_product_files", { kind: "background" }).then((files) => {
      if (files[0]) setBackground({ kind: "image", path: files[0] });
    }).catch((error) => note(`Không chọn được ảnh: ${String(error).slice(0, 160)}`));
  });
  template.addEventListener("change", () => {
    if (!template.value) return;
    const next = applyTemplate(doc, template.value, thumbName(host.context()?.name ?? "").replace(/ - thumbnail$/, ""));
    template.value = "";
    if (next === doc) return note(`Ảnh bìa đã có ${MAX_THUMB_TEXTS} chữ. Xóa bớt một chữ rồi chọn lại mẫu.`);
    doc = next;
    selected = doc.texts[doc.texts.length - 1]?.id ?? null;
    changed();
  });
  zoom.addEventListener("input", () => {
    doc = { ...doc, zoom: Number(zoom.value) / 100 };
    changed();
  });
  darken.addEventListener("input", () => {
    doc = { ...doc, darken: Number(darken.value) / 100 };
    changed();
  });
  $("#thumb-add-text").addEventListener("click", () => {
    if (doc.texts.length >= MAX_THUMB_TEXTS) return;
    const text = makeText("Chữ mới", { y: 0.5 });
    doc = { ...doc, texts: [...doc.texts, text] };
    selected = text.id;
    changed();
    textInput.focus();
    textInput.select();
  });
  $("#thumb-delete-text").addEventListener("click", () => {
    doc = { ...doc, texts: doc.texts.filter((text) => text.id !== selected) };
    selected = null;
    changed();
  });
  textInput.addEventListener("input", () => updateText({ text: textInput.value.slice(0, 120) }));
  font.addEventListener("change", () => { if (isFont(font.value)) updateText({ font: font.value }); });
  size.addEventListener("input", () => updateText({ size: Number(size.value) }));
  color.addEventListener("input", () => updateText({ color: color.value }));
  stroke.addEventListener("input", () => updateText({ stroke: stroke.value }));
  strokeWidth.addEventListener("input", () => updateText({ strokeWidth: Number(strokeWidth.value) }));
  shadow.addEventListener("change", () => updateText({ shadow: shadow.checked }));
  band.addEventListener("change", () => updateText({ band: band.checked ? bandColor.value : null }));
  bandColor.addEventListener("input", () => updateText({ band: bandColor.value }));
  format.addEventListener("change", () => {
    doc = { ...doc, format: format.value === "png" ? "png" : "jpeg" };
    changed();
  });

  // Drag a text to move it; drag elsewhere to move the picture inside the frame.
  let drag: { id: string | null; x: number; y: number; pointer: number } | null = null;
  const point = (event: PointerEvent): { x: number; y: number } => {
    const rect = canvas.getBoundingClientRect();
    return { x: (event.clientX - rect.left) * THUMB_WIDTH / rect.width, y: (event.clientY - rect.top) * THUMB_HEIGHT / rect.height };
  };
  canvas.addEventListener("pointerdown", (event) => {
    const at = point(event);
    const id = textAt(boxes, at.x, at.y);
    if (id !== selected) select(id);
    drag = { id, ...at, pointer: event.pointerId };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drag || drag.pointer !== event.pointerId) return;
    const at = point(event);
    const dx = at.x - drag.x, dy = at.y - drag.y;
    drag.x = at.x;
    drag.y = at.y;
    if (drag.id) doc = { ...doc, texts: doc.texts.map((text) => text.id === drag!.id ? moveText(text, dx, dy) : text) };
    else if (picture) doc = { ...doc, ...dragPan(doc, picture.naturalWidth, picture.naturalHeight, dx, dy) };
    else return;
    draw();
  });
  const drop = (event: PointerEvent): void => {
    if (!drag || drag.pointer !== event.pointerId) return;
    drag = null;
    changed();
  };
  canvas.addEventListener("pointerup", drop);
  canvas.addEventListener("pointercancel", drop);
  canvas.addEventListener("keydown", (event) => {
    const text = doc.texts.find((item) => item.id === selected);
    const step = event.shiftKey ? 32 : 8;
    const moves: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const move = moves[event.key];
    if (!text || !move) return;
    event.preventDefault();
    event.stopPropagation();
    doc = { ...doc, texts: doc.texts.map((item) => item.id === text.id ? moveText(item, move[0], move[1]) : item) };
    changed();
  });

  // Saving: drawn again without the selection frame, then written as a new file.
  function imageBytes(type: "png" | "jpeg"): Promise<ArrayBuffer> {
    exporting = true;
    draw();
    return new Promise<ArrayBuffer>((resolve, reject) => {
      canvas.toBlob((blob) => {
        exporting = false;
        draw();
        if (!blob) return reject(new Error("Không tạo được ảnh"));
        void blob.arrayBuffer().then(resolve, reject);
      }, type === "png" ? "image/png" : "image/jpeg", 0.92);
    });
  }
  saveButton.addEventListener("click", () => {
    const name = nameInput.value.trim();
    if (!name) return note("Hãy đặt tên cho thumbnail.");
    if (!folder) return note("Hãy chọn thư mục lưu.");
    saveButton.disabled = true;
    note("Đang lưu thumbnail…");
    void imageBytes(doc.format)
      .then((bytes) => host.save(bytes, folder, name, doc.format))
      .then((path) => {
        saved = path;
        result.hidden = false;
        note(`Đã lưu ${path} (${THUMB_WIDTH}×${THUMB_HEIGHT}). Video không bị render lại.`);
      })
      .catch((error) => note(`Không lưu được thumbnail: ${String(error).slice(0, 200)}`))
      .finally(() => { saveButton.disabled = false; });
  });
  $("#thumb-folder-pick").addEventListener("click", () => {
    void invoke<string | null>("pick_folder").then((picked) => {
      if (!picked) return;
      folder = picked;
      folderLabel.textContent = picked;
      folderLabel.title = picked;
    }).catch(() => undefined);
  });
  $("#thumb-reveal").addEventListener("click", () => {
    if (saved) void invoke("thumbnail_reveal", { path: saved }).catch((error) => note(String(error).slice(0, 160)));
  });

  // Opening the section fills in the name and folder, and a first frame when there is no background yet.
  section.addEventListener("toggle", () => {
    if (!section.open) return;
    const now = host.context();
    if (now) {
      if (!nameInput.value.trim()) nameInput.value = thumbName(now.name);
      if (!folder) {
        folder = now.folder;
        folderLabel.textContent = folder;
        folderLabel.title = folder;
      }
      if (!doc.background && now.frame) setBackground({ kind: "frame", path: now.frame.path, time: now.frame.time });
    }
    void loadPicture();
    draw();
    renderFields();
  });

  return {
    begin(value) {
      doc = normalizeThumb(value) ?? emptyThumb();
      selected = null;
      picture = null;
      pictureKey = "";
      folder = "";
      saved = "";
      nameInput.value = "";
      folderLabel.textContent = "";
      result.hidden = true;
      note("");
      section.open = false;
      renderFields();
      draw();
    },
    document: () => hasThumb(doc) ? doc : null,
    end() {
      loading += 1;
      release();
      picture = null;
      pictureKey = "";
    },
  };
}
