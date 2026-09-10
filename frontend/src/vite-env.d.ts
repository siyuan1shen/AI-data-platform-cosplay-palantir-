/// <reference types="vite/client" />

declare const __API_BASE_URL__: string;

declare module "cytoscape-elk" {
  import type { Ext } from "cytoscape";
  const extension: Ext;
  export default extension;
}
