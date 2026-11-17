// #923 browser evidence: real Chromium, the app's own thumbnail view (vite preview of apps/desktop),
// four layouts with long titles; window canvas vs the bytes the app's save path produced (imageBytes → canvas.toBlob).
const { chromium } = require('playwright');
const crypto = require('crypto');
const fs = require('fs');
const OUT = process.argv[2];
// usage: node t4.js <out dir>  (vite preview of apps/desktop on 127.0.0.1:1427)
const TITLES = { t45: 'RC23 THÁP NUÔI LỢN 26 TẦNG Ở HỒ BẮC TRUNG QUỐC', t107: 'Bên trong tòa tháp nuôi lợn 26 tầng lớn nhất thế giới ở Hồ Bắc Trung Quốc mỗi năm xuất chuồng 1,2 triệu con' };
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1500, height: 950 } });
  p.on('pageerror', e => console.log('PAGEERROR', e.message));
  await p.addInitScript(() => {
    window.__saved = [];
    const original = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function (done, type, quality) {
      return original.call(this, (blob) => { if (blob) blob.arrayBuffer().then((buffer) => window.__saved.push({ type, bytes: Array.from(new Uint8Array(buffer)) })); done(blob); }, type, quality);
    };
  });
  await p.goto('http://127.0.0.1:1427/'); await p.waitForTimeout(2500);
  await p.click('#nav-edit'); await p.waitForTimeout(800);
  await p.getByText('Mở đầu kênh Phim Hay').first().click(); await p.waitForTimeout(2000);
  await p.evaluate(() => { let e = document.querySelector('#thumb-section'); while (e) { if (e.hidden) e.hidden = false; if (e.tagName === 'DETAILS') { e.open = true; e.dispatchEvent(new Event('toggle')); } e = e.parentElement; } });
  await p.waitForTimeout(500);
  await p.click('#thumb-frame'); await p.waitForTimeout(1500);
  const canvas = p.locator('#thumb-canvas');
  const box = await canvas.boundingBox(); const k = box.width / 1280;
  const results = {};
  for (const [key, title] of Object.entries(TITLES)) for (const id of ['left-column', 'band', 'big-bottom', 'two-lines']) {
    // only the layout's own texts: delete any text first
    for (let i = 0; i < 6; i++) { const chip = p.locator('#thumb-chips .chip').first(); if (!(await chip.count())) break; await chip.click(); await p.click('#thumb-delete-text'); }
    await p.selectOption('#thumb-template', id); await p.waitForTimeout(200);
    await p.fill('#thumb-text', title); await p.waitForTimeout(200);
    // deselect (click on the top-right corner area without moving) so the window shows no frame
    await p.evaluate(() => {
      const c = document.querySelector('#thumb-canvas'); const r = c.getBoundingClientRect(); const fields = document.querySelector('#thumb-text-fields');
      for (const [x, y] of [[2, 2], [1278, 2], [2, 718], [1278, 718], [640, 2], [640, 718], [2, 360], [1278, 360]]) {
        const at = { clientX: r.left + x * r.width / 1280, clientY: r.top + y * r.height / 720, pointerId: 1, bubbles: true };
        c.dispatchEvent(new PointerEvent('pointerdown', at)); c.dispatchEvent(new PointerEvent('pointerup', at));
        if (fields.hidden) return;
      }
    }); await p.waitForTimeout(200);
    const framed = await p.evaluate(() => document.querySelector('#thumb-text-fields').hidden === false);
    if (framed) console.log('STILL SELECTED', key, id);
    const shot = `${OUT}/${key}-${id}-window.png`; await canvas.screenshot({ path: shot });
    const results1 = {};
    for (const format of ['png', 'jpeg']) {
      await p.selectOption('#thumb-format', format);
      await p.fill('#thumb-name', `${key}-${id}`);
      const before = await p.evaluate(() => window.__saved.length);
      await p.click('#thumb-save'); await p.waitForFunction((n) => window.__saved.length > n, before); await p.waitForTimeout(150);
      const stats = await p.evaluate(async () => {
        const saved = window.__saved[window.__saved.length - 1];
        const blob = new Blob([new Uint8Array(saved.bytes)], { type: saved.type });
        const bitmap = await createImageBitmap(blob);
        const off = new OffscreenCanvas(bitmap.width, bitmap.height); const g = off.getContext('2d'); g.drawImage(bitmap, 0, 0);
        const img = g.getImageData(0, 0, bitmap.width, bitmap.height).data;
        const win = document.querySelector('#thumb-canvas').getContext('2d').getImageData(0, 0, 1280, 720).data;
        let differ = 0, sum = 0, max = 0;
        for (let i = 0; i < img.length; i += 4) { const d = Math.max(Math.abs(img[i] - win[i]), Math.abs(img[i+1] - win[i+1]), Math.abs(img[i+2] - win[i+2])); if (d > 0) differ++; sum += d; if (d > max) max = d; }
        // text pixels: bright (sum of RGB > 600) or text yellow; counted in the outer 2 px of each side
        const bright = (x, y) => { const i = (y * bitmap.width + x) * 4; return img[i] + img[i+1] + img[i+2] > 600 ? 1 : 0; };
        let left = 0, right = 0, top = 0, bottom = 0;
        for (let y = 0; y < 720; y++) for (const x of [0, 1]) { left += bright(x, y); right += bright(1279 - x, y); }
        for (let x = 0; x < 1280; x++) for (const y of [0, 1]) { top += bright(x, y); bottom += bright(x, 719 - y); }
        return { type: saved.type, width: bitmap.width, height: bitmap.height, bytes: saved.bytes.length, pixelsDifferingFromWindow: differ, meanAbsDiff: +(sum / (img.length / 4)).toFixed(3), maxDiff: max, edgeBright: { left, right, top, bottom } };
      });
      const saved = await p.evaluate(() => window.__saved[window.__saved.length - 1].bytes);
      const buf = Buffer.from(saved); stats.sha256 = crypto.createHash('sha256').update(buf).digest('hex');
      fs.writeFileSync(`${OUT}/${key}-${id}.${format === 'png' ? 'png' : 'jpg'}`, buf);
      results1[format] = stats;
    }
    results[`${key}-${id}`] = results1;
  }
  // Drag: a one-word text pushed against an edge with the real mouse, then dragged back 40 px.
  // Ink is found by colour: every dragged text is set to pure green (#00ff00), which the background frame does not have.
  const green = () => p.evaluate(() => { const c = document.querySelector('#thumb-color'); c.value = '#00ff00'; c.dispatchEvent(new Event('input', { bubbles: true })); });
  const ink = () => p.evaluate(() => {
    const g = document.querySelector('#thumb-canvas').getContext('2d'); const d = g.getImageData(0, 0, 1280, 720).data;
    let left = 1280, right = -1, top = 720, bottom = -1;
    for (let y = 0; y < 720; y++) for (let x = 0; x < 1280; x++) { const i = (y * 1280 + x) * 4; if (d[i] < 40 && d[i+1] > 230 && d[i+2] < 40) { if (x < left) left = x; if (x > right) right = x; if (y < top) top = y; if (y > bottom) bottom = y; } }
    return { left, right, top, bottom };
  });
  for (let i = 0; i < 6; i++) { const chip = p.locator('#thumb-chips .chip').first(); if (!(await chip.count())) break; await chip.click(); await p.click('#thumb-delete-text'); }
  await p.click('#thumb-add-text'); await p.fill('#thumb-text', 'NGHIÊNG'); await green(); await p.waitForTimeout(200);
  const drags = {};
  const toScreen = async (x, y) => { await canvas.scrollIntoViewIfNeeded(); const r = await canvas.boundingBox(); return [r.x + x * r.width / 1280, r.y + y * r.height / 720]; };
  const drag = async (from, by, steps) => { const [sx, sy] = await toScreen(from[0], from[1]); const r = await canvas.boundingBox(); const k2 = r.width / 1280;
    await p.mouse.move(sx, sy); await p.mouse.down(); for (let i = 1; i <= steps; i++) await p.mouse.move(sx + by[0] * k2 * i / steps, sy + by[1] * k2 * i / steps); await p.mouse.up(); await p.waitForTimeout(150); };
  for (const [name, push, back] of [['left', [-900, 0], [40, 0]], ['right', [900, 0], [-40, 0]], ['bottom', [0, 500], [0, -40]], ['top', [0, -500], [0, 40]]]) {
    const start = await ink(); await drag([(start.left + start.right) / 2, (start.top + start.bottom) / 2], push, 30);
    const pushed = await ink(); await drag([(pushed.left + pushed.right) / 2, (pushed.top + pushed.bottom) / 2], back, 4);
    const moved = await ink();
    drags[name] = { pushed, back, after: moved, movedBy: [moved.left - pushed.left, moved.top - pushed.top] };
    await drag([(moved.left + moved.right) / 2, (moved.top + moved.bottom) / 2], [640 - (moved.left + moved.right) / 2, 360 - (moved.top + moved.bottom) / 2], 10);
  }
  results.drag = drags;
  // Drags with the real mouse on long titles. A canvas hash compares the whole window drawing (with the selection frame).
  const hash = () => p.evaluate(async () => {
    const d = document.querySelector('#thumb-canvas').getContext('2d').getImageData(0, 0, 1280, 720).data;
    const h = await crypto.subtle.digest('SHA-256', d); return Array.from(new Uint8Array(h)).map((v) => v.toString(16).padStart(2, '0')).join('').slice(0, 16);
  });
  const clear = async () => { for (let i = 0; i < 6; i++) { const chip = p.locator('#thumb-chips .chip').first(); if (!(await chip.count())) break; await chip.click(); await p.click('#thumb-delete-text'); } };
  const layout = async (id, title) => { await clear(); await p.selectOption('#thumb-template', id); await p.waitForTimeout(200); await p.fill('#thumb-text', title); await green(); await p.waitForTimeout(200); };
  const centre = (r) => [(r.left + r.right) / 2, (r.top + r.bottom) / 2];
  // path: list of [dx, dy] in SCREEN px from the grab point; the mouse goes through each, then is released at the last
  const path = async (from, points, check) => { const [sx, sy] = await toScreen(from[0], from[1]);
    await p.mouse.move(sx, sy); await p.mouse.down(); const seen = [];
    for (const [dx, dy] of points) { await p.mouse.move(sx + dx, sy + dy); if (check) seen.push(await check()); }
    await p.mouse.up(); await p.waitForTimeout(150); return seen; };
  const scenarios = {};
  const K = (await canvas.boundingBox()).width / 1280; // screen px per thumbnail px
  // wob: `steps` moves to dx THUMBNAIL px across, with a `amp` SCREEN px up/down wobble on the way, ending at dy screen px
  const wob = (dx, steps, amp, dy = 0) => Array.from({ length: steps }, (_, i) => [dx * K * (i + 1) / steps, (i + 1) === steps ? dy : (i % 2 ? amp : -amp)]);
  // A: big-bottom 45, two strokes 120 thumbnail px right then back, with a 4 screen px (> DRAG_START_PX = 3) wobble.
  await layout('big-bottom', TITLES.t45);
  let h0 = await hash(); let i0 = await ink();
  await path(centre(i0), wob(120, 15, 4)); const iMid = await ink();
  await path(centre(iMid), wob(-120, 15, 4));
  scenarios.A_twoStrokes120WithWobble4px = { start: h0, middleInk: iMid, startInk: i0, end: await hash(), same: h0 === await hash() };
  // B: one stroke 300 thumbnail px right with a 4 px wobble and straight back to the grab point, then release.
  h0 = await hash(); i0 = await ink();
  await path(centre(i0), [...wob(300, 20, 4), ...wob(300, 20, 4).map(([dx, dy]) => [300 * K - dx, dy])]);
  scenarios.B_outAndBack300OneStroke = { start: h0, end: await hash(), same: h0 === await hash() };
  // C: 200 thumbnail px right ending 2 screen px higher (< DRAG_START_PX), release; then back ending 2 px higher.
  h0 = await hash(); i0 = await ink();
  await path(centre(i0), wob(200, 10, 4, -2)); const iC = await ink();
  await path(centre(iC), wob(-200, 10, 4, -2));
  scenarios.C_twoStrokes200EndingTwoPxHigher = { start: h0, end: await hash(), same: h0 === await hash() };
  // D: left-column 107, held against the left edge: push 100 screen px into it, 30 px up and down with a 2 px sideways
  // jitter, back to the grab point: the same lines in the same place across at every step; then pulled 40 px away.
  await layout('left-column', TITLES.t107);
  h0 = await hash(); i0 = await ink();
  const across = async () => { const r = await ink(); return [r.left, r.right]; };
  const seenD = await path(centre(i0), [[-100, 0], [-100, -30], [2, -30], [-2, 30], [2, 30], [0, 0]], across);
  scenarios.D_sideHeldPushAndJitter = { startAcross: [i0.left, i0.right], seen: seenD, sameAcrossEveryStep: seenD.every(([l, r]) => l === i0.left && r === i0.right), start: h0, end: await hash(), same: h0 === await hash() };
  const iD = await ink(); const seenPull = await path(centre(iD), [[20, 0], [40, 0]], across);
  scenarios.D_pulledAway40px = { before: [iD.left, iD.right], after: seenPull[1], moved: seenPull[1][0] > iD.left };
  // E: big-bottom 45 stretched 200 thumbnail px right, then 20 thumbnail px up, back down and back across, one stroke.
  await layout('big-bottom', TITLES.t45);
  h0 = await hash(); i0 = await ink();
  const seenE = await path(centre(i0), [[100 * K, 0], [200 * K, 0], [200 * K, -20 * K], [200 * K, 0], [100 * K, 0], [0, 0]], async () => ({ ink: await ink(), hash: await hash() }));
  scenarios.E_stretchUpBack = { stretched: seenE[1].ink, up: seenE[2].ink, upMovedPx: seenE[1].ink.top - seenE[2].ink.top, backDownSameAsStretched: seenE[1].hash === seenE[3].hash, backAcrossSameAsStart: seenE[5].hash === h0, end: await hash(), same: h0 === await hash() };
  // F: a text held at the left edge whose wrap depends on its room (#923 review round 6): "NGHIÊNG AB CD" placed where
  // "AB CD" just fits the room and the long first word holds it at the edge; 4 strokes of 40 screen px down/up, each
  // ending 2 screen px toward the edge, then 5 ArrowLeft presses: its lines must not change (ink width and height).
  await clear(); await p.click('#thumb-add-text'); await p.fill('#thumb-text', 'NGHIÊNG AB CD'); await green(); await p.waitForTimeout(200);
  const fit = await p.evaluate(() => { const g = document.createElement('canvas').getContext('2d'); g.font = 'bold 96px "Segoe UI Black", "Segoe UI", sans-serif';
    return { abcd: g.measureText('AB CD').width, word: g.measureText('NGHIÊNG').width }; });
  const targetX = (fit.abcd + 36) / 2560; // room = 2·x·1280 − 16 = "AB CD" + 20 px
  let iF = await ink();
  await path(centre(iF), [[(targetX - 0.5) * 1280 * K / 2, 0], [(targetX - 0.5) * 1280 * K, 0]]);
  const placed = await ink(); const shape = (r) => [r.left, r.right, r.bottom - r.top];
  const seenF = [];
  for (const dy of [40, -40, 40, -40]) { const r = await ink(); await path(centre(r), [[-1, dy / 2], [-2, dy]]); seenF.push(shape(await ink())); }
  await p.focus('#thumb-canvas'); for (let i = 0; i < 5; i++) await p.keyboard.press('ArrowLeft'); await p.waitForTimeout(150);
  const afterKeys = shape(await ink());
  scenarios.F_sideHeldRoomDependent = { fontWidths: fit, targetX, placed: shape(placed), heldAtLeftEdge: placed.left < 20, afterStrokes: seenF, afterKeys, sameLinesThroughout: [...seenF, afterKeys].every((s) => s[0] === shape(placed)[0] && s[1] === shape(placed)[1] && Math.abs(s[2] - shape(placed)[2]) <= 2), note: 'same ink left/right; ink height within 2 px (anti-aliasing at a new vertical sub-pixel place); one more line would add about 110 px' };
  results.scenarios = scenarios;
  results.stageWidthPx = (await canvas.boundingBox()).width;
  results.browser = `Chromium ${b.version()}`;
  console.log(JSON.stringify(results, null, 1));
  await b.close();
})();
