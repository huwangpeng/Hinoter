// Node 测试：抽取 hinote-viewer.html 的核心逻辑，验证解析结果与 Python 一致
import fs from "node:fs";

// ---- 浏览器全局对象的最小桩 ----
globalThis.btoa = (s) => Buffer.from(s, "binary").toString("base64");

const html = fs.readFileSync("E:/dev/hinoter/hinote-viewer.html", "utf8");
const m = html.match(/<script>([\s\S]*)<\/script>/);
let code = m[1];
// 截掉 UI 绑定部分（依赖 document）
const cutIdx = code.indexOf("/* ============================================================\n * UI 逻辑");
code = code.slice(0, cutIdx);
// 去掉 "use strict" 外的顶层 const 冲突无所谓，用间接 eval
const runner = new Function(code + `
  return { readZip, inflateRaw, gunzip, parsePencilEngine, buildPage, svgDocument, BACKGROUND_GRIDS, strokeOutline, strokeWidth };
`);
const W = runner();

const buf = fs.readFileSync("E:/dev/hinoter/1.hinote");
const zipEntries = W.readZip(new Uint8Array(buf));
const names = Object.keys(zipEntries);
const files = {};
for (const name of names) {
  const e = zipEntries[name];
  files[name] = {
    _raw: e.raw, _method: e.method, _bytes: null,
    async bytes() {
      if (!this._bytes) this._bytes = this._method === 8 ? await W.inflateRaw(this._raw) : this._raw;
      return this._bytes;
    }
  };
}
const pageDatas = [];
for (const name of names) {
  if (zipEntries[name].fullName.startsWith("pages/") && zipEntries[name].fullName.endsWith(".jhinote")) {
    const raw = await files[name].bytes();
    const jsonBytes = await W.gunzip(raw);
    const obj = JSON.parse(new TextDecoder().decode(jsonBytes));
    pageDatas.push(obj.customNotePageContent || obj);
  }
}
pageDatas.sort((a, b) => (a.pageNumber || 0) - (b.pageNumber || 0));
const pages = [];
for (const pd of pageDatas) {
  const page = await W.buildPage(pd, files);
  if (page.strokes.length || page.images.length || W.BACKGROUND_GRIDS[page.backgroundTemplate]) pages.push(page);
}
console.log("pages:", pages.length);
const p2 = pages[1];
console.log("page2:", "template=", p2.backgroundTemplate, "texts=", p2.texts.length, "strokes=", p2.strokes.length);
if (p2.texts[0]) {
  console.log("page2 line1:", p2.texts[0].lines[0].text.slice(0, 30), "fs=", p2.texts[0].lines[0].fs.toFixed(2));
}
// 荧光笔颜色验证
const hl = [];
for (const p of pages) for (const s of p.strokes) if (s.penType === 5) hl.push([s.color, s.opacity]);
const uniq = [...new Map(hl.map(h => [h[0].join(","), h])).values()];
console.log("highlighter colors:", uniq.map(([c, o]) => "#" + c.map(v => v.toString(16).padStart(2, "0")).join("") + "@" + o));
// SVG 生成 + 轮廓计数
const svg = W.svgDocument("t", p2);
const pathCount = (svg.match(/<path/g) || []).length;
const tspanCount = (svg.match(/<tspan/g) || []).length;
console.log("page2 svg: paths=", pathCount, "tspans=", tspanCount, "size=", (svg.length / 1024).toFixed(0) + "KB");
// 检查一个笔画轮廓是否平滑（Chaikin 后顶点数应 > 2*n）
const s0 = p2.strokes.find(s => s.penType !== 5);
const out = W.strokeOutline(s0);
console.log("sample stroke: n=", s0.points.length, "outline pts=", out.length);
// 早期荧光笔宽度验证（page 11 高亮条应使用固定宽度，忽略极低压感）
const p11 = pages[10];
const hl11 = p11.strokes.find(s => s.penType === 5);
if (hl11) {
  const wEarly = W.strokeWidth(hl11, hl11.pressures[0]);
  const wFixed = hl11.baseWidth * 0.8;
  console.log("page11 highlighter baseWidth=", hl11.baseWidth, "pressure=", hl11.pressures[0], "renderWidth=", wEarly, "fixedWidth=", wFixed, "OK=", wEarly === wFixed);
}
// 老版本普通笔红蓝互换验证（page 11 的蓝色圈应为 B > R）
const pen11 = p11.strokes.find(s => s.penType === 1 && s.color[2] > 80);
if (pen11) {
  console.log("page11 type1 color=", pen11.color, "OK blue-dominant=", pen11.color[2] > pen11.color[0]);
}
console.log("OK");
