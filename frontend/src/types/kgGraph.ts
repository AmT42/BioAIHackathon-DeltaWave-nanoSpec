export type KgSubgraphNode = {
  key: string;
  id: string;
  name: string;
  node_type: string;
  labels: string[];
};

export type KgSubgraphEdge = {
  source: string;
  target: string;
  type: string;
  weight: number;
  confidence_key?: string | null;
};

export type KgSubgraph = {
  summary: {
    node_count: number;
    edge_count: number;
  };
  nodes: KgSubgraphNode[];
  edges: KgSubgraphEdge[];
};

export type KgNodeMetrics = {
  pagerank: number;
  weighted_degree: number;
  edge_type_diversity: number;
  in_degree: number;
  out_degree: number;
  weighted_in_degree: number;
  weighted_out_degree: number;
};

export type KgNodeScore = {
  importance_score: number;
  normalized_pagerank: number;
  normalized_weighted_degree: number;
  normalized_edge_type_diversity: number;
  rank_in_type: number;
  is_top_in_type: boolean;
};

export type KgNode = {
  key: string;
  id: string;
  name: string;
  node_type: string;
  labels: string[];
  seen_count: number;
  metrics: KgNodeMetrics;
  score: KgNodeScore;
};

export type KgEdge = {
  key: string;
  source: string;
  target: string;
  type: string;
  weight: number;
  confidence_key?: string | null;
  seen_count: number;
};

export type KgMergedGraph = {
  nodes_by_key: Record<string, KgNode>;
  edges_by_key: Record<string, KgEdge>;
  summary: {
    node_count: number;
    edge_count: number;
    node_type_count: number;
  };
  scoring: {
    scope: "query_local";
    per_node_type_ranking: true;
    weights: {
      pagerank: number;
      weighted_degree: number;
      edge_type_diversity: number;
    };
    edge_weighting: "confidence_aware_fallback_1.0";
  };
};

export type KgCytoscapeElement = {
  data: Record<string, string | number>;
};
