/**
 * DubFlow's own player bar for a <video>: replaces the browser's native
 * controls (Hàng đợi, Bán hàng) with one that matches the app: play, a seek
 * line with what is loaded, time, speed, volume and full screen. It sits over
 * the bottom of the picture and fades while the video plays untouched.
 */

const ICONS = {
  play: '<path d="M7 4.5v15l12.5-7.5z" fill="currentColor"/>',
  pause: '<path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" fill="currentColor"/>',
  back: '<path d="M11.5 6.5 6 12l5.5 5.5M18 6.5 12.5 12l5.5 5.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
  forward: '<path d="m12.5 6.5 5.5 5.5-5.5 5.5M6 6.5l5.5 5.5L6 17.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
  volume: '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" fill="currentColor"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
  muted: '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" fill="currentColor"/><path d="m16 9.5 5 5m0-5-5 5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
  full: '<path d="M4.5 9V4.5H9M15 4.5h4.5V9M19.5 15v4.5H15M9 19.5H4.5V15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
  exitFull: '<path d="M9 4.5V9H4.5M19.5 9H15V4.5M15 19.5V15h4.5M4.5 15H9v4.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
};
const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];
const VOLUME_KEY = "dubflow.player.volume";

function svg(path: string): string {
  return `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">${path}</svg>`;
}

/** 75 → "1:15", 3725 → "1:02:05". */
export function clock(seconds: number): string {
  const total = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

function button(className: string, label: string, icon: string): HTMLButtonElement {
  const node = document.createElement("button");
  node.type = "button";
  node.className = `pbar-btn ${className}`;
  node.setAttribute("aria-label", label);
  node.title = label;
  node.innerHTML = svg(icon);
  return node;
}

function readVolume(): { volume: number; muted: boolean } {
  try {
    const saved = JSON.parse(localStorage.getItem(VOLUME_KEY) ?? "null") as { volume?: unknown; muted?: unknown } | null;
    const volume = typeof saved?.volume === "number" && saved.volume >= 0 && saved.volume <= 1 ? saved.volume : 1;
    return { volume, muted: saved?.muted === true };
  } catch {
    return { volume: 1, muted: false };
  }
}

/**
 * Put the bar on `video`, inside `frame` (the element that goes full screen).
 * The video loses its native controls. Returns a function that hides or shows the bar.
 */
export function mountPlayerBar(video: HTMLVideoElement, frame: HTMLElement): { setVisible(visible: boolean): void } {
  video.controls = false;
  video.removeAttribute("controls");
  frame.classList.add("pbar-host");
  if (!frame.hasAttribute("tabindex")) frame.tabIndex = 0;

  const bar = document.createElement("div");
  bar.className = "pbar";
  const seek = document.createElement("div");
  seek.className = "pbar-seek";
  seek.setAttribute("role", "slider");
  seek.setAttribute("aria-label", "Tua video");
  seek.tabIndex = 0;
  const loaded = document.createElement("span");
  loaded.className = "pbar-loaded";
  const done = document.createElement("span");
  done.className = "pbar-done";
  const knob = document.createElement("span");
  knob.className = "pbar-knob";
  const tip = document.createElement("span");
  tip.className = "pbar-tip";
  tip.hidden = true;
  seek.append(loaded, done, knob, tip);

  const row = document.createElement("div");
  row.className = "pbar-row";
  const play = button("pbar-play", "Phát (phím cách)", ICONS.play);
  const back = button("", "Lùi 5 giây (←)", ICONS.back);
  const forward = button("", "Tới 5 giây (→)", ICONS.forward);
  const time = document.createElement("span");
  time.className = "pbar-time";
  time.textContent = "0:00 / 0:00";
  const spacer = document.createElement("span");
  spacer.className = "spacer";
  const speed = document.createElement("button");
  speed.type = "button";
  speed.className = "pbar-btn pbar-speed";
  speed.title = "Tốc độ phát";
  speed.textContent = "1×";
  const mute = button("", "Tắt tiếng (M)", ICONS.volume);
  const volume = document.createElement("input");
  volume.type = "range";
  volume.className = "pbar-volume";
  volume.min = "0";
  volume.max = "1";
  volume.step = "0.05";
  volume.setAttribute("aria-label", "Âm lượng");
  const full = button("", "Toàn màn hình (F)", ICONS.full);
  row.append(play, back, forward, time, spacer, speed, mute, volume, full);
  bar.append(seek, row);
  frame.append(bar);

  const saved = readVolume();
  video.volume = saved.volume;
  video.muted = saved.muted;

  let scrubbing = false;
  let idle = 0;

  const length = (): number => (Number.isFinite(video.duration) ? video.duration : 0);
  function paint(): void {
    const total = length();
    const at = scrubbing ? Number(seek.dataset.at ?? 0) : video.currentTime;
    const share = total ? Math.min(1, Math.max(0, at / total)) : 0;
    done.style.width = `${share * 100}%`;
    knob.style.left = `${share * 100}%`;
    let buffered = 0;
    for (let index = 0; index < video.buffered.length; index += 1) {
      if (video.buffered.start(index) <= at + 0.5) buffered = Math.max(buffered, video.buffered.end(index));
    }
    loaded.style.width = `${total ? Math.min(100, (buffered / total) * 100) : 0}%`;
    time.textContent = `${clock(at)} / ${clock(total)}`;
    seek.setAttribute("aria-valuemin", "0");
    seek.setAttribute("aria-valuemax", String(Math.round(total)));
    seek.setAttribute("aria-valuenow", String(Math.round(at)));
    seek.setAttribute("aria-valuetext", clock(at));
  }
  function paintPlay(): void {
    const paused = video.paused || video.ended;
    play.innerHTML = svg(paused ? ICONS.play : ICONS.pause);
    play.setAttribute("aria-label", paused ? "Phát (phím cách)" : "Tạm dừng (phím cách)");
    play.title = play.getAttribute("aria-label") ?? "";
    frame.classList.toggle("pbar-paused", paused);
    wake();
  }
  function paintVolume(): void {
    const silent = video.muted || video.volume === 0;
    mute.innerHTML = svg(silent ? ICONS.muted : ICONS.volume);
    mute.setAttribute("aria-label", silent ? "Bật tiếng (M)" : "Tắt tiếng (M)");
    mute.title = mute.getAttribute("aria-label") ?? "";
    volume.value = String(video.muted ? 0 : video.volume);
    volume.style.setProperty("--fill", `${(video.muted ? 0 : video.volume) * 100}%`);
    try { localStorage.setItem(VOLUME_KEY, JSON.stringify({ volume: video.volume, muted: video.muted })); } catch { /* chỉ là tiện ích */ }
  }
  function paintFull(): void {
    const on = document.fullscreenElement === frame;
    full.innerHTML = svg(on ? ICONS.exitFull : ICONS.full);
    full.setAttribute("aria-label", on ? "Thoát toàn màn hình (F)" : "Toàn màn hình (F)");
    full.title = full.getAttribute("aria-label") ?? "";
    frame.classList.toggle("pbar-full", on);
  }
  function wake(): void {
    frame.classList.add("pbar-awake");
    window.clearTimeout(idle);
    if (!video.paused && !scrubbing) idle = window.setTimeout(() => frame.classList.remove("pbar-awake"), 2200);
  }

  const toggle = (): void => {
    if (!video.src) return;
    if (video.paused || video.ended) void video.play().catch(() => undefined); else video.pause();
  };
  const jump = (seconds: number): void => {
    if (!length()) return;
    video.currentTime = Math.min(length(), Math.max(0, video.currentTime + seconds));
    wake();
  };
  const toggleFull = (): void => {
    if (document.fullscreenElement === frame) void document.exitFullscreen().catch(() => undefined);
    else void frame.requestFullscreen?.().catch(() => undefined);
  };

  function atPointer(event: PointerEvent): number {
    const box = seek.getBoundingClientRect();
    return Math.min(1, Math.max(0, (event.clientX - box.left) / Math.max(1, box.width))) * length();
  }
  seek.addEventListener("pointerdown", (event) => {
    if (!length() || event.button !== 0) return;
    scrubbing = true;
    seek.setPointerCapture(event.pointerId);
    seek.dataset.at = String(atPointer(event));
    frame.classList.add("pbar-scrubbing");
    paint();
  });
  seek.addEventListener("pointermove", (event) => {
    if (!length()) return;
    const at = atPointer(event);
    const box = seek.getBoundingClientRect();
    tip.hidden = false;
    tip.textContent = clock(at);
    tip.style.left = `${Math.min(box.width - 24, Math.max(24, event.clientX - box.left))}px`;
    if (scrubbing) { seek.dataset.at = String(at); paint(); }
  });
  seek.addEventListener("pointerleave", () => { if (!scrubbing) tip.hidden = true; });
  const endScrub = (event: PointerEvent): void => {
    if (!scrubbing) return;
    scrubbing = false;
    frame.classList.remove("pbar-scrubbing");
    tip.hidden = true;
    video.currentTime = atPointer(event);
    paint();
    wake();
  };
  seek.addEventListener("pointerup", endScrub);
  seek.addEventListener("pointercancel", () => { scrubbing = false; frame.classList.remove("pbar-scrubbing"); paint(); });
  seek.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); event.stopPropagation(); jump(event.key === "ArrowLeft" ? -5 : 5); }
    if (event.key === "Home") { event.preventDefault(); video.currentTime = 0; }
    if (event.key === "End" && length()) { event.preventDefault(); video.currentTime = length(); }
  });

  play.addEventListener("click", toggle);
  back.addEventListener("click", () => jump(-5));
  forward.addEventListener("click", () => jump(5));
  speed.addEventListener("click", () => {
    const next = SPEEDS[(SPEEDS.indexOf(video.playbackRate) + 1) % SPEEDS.length] ?? 1;
    video.playbackRate = next;
  });
  video.addEventListener("ratechange", () => {
    speed.textContent = `${String(video.playbackRate).replace(".", ",")}×`;
    speed.classList.toggle("on", video.playbackRate !== 1);
  });
  mute.addEventListener("click", () => {
    if (video.muted || video.volume === 0) { video.muted = false; if (video.volume === 0) video.volume = 0.6; } else video.muted = true;
  });
  volume.addEventListener("input", () => { video.volume = Number(volume.value); video.muted = video.volume === 0; });
  full.addEventListener("click", toggleFull);
  video.addEventListener("click", toggle);
  video.addEventListener("dblclick", toggleFull);
  document.addEventListener("fullscreenchange", paintFull);
  frame.addEventListener("pointermove", wake);
  frame.addEventListener("pointerleave", () => { if (!video.paused) frame.classList.remove("pbar-awake"); });
  frame.addEventListener("keydown", (event) => {
    const target = event.target as HTMLElement;
    if (target !== frame && target !== video && !bar.contains(target)) return;
    if (target instanceof HTMLButtonElement && (event.key === " " || event.key === "Enter")) return;
    if (target === volume && event.key.startsWith("Arrow")) return;
    const key = event.key.toLowerCase();
    if (key === " " || key === "k") { event.preventDefault(); toggle(); }
    else if (key === "arrowleft" || key === "j") { event.preventDefault(); jump(-5); }
    else if (key === "arrowright" || key === "l") { event.preventDefault(); jump(5); }
    else if (key === "m") { event.preventDefault(); mute.click(); }
    else if (key === "f") { event.preventDefault(); toggleFull(); }
  });
  for (const name of ["timeupdate", "durationchange", "loadedmetadata", "progress", "seeked", "emptied"]) video.addEventListener(name, paint);
  for (const name of ["play", "pause", "ended", "emptied"]) video.addEventListener(name, paintPlay);
  video.addEventListener("volumechange", paintVolume);

  paint();
  paintPlay();
  paintVolume();
  return {
    setVisible(visible: boolean): void { bar.hidden = !visible; },
  };
}
