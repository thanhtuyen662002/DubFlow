import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";

const root = new URL("../", import.meta.url);
const output = new URL("dist/", root);
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await cp(new URL("src/styles.css", root), new URL("styles.css", output));
const html = await readFile(new URL("index.html", root), "utf8");
await writeFile(
  new URL("index.html", output),
  html.replace("./src/styles.css", "./styles.css").replace("./src/main.ts", "./main.js"),
  "utf8",
);
