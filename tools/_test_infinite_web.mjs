import fs from "node:fs";

// Global mocks for Node environment
globalThis.document = {
  getElementById: () => ({ style: {}, classList: { add: () => {}, remove: () => {} }, addEventListener: () => {} })
};

const html = fs.readFileSync("web/infinite-viewer.html", "utf8");
const scriptContent = html.match(/<script>([\s\S]*)<\/script>/)[1];

// Extract helper functions
const runner = new Function(scriptContent + `
  return { isPenkit, parsePenkitHeader, decodePenkitStrokes, readZip };
`);

const W = runner();

const buf = fs.readFileSync("sample/无边.hinote");
const zipEntries = W.readZip(new Uint8Array(buf));
const names = Object.keys(zipEntries);

console.log("=== Node.js Web Script Test ===");
let penkitBlocks = 0;
let totalStrokes = 0;

import zlib from "node:zlib";

for (const name of names) {
  const e = zipEntries[name];
  const rawBytes = e.method === 8 ? zlib.inflateRawSync(Buffer.from(e.raw)) : e.raw;
  if (W.isPenkit(rawBytes) && /^bsd_(-?\d+)_(-?\d+)_/.test(name)) {
    penkitBlocks++;
    const hdr = W.parsePenkitHeader(rawBytes);
    const strokes = W.decodePenkitStrokes(rawBytes, 28);
    totalStrokes += strokes.length;
    console.log(`Block ${name}: Header color=[${hdr.color.join(",")}], Strokes decoded=${strokes.length}`);
    if (strokes.length > 0) {
      console.log(`  Sample Stroke 0: ${strokes[0].points.length} points, p0=(${strokes[0].points[0][0].toFixed(2)}, ${strokes[0].points[0][1].toFixed(2)}), bbox=[${strokes[0].bbox.map(v => v.toFixed(2)).join(", ")}]`);
    }
  }
}

console.log(`Total PENKIT blocks: ${penkitBlocks}, Total strokes: ${totalStrokes}`);
if (totalStrokes > 0) {
  console.log("SUCCESS: Web decoder successfully extracted all infinite strokes!");
} else {
  console.error("FAILURE: No strokes decoded.");
  process.exit(1);
}
