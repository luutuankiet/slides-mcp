// Runtime for run_deck_script. Evaluated once per worker before the user's
// script. Defines the deck wrapper, selectors, helpers, emit and commit.
"use strict";

// mini-racer installs timer globals as non-configurable but writable.
for (const name of ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "queueMicrotask"]) {
  if (name in globalThis) globalThis[name] = undefined;
}

const __S = {
  pending: [],          // requests emitted since the last commit
  logs: [],
  logBytes: 0,
  status: "idle",       // idle | running | commit | done | error
  result: undefined,
  error: null,
  resume: null,
  maxRequests: 5000,
  total: 0,
  idSeq: 0,
};
globalThis.__S = __S;

globalThis.console = (() => {
  const log = (...args) => {
    if (__S.logs.length >= 100 || __S.logBytes > 8192) return;
    const line = args.map(a => typeof a === "string" ? a : __safeJSON(a)).join(" ");
    __S.logBytes += line.length;
    __S.logs.push(line.slice(0, 1000));
  };
  return { log, info: log, warn: log, error: log, debug: log };
})();

function __safeJSON(v) {
  try { return JSON.stringify(v); } catch (e) { return String(v); }
}

// ---- deck wrapper ---------------------------------------------------------

function __wrapDeck(raw) {
  const deck = raw;
  for (const slide of deck.slides) {
    const byId = new Map(slide.elements.map(e => [e.id, e]));
    Object.defineProperties(slide, {
      element: { value: (id) => byId.get(id), enumerable: false },
      find: { value: (pred) => slide.elements.filter(pred), enumerable: false },
      textElements: { get: () => slide.elements.filter(e => e.runs && e.runs.length), enumerable: false },
    });
  }
  const slideById = new Map(deck.slides.map(s => [s.id, s]));
  const elementIndex = new Map();
  for (const s of deck.slides) for (const e of s.elements) elementIndex.set(e.id, { slide: s, element: e });
  Object.defineProperties(deck, {
    slide: { value: (sel) => __select(deck, sel)[0], enumerable: false },
    select: { value: (sel) => __select(deck, sel), enumerable: false },
    element: { value: (id) => (elementIndex.get(id) || {}).element, enumerable: false },
    slideOf: { value: (id) => (elementIndex.get(id) || {}).slide, enumerable: false },
    slideById: { value: slideById, enumerable: false },
  });
  return deck;
}

// Selectors mirror read_slides: undefined/null = all; 1-based position;
// slide id; "3-7" range; array of those; predicate fn; {first}, {last},
// {hidden}, {with_notes}.
function __select(deck, sel) {
  const all = deck.slides;
  if (sel === undefined || sel === null) return all.slice();
  if (typeof sel === "function") return all.filter(sel);
  if (typeof sel === "number") {
    if (sel < 1 || sel > all.length) throw new RangeError(`slide position out of range: ${sel}`);
    return [all[sel - 1]];
  }
  if (typeof sel === "string") {
    const m = /^(\d+)-(\d+)$/.exec(sel);
    if (m) return all.slice(Math.max(0, +m[1] - 1), +m[2]);
    const s = deck.slideById.get(sel);
    if (!s) throw new RangeError(`unknown slide id: ${sel}`);
    return [s];
  }
  if (Array.isArray(sel)) return sel.flatMap(x => __select(deck, x));
  if (typeof sel === "object") {
    if ("first" in sel) return all.slice(0, sel.first);
    if ("last" in sel) return sel.last ? all.slice(-sel.last) : [];
    if ("hidden" in sel) return all.filter(s => s.hidden === !!sel.hidden);
    if (sel.with_notes) return all.filter(s => s.notes && s.notes.text);
    if (sel.id) return __select(deck, sel.id);
  }
  throw new TypeError(`unsupported slide selector: ${__safeJSON(sel)}`);
}

// ---- emit / commit --------------------------------------------------------

function emit(...reqs) {
  for (const r of reqs.flat(Infinity)) {
    if (r === undefined || r === null) continue;
    if (typeof r !== "object" || Array.isArray(r) || Object.keys(r).length !== 1) {
      throw new TypeError(`emit() takes Slides API requests with exactly one top-level key; got ${__safeJSON(r)}`);
    }
    __S.total += 1;
    if (__S.total > __S.maxRequests) {
      throw new RangeError(`request cap reached: more than ${__S.maxRequests} requests emitted (raise max_requests)`);
    }
    __S.pending.push(r);
  }
  return reqs.length;
}

function commit() {
  return new Promise((resolve) => {
    __S.status = "commit";
    __S.resume = (rawDeck) => {
      globalThis.deck = __wrapDeck(rawDeck);
      __S.status = "running";
      resolve(globalThis.deck);
    };
  });
}

// ---- helpers --------------------------------------------------------------

const EMU_PER_IN = 914400, EMU_PER_PT = 12700;

function rgb(hex) {
  const h = String(hex).replace(/^#/, "");
  if (!/^[0-9a-fA-F]{6}$/.test(h)) throw new TypeError(`not a #RRGGBB colour: ${hex}`);
  return {
    red: parseInt(h.slice(0, 2), 16) / 255,
    green: parseInt(h.slice(2, 4), 16) / 255,
    blue: parseInt(h.slice(4, 6), 16) / 255,
  };
}
function color(hex) { return { opaqueColor: { rgbColor: rgb(hex) } }; }
function themeColor(slot) { return { opaqueColor: { themeColor: slot } }; }
function solid(hex, alpha = 1) { return { solidFill: { color: { rgbColor: rgb(hex) }, alpha } }; }
function inch(n) { return n * EMU_PER_IN; }
function pt(n) { return n * EMU_PER_PT; }
function toIn(emu) { return emu / EMU_PER_IN; }
function toPt(emu) { return emu / EMU_PER_PT; }
function newId(prefix = "mcp") {
  __S.idSeq += 1;
  return `${prefix}_${Date.now().toString(36)}_${__S.idSeq}`;
}

function __idOf(x) { return typeof x === "string" ? x : x && x.id; }

// Friendly text style -> Slides TextStyle + field mask.
//   {fontFamily, weight, bold, italic, underline, sizePt, color: "#hex" | {theme: "ACCENT1"}}
// A weight always travels with its family as weightedFontFamily.
function textStyle(style) {
  const out = {}, fields = [];
  if (style.weight !== undefined) {
    if (!style.fontFamily) throw new TypeError("weight needs fontFamily alongside it");
    out.weightedFontFamily = { fontFamily: style.fontFamily, weight: style.weight };
    fields.push("weightedFontFamily");
  } else if (style.fontFamily !== undefined) {
    out.fontFamily = style.fontFamily;
    fields.push("fontFamily");
  }
  for (const [k, v] of Object.entries(style)) {
    if (k === "fontFamily" || k === "weight") continue;
    if (k === "sizePt") { out.fontSize = { magnitude: v, unit: "PT" }; fields.push("fontSize"); }
    else if (k === "color") {
      out.foregroundColor = typeof v === "string" ? color(v) : themeColor(v.theme);
      fields.push("foregroundColor");
    }
    else if (["bold", "italic", "underline", "strikethrough", "smallCaps"].includes(k)) { out[k] = !!v; fields.push(k); }
    else throw new TypeError(`unknown text style key: ${k}`);
  }
  return { style: out, fields: fields.join(",") };
}

// updateTextStyle on element `el` (object or id): the whole text, or the
// UTF-16 range {start, end} (a run object works).
function styleText(el, style, range) {
  const { style: s, fields } = textStyle(style);
  const textRange = range && range.start !== undefined
    ? { type: "FIXED_RANGE", startIndex: range.start, endIndex: range.end }
    : { type: "ALL" };
  return { updateTextStyle: { objectId: __idOf(el), textRange, style: s, fields } };
}

// One updateTextStyle per non-blank run of `el` matching pred(run). When the
// style changes fontFamily without a weight, each run keeps its own weight,
// so a font swap never turns bold headings thin.
function styleRuns(el, pred, style) {
  return el.runs.filter(r => r.text.trim() && (!pred || pred(r))).map(r => {
    const st = Object.assign({}, style);
    if (st.fontFamily !== undefined && st.weight === undefined && r.weight) st.weight = r.weight;
    return styleText(el, st, r);
  });
}

function setFill(el, hex, alpha = 1) {
  return { updateShapeProperties: {
    objectId: __idOf(el),
    shapeProperties: { shapeBackgroundFill: hex === null ? { propertyState: "NOT_RENDERED" } : solid(hex, alpha) },
    fields: hex === null ? "shapeBackgroundFill.propertyState" : "shapeBackgroundFill.solidFill.color,shapeBackgroundFill.solidFill.alpha",
  } };
}

function setBackground(slide, hex) {
  return { updatePageProperties: {
    objectId: __idOf(slide),
    pageProperties: { pageBackgroundFill: solid(hex) },
    fields: "pageBackgroundFill.solidFill.color",
  } };
}

// Resize and/or move an unrotated element, pinning one edge.
//   opts: {w, h, x, y} in EMU (page space); pin: "left"|"right"|"center"
//   horizontally and pinY: "top"|"bottom"|"middle" vertically.
function resize(el, opts) {
  if (el.rotation) throw new RangeError(`resize() supports unrotated elements only (${el.id} is rotated ${el.rotation} deg)`);
  const t = el.transform || {};
  const [pa, , , pd] = el.parentMatrix || [1, 0, 0, 1, 0, 0];
  const sx0 = t.scaleX === undefined ? 1 : t.scaleX, sy0 = t.scaleY === undefined ? 1 : t.scaleY;
  let tx = t.translateX || 0, ty = t.translateY || 0, sx = sx0, sy = sy0;
  const pin = opts.pin || "left", pinY = opts.pinY || "top";
  if (opts.w !== undefined) {
    const localW0 = el.size.w * sx0, localW1 = opts.w / pa;
    sx = localW1 / el.size.w;
    if (pin === "right") tx += localW0 - localW1;
    else if (pin === "center") tx += (localW0 - localW1) / 2;
  }
  if (opts.h !== undefined) {
    const localH0 = el.size.h * sy0, localH1 = opts.h / pd;
    sy = localH1 / el.size.h;
    if (pinY === "bottom") ty += localH0 - localH1;
    else if (pinY === "middle") ty += (localH0 - localH1) / 2;
  }
  const pm = el.parentMatrix || [1, 0, 0, 1, 0, 0];
  if (opts.x !== undefined) tx = (opts.x - pm[4]) / pa;
  if (opts.y !== undefined) ty = (opts.y - pm[5]) / pd;
  return { updatePageElementTransform: {
    objectId: el.id,
    applyMode: "ABSOLUTE",
    transform: { scaleX: sx, scaleY: sy, shearX: 0, shearY: 0, translateX: tx, translateY: ty, unit: "EMU" },
  } };
}
function move(el, x, y) { return resize(el, { x, y }); }

// A text box with text and style. Always sets autofit NONE, the setting the
// API otherwise leaves to a default that resizes text unpredictably.
//   opts: {id?, text, x, y, w, h (EMU), style?}
function textBox(slide, opts) {
  const id = opts.id || newId("tb");
  const reqs = [
    { createShape: { objectId: id, shapeType: "TEXT_BOX", elementProperties: {
      pageObjectId: __idOf(slide),
      size: { width: { magnitude: opts.w, unit: "EMU" }, height: { magnitude: opts.h, unit: "EMU" } },
      transform: { scaleX: 1, scaleY: 1, translateX: opts.x, translateY: opts.y, unit: "EMU" },
    } } },
  ];
  if (opts.text) reqs.push({ insertText: { objectId: id, insertionIndex: 0, text: opts.text } });
  reqs.push({ updateShapeProperties: { objectId: id, shapeProperties: { autofit: { autofitType: "NONE" } }, fields: "autofit.autofitType" } });
  if (opts.text && opts.style) {
    const { style, fields } = textStyle(opts.style);
    reqs.push({ updateTextStyle: { objectId: id, textRange: { type: "ALL" }, style, fields } });
  }
  return reqs;
}

// Speaker notes from Markdown. Expanded into real requests by the server.
function setNotes(slide, markdown, opts = {}) {
  const mode = opts.mode || "replace";
  if (mode !== "replace" && mode !== "append") throw new TypeError(`setNotes mode must be replace|append`);
  return { __setNotes: { slideId: __idOf(slide), markdown: String(markdown), mode } };
}

const helpers = {
  rgb, color, themeColor, solid, inch, pt, toIn, toPt, newId,
  textStyle, styleText, styleRuns, setFill, setBackground, resize, move, textBox, setNotes,
};
globalThis.helpers = helpers;
globalThis.emit = emit;
globalThis.commit = commit;

// ---- driver ---------------------------------------------------------------

function __start(inputJSON, deckJSON, maxRequests) {
  __S.maxRequests = maxRequests;
  globalThis.input = JSON.parse(inputJSON);
  globalThis.deck = __wrapDeck(JSON.parse(deckJSON));
  __S.status = "running";
  __main(globalThis.input).then(
    (v) => { __S.result = v; __S.status = "done"; },
    (e) => { __S.error = e; __S.status = "error"; },
  );
}

function __poll() {
  const out = { status: __S.status, logs: __S.logs.splice(0) };
  if (__S.status === "commit" || __S.status === "done") {
    out.requests = __S.pending.splice(0);
  }
  if (__S.status === "done") {
    try {
      const s = JSON.stringify(__S.result === undefined ? null : __S.result, (k, v) => {
        if (typeof v === "bigint") throw new TypeError("return value contains a BigInt, which JSON cannot represent");
        if (typeof v === "function") throw new TypeError(`return value contains a function${k ? ` at key "${k}"` : ""}, which JSON cannot represent`);
        if (typeof v === "symbol") throw new TypeError("return value contains a Symbol, which JSON cannot represent");
        return v;
      });
      out.result = s === undefined ? "null" : s;
    } catch (e) {
      out.status = "error";
      out.error = { name: e.name, message: `return value is not JSON-serialisable: ${e.message}`, stack: "" };
    }
  }
  if (__S.status === "error") {
    const e = __S.error;
    out.error = (e && typeof e === "object")
      ? { name: e.name || "Error", message: String(e.message), stack: String(e.stack || "") }
      : { name: "Error", message: `script threw a non-Error value: ${__safeJSON(e)}`, stack: "" };
  }
  return JSON.stringify(out);
}

function __resume(deckJSON) {
  __S.resume(JSON.parse(deckJSON));
}
