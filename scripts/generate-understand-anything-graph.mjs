#!/usr/bin/env node

/**
 * Build a read-only Understand-Anything graph from deterministic structural
 * extraction results. The optional third-party checkout lives in .tools/ and
 * is never part of the application runtime or enterprise data path.
 *
 * Usage:
 *   node scripts/generate-understand-anything-graph.mjs [project-root]
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { basename, join, relative, sep } from "node:path";
import { execFileSync } from "node:child_process";

const root = process.argv[2] ? process.argv[2] : process.cwd();
const uaDir = join(root, ".ua");
const intermediateDir = join(uaDir, "intermediate");
const outputPath = join(uaDir, "knowledge-graph.json");
const scanPath = join(intermediateDir, "scan-result.json");

if (!existsSync(scanPath)) {
  throw new Error(`Missing ${scanPath}. Run the Understand-Anything scan first.`);
}

const scan = JSON.parse(readFileSync(scanPath, "utf8"));
const batchFiles = readdirSync(intermediateDir)
  .filter((name) => /^batch-\d+(?:-part-\d+)?\.json$/u.test(name))
  .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
if (!batchFiles.length) throw new Error(`No structural batch files found in ${intermediateDir}.`);

const results = [];
for (const name of batchFiles) {
  const batch = JSON.parse(readFileSync(join(intermediateDir, name), "utf8"));
  for (const result of batch.results ?? []) results.push(result);
}

const fileMeta = new Map((scan.files ?? []).map((file) => [file.path, file]));
const nodes = [];
const edges = [];
const nodeIds = new Set();
const edgeKeys = new Set();
const functionIds = new Map();

const complexity = (lines) => lines > 700 ? "complex" : lines > 250 ? "moderate" : "simple";
const categoryType = (category, filePath) => {
  if (category === "config") return "config";
  if (category === "docs") return "document";
  if (category === "data") return "table";
  if (category === "infra") return filePath.toLowerCase().includes("docker") ? "service" : "resource";
  return "file";
};
const addNode = (node) => {
  if (nodeIds.has(node.id)) return;
  nodeIds.add(node.id);
  nodes.push(node);
};
const addEdge = (source, target, type, weight = 0.5) => {
  if (!nodeIds.has(source) || !nodeIds.has(target)) return;
  const key = `${type}|${source}|${target}`;
  if (edgeKeys.has(key)) return;
  edgeKeys.add(key);
  edges.push({ source, target, type, direction: "forward", weight });
};
const tagsFor = (filePath, meta) => {
  const parts = filePath.split("/");
  const tags = [meta.fileCategory, meta.language];
  for (const marker of ["frontend", "backend", "agent", "imports", "projection", "actions", "work-observation", "docs"]) {
    if (filePath.toLowerCase().includes(marker)) tags.push(marker);
  }
  return [...new Set(tags)];
};
const descriptionFor = (filePath, meta) => {
  const area = filePath.startsWith("frontend/") ? "前端界面与交互" :
    filePath.startsWith("backend/") ? "后端领域服务与接口" :
    filePath.startsWith("scripts/") ? "运行与验证脚本" :
    filePath.startsWith("docs/") ? "项目设计与使用文档" : "项目配置或基础文件";
  return `${area}：${basename(filePath)}（${meta.language}，${meta.sizeLines} 行）。结构关系由静态分析生成。`;
};

for (const [filePath, meta] of fileMeta) {
  const type = categoryType(meta.fileCategory, filePath);
  addNode({
    id: `${type}:${filePath}`,
    type,
    name: basename(filePath),
    filePath,
    summary: descriptionFor(filePath, meta),
    tags: tagsFor(filePath, meta),
    complexity: complexity(meta.sizeLines),
  });
}

for (const result of results) {
  const meta = fileMeta.get(result.path);
  if (!meta) continue;
  const fileType = categoryType(meta.fileCategory, result.path);
  const fileId = `${fileType}:${result.path}`;
  const fileSummary = descriptionFor(result.path, meta);

  for (const fn of result.functions ?? []) {
    const id = `function:${result.path}:${fn.name}`;
    addNode({
      id,
      type: "function",
      name: fn.name,
      filePath: result.path,
      lineRange: [fn.startLine ?? fn.lineRange?.[0] ?? 1, fn.endLine ?? fn.lineRange?.[1] ?? fn.startLine ?? 1],
      summary: `函数 ${fn.name}：位于 ${basename(result.path)}，参数 ${fn.params?.length ?? 0} 个。`,
      tags: ["function", meta.language, meta.fileCategory],
      complexity: complexity(result.totalLines ?? meta.sizeLines),
    });
    functionIds.set(`${result.path}|${fn.name}`, id);
    addEdge(fileId, id, "contains", 1);
  }
  for (const cls of result.classes ?? []) {
    const id = `class:${result.path}:${cls.name}`;
    addNode({
      id,
      type: "class",
      name: cls.name,
      filePath: result.path,
      lineRange: [cls.startLine ?? cls.lineRange?.[0] ?? 1, cls.endLine ?? cls.lineRange?.[1] ?? cls.startLine ?? 1],
      summary: `类 ${cls.name}：位于 ${basename(result.path)}，包含 ${cls.methods?.length ?? 0} 个方法。`,
      tags: ["class", meta.language, meta.fileCategory],
      complexity: complexity(result.totalLines ?? meta.sizeLines),
    });
    addEdge(fileId, id, "contains", 1);
  }
  for (const definition of result.definitions ?? []) {
    const id = `${definition.kind}:${result.path}:${definition.name}`;
    addNode({
      id,
      type: definition.kind === "table" || definition.kind === "view" ? "table" : "schema",
      name: definition.name,
      filePath: result.path,
      lineRange: definition.lineRange,
      summary: `${definition.kind} ${definition.name}：${definition.fields?.length ?? 0} 个字段。`,
      tags: ["definition", meta.language],
      complexity: "simple",
    });
    addEdge(fileId, id, "contains", 1);
  }
  for (const service of result.services ?? []) {
    const id = `service:${result.path}:${service.name}`;
    addNode({ id, type: "service", name: service.name, filePath: result.path, summary: `服务 ${service.name}。`, tags: ["service"], complexity: "simple" });
    addEdge(fileId, id, "contains", 1);
  }
  for (const endpoint of result.endpoints ?? []) {
    const name = `${endpoint.method ?? ""} ${endpoint.path}`.trim();
    const id = `endpoint:${result.path}:${endpoint.path}`;
    addNode({ id, type: "endpoint", name, filePath: result.path, lineRange: endpoint.lineRange, summary: `接口 ${name}。`, tags: ["endpoint", "api"], complexity: "simple" });
    addEdge(fileId, id, "contains", 1);
  }
  for (const step of result.steps ?? []) {
    const id = `pipeline:${result.path}:${step.name}`;
    addNode({ id, type: "pipeline", name: step.name, filePath: result.path, lineRange: step.lineRange, summary: `流水线步骤 ${step.name}。`, tags: ["pipeline"], complexity: "simple" });
    addEdge(fileId, id, "contains", 1);
  }
  for (const call of result.callGraph ?? []) {
    const source = functionIds.get(`${result.path}|${call.caller}`);
    const target = functionIds.get(`${result.path}|${call.callee}`);
    if (source && target) addEdge(source, target, "calls", 0.8);
  }
}

for (const [from, imports] of Object.entries(scan.importMap ?? {})) {
  const fromMeta = fileMeta.get(from);
  const fromType = fromMeta ? categoryType(fromMeta.fileCategory, from) : "file";
  for (const to of imports ?? []) {
    const toMeta = fileMeta.get(to);
    const toType = toMeta ? categoryType(toMeta.fileCategory, to) : "file";
    addEdge(`${fromType}:${from}`, `${toType}:${to}`, "imports", 0.7);
  }
}

const layerDefinitions = [
  ["frontend", "前端展示层", "React 页面、组件、前端 API 和工作区状态。"],
  ["backend", "后端领域层", "企业投影、本体、管理 Agent、导入和 Action 服务。"],
  ["contracts", "契约与接口层", "OpenAPI 和跨前后端数据契约。"],
  ["scripts", "运行与验证层", "启动、诊断、测试和独立采集脚本。"],
  ["docs", "文档与设计层", "产品定位、架构决策、使用手册和验收记录。"],
  ["root", "项目基础层", "根目录配置、许可证和项目说明。"],
];
const layers = layerDefinitions.map(([key, name, description]) => ({
  id: `layer:${key}`,
  name,
  description,
  nodeIds: nodes.filter((node) => {
    if (!node.filePath) return false;
    if (key === "root") return !node.filePath.includes("/");
    return node.filePath.startsWith(`${key}/`);
  }).filter((node) => ["file", "config", "document", "table", "service", "resource"].includes(node.type)).map((node) => node.id),
})).filter((layer) => layer.nodeIds.length > 0);

// Keep the viewer's layer invariant for unusual generated child nodes (for
// example schema variables or CI resources). They remain visible in the
// graph and fall back to the project foundation layer instead of becoming
// orphaned from the architecture overview.
const assignedLayerNodeIds = new Set(layers.flatMap((layer) => layer.nodeIds));
const foundationLayer = layers.find((layer) => layer.id === "layer:root");
if (foundationLayer) {
  for (const node of nodes) {
    const isFileLevel = ["file", "config", "document", "service", "pipeline", "table", "schema", "resource", "endpoint"].includes(node.type);
    if (isFileLevel && !assignedLayerNodeIds.has(node.id)) {
      foundationLayer.nodeIds.push(node.id);
    }
  }
}

const nodeIdsSet = new Set(nodes.map((node) => node.id));
const tour = [
  { order: 1, title: "项目定位", description: "从项目说明开始，理解企业数字投影、正式数据和管理 Agent 的边界。", nodeIds: ["document:README.md"] },
  { order: 2, title: "前端入口", description: "查看前端如何装配建设端和管理端工作区。", nodeIds: ["file:frontend/src/main.tsx", "file:frontend/src/app/App.tsx"] },
  { order: 3, title: "后端入口", description: "查看后端应用、路由和领域服务的组织方式。", nodeIds: ["file:backend/src/enterprise_insight_backend/main.py", "file:backend/src/enterprise_insight_backend/agent_runtime.py"] },
  { order: 4, title: "企业投影与本体", description: "沿着建模、来源、映射和统一数据的模块查看正式企业层。", nodeIds: nodes.filter((node) => node.filePath?.includes("projection") || node.filePath?.includes("ontology") || node.filePath?.includes("semantic")).slice(0, 8).map((node) => node.id) },
  { order: 5, title: "管理 Agent 与 Action", description: "理解管理查询、复杂任务路由、推演和动作预览的实现边界。", nodeIds: nodes.filter((node) => node.filePath?.includes("agent") || node.filePath?.includes("action")).slice(0, 10).map((node) => node.id) },
  { order: 6, title: "运行方式", description: "最后查看启动器、配置、测试和验收记录。", nodeIds: ["file:scripts/launch.ps1", "file:scripts/start-enterprise-insight.cmd", "document:SIMULATION_STAR_MFG_2026-09-16_RUN.md"] },
].map((step) => ({ ...step, nodeIds: step.nodeIds.filter((id) => nodeIdsSet.has(id)) })).filter((step) => step.nodeIds.length > 0);

mkdirSync(uaDir, { recursive: true });
writeFileSync(outputPath, JSON.stringify({
  version: "1.0.0",
  project: {
    name: "企业数字投影平台",
    languages: Object.keys(scan.stats?.byLanguage ?? {}).sort(),
    frameworks: ["React", "Vite", "FastAPI", "SQLAlchemy"],
    description: "面向企业核心管理层的本地部署数字投影、语义统一和管理 Agent 平台。",
    analyzedAt: new Date().toISOString(),
    gitCommitHash: (() => { try { return execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim(); } catch { return "working-tree"; } })(),
  },
  nodes,
  edges,
  layers,
  tour,
}, null, 2), "utf8");

console.log(JSON.stringify({ outputPath, files: fileMeta.size, nodes: nodes.length, edges: edges.length, layers: layers.length, tourSteps: tour.length }, null, 2));
