// Downloads the pinned 3d-force-graph UMD bundle into the package. Run: bun scripts/vendor.ts
const VERSION = "1.73.6";
const SRC = `https://unpkg.com/3d-force-graph@${VERSION}/dist/3d-force-graph.min.js`;
const OUT = new URL("../cognee_community_connector_gitgraph/vendor/", import.meta.url);

const res = await fetch(SRC);
if (!res.ok) throw new Error(`${SRC}: ${res.status}`);
const body = await res.text();
await Bun.write(new URL("3d-force-graph.min.js", OUT), body);
console.log(`wrote 3d-force-graph@${VERSION}, ${body.length} bytes`);
