/** The catalog shipped with the app, so sounds are listed before the pack is downloaded. */
import data from "./catalog.json" with { type: "json" };
import { parseCatalog, type Catalog } from "./catalog.ts";

export const CATALOG: Catalog = parseCatalog(data);
