import {
  KgCytoscapeElement,
  KgEdge,
  KgMergedGraph,
  KgNode,
  KgSubgraph,
  KgSubgraphEdge,
  KgSubgraphNode,
} from "@/types/kgGraph";

const KG_TOOL_NAMES = new Set(["kg_query", "kg_cypher_execute"]);

const EMPTY_METRICS = {
  pagerank: 0,
  weighted_degree: 0,
  edge_type_diversity: 0,
  in_degree: 0,
  out_degree: 0,
  weighted_in_degree: 0,
  weighted_out_degree: 0,
};

const EMPTY_SCORE = {
  importance_score: 0,
  normalized_pagerank: 0,
  normalized_weighted_degree: 0,
  normalized_edge_type_diversity: 0,
  rank_in_type: 0,
  is_top_in_type: false,
};

const TYPE_COLORS = [
  "#4F6D7A",
  "#5C8D89",
  "#7C6A8B",
  "#9A6F5A",
  "#6A7E9A",
  "#7F8F5A",
  "#8A5A7A",
  "#6B8A7A",
];

export function createEmptyKgMergedGraph(): KgMergedGraph {
  return {
    nodes_by_key: {},
    edges_by_key: {},
    summary: {
      node_count: 0,
      edge_count: 0,
      node_type_count: 0,
    },
    scoring: {
      scope: "query_local",
      per_node_type_ranking: true,
      weights: {
        pagerank: 0.55,
        weighted_degree: 0.3,
        edge_type_diversity: 0.15,
      },
      edge_weighting: "confidence_aware_fallback_1.0",
    },
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function toStringValue(value: unknown, fallback = ""): string {
  if (typeof value === "string") return value.trim() || fallback;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return fallback;
}

function toNumberValue(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => toStringValue(item))
    .filter((item) => item.length > 0);
}

function normalizeSubgraphNode(raw: unknown): KgSubgraphNode | null {
  const row = asRecord(raw);
  if (!row) return null;
  const key = toStringValue(row.key);
  if (!key) return null;
  const labels = toStringArray(row.labels);
  const nodeType = toStringValue(row.node_type, labels[0] ?? "Unknown");
  const fallbackId = key;
  const id = toStringValue(row.id, fallbackId);
  const name = toStringValue(row.name, id);
  return {
    key,
    id,
    name,
    node_type: nodeType || "Unknown",
    labels,
  };
}

function normalizeSubgraphEdge(raw: unknown): KgSubgraphEdge | null {
  const row = asRecord(raw);
  if (!row) return null;
  const source = toStringValue(row.source);
  const target = toStringValue(row.target);
  const type = toStringValue(row.type, "RELATED_TO");
  if (!source || !target) return null;
  return {
    source,
    target,
    type,
    weight: Math.max(0, toNumberValue(row.weight, 1)),
    confidence_key: toStringValue(row.confidence_key, "") || null,
  };
}

function normalizeSubgraph(raw: unknown): KgSubgraph | null {
  const row = asRecord(raw);
  if (!row) return null;
  const nodes = asArray(row.nodes)
    .map(normalizeSubgraphNode)
    .filter((item): item is KgSubgraphNode => Boolean(item));
  const edges = asArray(row.edges)
    .map(normalizeSubgraphEdge)
    .filter((item): item is KgSubgraphEdge => Boolean(item));
  const summary = asRecord(row.summary);
  return {
    summary: {
      node_count: toNumberValue(summary?.node_count, nodes.length),
      edge_count: toNumberValue(summary?.edge_count, edges.length),
    },
    nodes,
    edges,
  };
}

function isSuccessfulResult(toolResult: Record<string, unknown>): boolean {
  const status = toStringValue(toolResult.status).toLowerCase();
  return status === "success" || status === "completed";
}

function stableNodeType(nodeType: string): string {
  return nodeType.trim() || "Unknown";
}

function edgeKey(edge: KgSubgraphEdge): string {
  return `${edge.source}|${edge.type}|${edge.target}`;
}

function hashString(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash << 5) - hash + value.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}

export function nodeTypeColor(nodeType: string): string {
  const clean = stableNodeType(nodeType);
  return TYPE_COLORS[hashString(clean) % TYPE_COLORS.length];
}

function clamp(value: number, min: number, max: number): number {
  if (value < min) return min;
  if (value > max) return max;
  return value;
}

function round6(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000;
}

function minMaxNormalize(values: Record<string, number>, keys: string[]): Record<string, number> {
  if (keys.length === 0) return {};
  const points = keys.map((key) => values[key] ?? 0);
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min;
  if (span <= 1e-12) {
    const constant = max > 0 ? 1 : 0;
    const out: Record<string, number> = {};
    for (const key of keys) out[key] = constant;
    return out;
  }
  const out: Record<string, number> = {};
  for (const key of keys) out[key] = (values[key] - min) / span;
  return out;
}

function computeWeightedPageRank(
  nodeKeys: string[],
  adjacency: Record<string, Record<string, number>>,
  outWeight: Record<string, number>,
  damping = 0.85,
  maxIter = 100,
  tol = 1e-9
): Record<string, number> {
  if (nodeKeys.length === 0) return {};
  const n = nodeKeys.length;
  const base = 1 / n;
  let ranks: Record<string, number> = {};
  for (const key of nodeKeys) ranks[key] = base;
  const teleport = (1 - damping) / n;

  for (let iter = 0; iter < maxIter; iter += 1) {
    let dangling = 0;
    for (const key of nodeKeys) {
      if ((outWeight[key] ?? 0) <= 0) dangling += ranks[key] ?? 0;
    }
    const next: Record<string, number> = {};
    for (const key of nodeKeys) {
      next[key] = teleport + (damping * dangling) / n;
    }
    for (const source of Object.keys(adjacency)) {
      const out = outWeight[source] ?? 0;
      if (out <= 0) continue;
      const sourceRank = ranks[source] ?? 0;
      if (sourceRank <= 0) continue;
      const scale = (damping * sourceRank) / out;
      for (const [target, weight] of Object.entries(adjacency[source])) {
        next[target] = (next[target] ?? 0) + scale * weight;
      }
    }
    let delta = 0;
    for (const key of nodeKeys) delta += Math.abs((next[key] ?? 0) - (ranks[key] ?? 0));
    ranks = next;
    if (delta < tol) break;
  }
  return ranks;
}

function recomputeSummary(graph: KgMergedGraph): KgMergedGraph["summary"] {
  const nodeKeys = Object.keys(graph.nodes_by_key);
  const edgeKeys = Object.keys(graph.edges_by_key);
  const nodeTypes = new Set(nodeKeys.map((key) => stableNodeType(graph.nodes_by_key[key].node_type)));
  return {
    node_count: nodeKeys.length,
    edge_count: edgeKeys.length,
    node_type_count: nodeTypes.size,
  };
}

export function extractKgSubgraphFromToolResult(
  toolName: string | undefined,
  toolResult: Record<string, unknown> | null | undefined
): KgSubgraph | null {
  const result = asRecord(toolResult);
  if (!result || !isSuccessfulResult(result)) return null;
  const output = asRecord(result.output);
  const normalizedTool = (toolName ?? "").trim();
  const isKgByToolName = normalizedTool.length > 0 && KG_TOOL_NAMES.has(normalizedTool);
  if (!isKgByToolName) {
    const sourceMeta = asRecord(output?.source_meta);
    const source = toStringValue(sourceMeta?.source);
    if (source !== "crossbar_kg") return null;
  }
  const data = asRecord(output?.data);
  return normalizeSubgraph(data?.subgraph);
}

export function mergeSubgraphIntoThreadGraph(graph: KgMergedGraph, subgraph: KgSubgraph): KgMergedGraph {
  const nextNodes = { ...graph.nodes_by_key };
  const nextEdges = { ...graph.edges_by_key };

  for (const node of subgraph.nodes) {
    const key = node.key;
    const existing = nextNodes[key];
    if (!existing) {
      nextNodes[key] = {
        key,
        id: node.id || key,
        name: node.name || node.id || key,
        node_type: stableNodeType(node.node_type),
        labels: [...new Set(node.labels)],
        seen_count: 1,
        metrics: { ...EMPTY_METRICS },
        score: { ...EMPTY_SCORE },
      };
      continue;
    }
    nextNodes[key] = {
      ...existing,
      id: node.id || existing.id,
      name: node.name || existing.name,
      node_type: stableNodeType(node.node_type || existing.node_type),
      labels: [...new Set([...(existing.labels || []), ...node.labels])],
      seen_count: existing.seen_count + 1,
    };
  }

  for (const edge of subgraph.edges) {
    const key = edgeKey(edge);
    const existing = nextEdges[key];
    if (!existing) {
      nextEdges[key] = {
        key,
        source: edge.source,
        target: edge.target,
        type: edge.type,
        weight: Math.max(0, edge.weight || 0),
        confidence_key: edge.confidence_key ?? null,
        seen_count: 1,
      };
      continue;
    }
    nextEdges[key] = {
      ...existing,
      weight: Math.max(existing.weight, Math.max(0, edge.weight || 0)),
      confidence_key: edge.confidence_key ?? existing.confidence_key ?? null,
      seen_count: existing.seen_count + 1,
    };
  }

  const merged: KgMergedGraph = {
    ...graph,
    nodes_by_key: nextNodes,
    edges_by_key: nextEdges,
  };
  return {
    ...merged,
    summary: recomputeSummary(merged),
  };
}

export function recomputeMergedImportanceStats(graph: KgMergedGraph): KgMergedGraph {
  const nodeKeys = Object.keys(graph.nodes_by_key);
  if (nodeKeys.length === 0) {
    return {
      ...graph,
      summary: recomputeSummary(graph),
    };
  }

  const outWeight: Record<string, number> = {};
  const inWeight: Record<string, number> = {};
  const outDegree: Record<string, number> = {};
  const inDegree: Record<string, number> = {};
  const edgeTypeSets: Record<string, Set<string>> = {};
  const adjacency: Record<string, Record<string, number>> = {};

  for (const key of nodeKeys) {
    outWeight[key] = 0;
    inWeight[key] = 0;
    outDegree[key] = 0;
    inDegree[key] = 0;
    edgeTypeSets[key] = new Set<string>();
    adjacency[key] = {};
  }

  for (const edge of Object.values(graph.edges_by_key)) {
    if (!graph.nodes_by_key[edge.source] || !graph.nodes_by_key[edge.target]) continue;
    const weight = Math.max(0, edge.weight || 0);
    outWeight[edge.source] += weight;
    inWeight[edge.target] += weight;
    outDegree[edge.source] += 1;
    inDegree[edge.target] += 1;
    edgeTypeSets[edge.source].add(edge.type);
    edgeTypeSets[edge.target].add(edge.type);
    adjacency[edge.source][edge.target] = (adjacency[edge.source][edge.target] ?? 0) + weight;
  }

  const pagerank = computeWeightedPageRank(nodeKeys, adjacency, outWeight);
  const weightedDegree: Record<string, number> = {};
  const diversity: Record<string, number> = {};
  for (const key of nodeKeys) {
    weightedDegree[key] = (outWeight[key] ?? 0) + (inWeight[key] ?? 0);
    diversity[key] = edgeTypeSets[key].size;
  }

  const byType: Record<string, string[]> = {};
  for (const key of nodeKeys) {
    const nodeType = stableNodeType(graph.nodes_by_key[key].node_type);
    if (!byType[nodeType]) byType[nodeType] = [];
    byType[nodeType].push(key);
  }

  const nextNodes: Record<string, KgNode> = {};
  for (const nodeType of Object.keys(byType)) {
    const keys = byType[nodeType];
    const normPagerank = minMaxNormalize(pagerank, keys);
    const normWeightedDegree = minMaxNormalize(weightedDegree, keys);
    const normDiversity = minMaxNormalize(diversity, keys);

    const scored = keys.map((key) => {
      const score =
        0.55 * (normPagerank[key] ?? 0) +
        0.3 * (normWeightedDegree[key] ?? 0) +
        0.15 * (normDiversity[key] ?? 0);
      return {
        key,
        score,
      };
    });

    scored.sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      const pagerankDelta = (pagerank[b.key] ?? 0) - (pagerank[a.key] ?? 0);
      if (pagerankDelta !== 0) return pagerankDelta;
      const degreeDelta = (weightedDegree[b.key] ?? 0) - (weightedDegree[a.key] ?? 0);
      if (degreeDelta !== 0) return degreeDelta;
      return (graph.nodes_by_key[a.key].name || a.key).localeCompare(graph.nodes_by_key[b.key].name || b.key);
    });

    const rankByKey: Record<string, number> = {};
    for (let i = 0; i < scored.length; i += 1) rankByKey[scored[i].key] = i + 1;
    const topKey = scored[0]?.key;

    for (const key of keys) {
      const base = graph.nodes_by_key[key];
      const importance = scored.find((row) => row.key === key)?.score ?? 0;
      nextNodes[key] = {
        ...base,
        metrics: {
          pagerank: round6(pagerank[key] ?? 0),
          weighted_degree: round6(weightedDegree[key] ?? 0),
          edge_type_diversity: Math.trunc(diversity[key] ?? 0),
          in_degree: Math.trunc(inDegree[key] ?? 0),
          out_degree: Math.trunc(outDegree[key] ?? 0),
          weighted_in_degree: round6(inWeight[key] ?? 0),
          weighted_out_degree: round6(outWeight[key] ?? 0),
        },
        score: {
          importance_score: round6(clamp(importance, 0, 1)),
          normalized_pagerank: round6(clamp(normPagerank[key] ?? 0, 0, 1)),
          normalized_weighted_degree: round6(clamp(normWeightedDegree[key] ?? 0, 0, 1)),
          normalized_edge_type_diversity: round6(clamp(normDiversity[key] ?? 0, 0, 1)),
          rank_in_type: rankByKey[key] ?? 0,
          is_top_in_type: topKey === key,
        },
      };
    }
  }

  return {
    ...graph,
    nodes_by_key: nextNodes,
    summary: recomputeSummary({ ...graph, nodes_by_key: nextNodes }),
  };
}

function interpolateHexColor(lowHex: string, highHex: string, t: number): string {
  const tt = clamp(t, 0, 1);
  const low = lowHex.replace("#", "");
  const high = highHex.replace("#", "");
  const lr = parseInt(low.slice(0, 2), 16);
  const lg = parseInt(low.slice(2, 4), 16);
  const lb = parseInt(low.slice(4, 6), 16);
  const hr = parseInt(high.slice(0, 2), 16);
  const hg = parseInt(high.slice(2, 4), 16);
  const hb = parseInt(high.slice(4, 6), 16);
  const r = Math.round(lr + (hr - lr) * tt);
  const g = Math.round(lg + (hg - lg) * tt);
  const b = Math.round(lb + (hb - lb) * tt);
  return `#${r.toString(16).padStart(2, "0")}${g.toString(16).padStart(2, "0")}${b.toString(16).padStart(2, "0")}`;
}

export function buildCytoscapeElements(graph: KgMergedGraph): {
  elements: KgCytoscapeElement[];
  nodeTypeColors: Record<string, string>;
} {
  const nodeValues = Object.values(graph.nodes_by_key);
  const edgeValues = Object.values(graph.edges_by_key);
  const nodeTypeColors: Record<string, string> = {};
  const elements: KgCytoscapeElement[] = [];

  for (const node of nodeValues) {
    const nodeType = stableNodeType(node.node_type);
    nodeTypeColors[nodeType] = nodeTypeColor(nodeType);
    const score = clamp(node.score.importance_score ?? 0, 0, 1);
    const size = round6(24 + score * 28);
    const color = interpolateHexColor("#3B4A5A", "#FF9A5A", score);
    elements.push({
      data: {
        id: node.key,
        label: node.name || node.id || node.key,
        node_type: nodeType,
        score: score,
        size: size,
        color,
        is_top: node.score.is_top_in_type ? 1 : 0,
        rank: node.score.rank_in_type,
        pagerank: node.metrics.pagerank,
        weighted_degree: node.metrics.weighted_degree,
        edge_type_diversity: node.metrics.edge_type_diversity,
        in_degree: node.metrics.in_degree,
        out_degree: node.metrics.out_degree,
        weighted_in_degree: node.metrics.weighted_in_degree,
        weighted_out_degree: node.metrics.weighted_out_degree,
      },
    });
  }

  const edgeWeights = edgeValues.map((edge) => edge.weight);
  const minEdgeWeight = edgeWeights.length > 0 ? Math.min(...edgeWeights) : 0;
  const maxEdgeWeight = edgeWeights.length > 0 ? Math.max(...edgeWeights) : 0;
  const span = maxEdgeWeight - minEdgeWeight;

  for (const edge of edgeValues) {
    const normalized = span <= 1e-12 ? 0.5 : (edge.weight - minEdgeWeight) / span;
    elements.push({
      data: {
        id: `edge:${edge.key}`,
        source: edge.source,
        target: edge.target,
        edge_type: edge.type,
        weight: round6(edge.weight),
        width: round6(1.2 + normalized * 4),
      },
    });
  }

  return { elements, nodeTypeColors };
}
