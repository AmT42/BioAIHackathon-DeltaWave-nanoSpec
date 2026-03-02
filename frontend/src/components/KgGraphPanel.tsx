"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { Core, EventObjectNode, StylesheetJson } from "cytoscape";

import { EvidenceStrengthPanel } from "@/components/EvidenceStrengthPanel";
import { EvidenceTimelinePanel } from "@/components/EvidenceTimelinePanel";
import { buildCytoscapeElements } from "@/lib/kgGraph";
import { EvidencePanelData } from "@/types/evidence";
import { KgMergedGraph } from "@/types/kgGraph";

type KgGraphPanelProps = {
  graph: KgMergedGraph;
  theme: "dark" | "light";
  evidence: EvidencePanelData | null;
};

const GRAPH_FIT_PADDING = 14;

type CyStylePalette = {
  nodeLabel: string;
  nodeTextOutline: string;
  nodeBorder: string;
  topNodeBorder: string;
  selectedNodeBorder: string;
  edgeLabel: string;
  edgeTextBackground: string;
  edgeLine: string;
  edgeArrow: string;
};

const CY_STYLE_PALETTES: Record<"dark" | "light", CyStylePalette> = {
  dark: {
    nodeLabel: "#ececf1",
    nodeTextOutline: "#0f1318",
    nodeBorder: "#1a232d",
    topNodeBorder: "#ffd84d",
    selectedNodeBorder: "#fff5cf",
    edgeLabel: "#d7dce7",
    edgeTextBackground: "#0f1318",
    edgeLine: "#5f6572",
    edgeArrow: "#5f6572",
  },
  light: {
    nodeLabel: "#0f172a",
    nodeTextOutline: "#f8fafc",
    nodeBorder: "#cbd5e1",
    topNodeBorder: "#d97706",
    selectedNodeBorder: "#9a3412",
    edgeLabel: "#334155",
    edgeTextBackground: "#f8fafc",
    edgeLine: "#64748b",
    edgeArrow: "#64748b",
  },
};

function buildCytoscapeStyles(theme: "dark" | "light") {
  const palette = CY_STYLE_PALETTES[theme];
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        width: "data(size)",
        height: "data(size)",
        "font-size": 9,
        "text-wrap": "wrap",
        "text-max-width": "70px",
        "text-valign": "center",
        "text-halign": "center",
        color: palette.nodeLabel,
        "text-outline-width": 1,
        "text-outline-color": palette.nodeTextOutline,
        "background-color": "data(type_color)",
        "border-width": 1.8,
        "border-color": palette.nodeBorder,
      },
    },
    {
      selector: "node[is_top = 1]",
      style: {
        "border-width": 5.4,
        "border-color": palette.topNodeBorder,
      },
    },
    {
      selector: "node:selected",
      style: {
        "overlay-opacity": 0,
        "border-width": 5.2,
        "border-color": palette.selectedNodeBorder,
        "z-index": 9999,
      },
    },
    {
      selector: "edge",
      style: {
        width: "data(width)",
        label: "data(label)",
        color: palette.edgeLabel,
        "font-size": 8,
        "text-rotation": "autorotate",
        "text-background-color": palette.edgeTextBackground,
        "text-background-opacity": 0.86,
        "text-background-padding": "2px",
        "line-color": palette.edgeLine,
        "curve-style": "bezier",
        "target-arrow-shape": "triangle",
        "target-arrow-color": palette.edgeArrow,
        opacity: 0.78,
      },
    },
  ] as StylesheetJson;
}

export function KgGraphPanel({ graph, theme, evidence }: KgGraphPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const selectedNodeKeyRef = useRef<string | null>(null);
  const topologySignatureRef = useRef<string>("");
  const themeRef = useRef<"dark" | "light">(theme);
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"graph" | "evidence">("graph");

  const { elements, nodeTypeColors } = useMemo(() => buildCytoscapeElements(graph), [graph]);
  const topologySignature = useMemo(() => {
    const nodeKeys = Object.keys(graph.nodes_by_key).sort();
    const edgeKeys = Object.keys(graph.edges_by_key).sort();
    return `n:${nodeKeys.join("|")}::e:${edgeKeys.join("|")}`;
  }, [graph.nodes_by_key, graph.edges_by_key]);
  const selectedNode = selectedNodeKey ? graph.nodes_by_key[selectedNodeKey] ?? null : null;
  const hasGraph = graph.summary.node_count > 0;
  const hasEvidence = Boolean(evidence);

  function fitToCanvas(cy: Core) {
    if (cy.nodes().length === 0) return;
    cy.fit(cy.elements(), GRAPH_FIT_PADDING);
    cy.center(cy.elements());
  }

  useEffect(() => {
    let cancelled = false;

    async function initialize() {
      if (!containerRef.current || cyRef.current) return;
      const mod = await import("cytoscape");
      if (cancelled || !containerRef.current) return;
      const cytoscape = mod.default;
      const cy = cytoscape({
        container: containerRef.current,
        elements: [],
        style: buildCytoscapeStyles(themeRef.current),
        wheelSensitivity: 0.18,
        boxSelectionEnabled: false,
        selectionType: "single",
        userPanningEnabled: true,
        userZoomingEnabled: true,
      });

      cy.on("tap", "node", (evt: EventObjectNode) => {
        setSelectedNodeKey(evt.target.id());
      });
      cy.on("tap", (evt) => {
        if (evt.target === cy) {
          setSelectedNodeKey(null);
        }
      });

      cyRef.current = cy;
    }

    initialize();
    return () => {
      cancelled = true;
      if (cyRef.current) {
        cyRef.current.destroy();
        cyRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    themeRef.current = theme;
    const cy = cyRef.current;
    if (!cy) return;
    cy.style(buildCytoscapeStyles(theme));
  }, [theme]);

  useEffect(() => {
    if (!hasEvidence && activeTab === "evidence") {
      setActiveTab("graph");
    }
  }, [activeTab, hasEvidence]);

  useEffect(() => {
    if (activeTab !== "graph") return;
    const cy = cyRef.current;
    if (!cy) return;
    const raf = window.requestAnimationFrame(() => {
      cy.resize();
      fitToCanvas(cy);
    });
    return () => window.cancelAnimationFrame(raf);
  }, [activeTab]);

  useEffect(() => {
    selectedNodeKeyRef.current = selectedNodeKey;
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.nodes().unselect();
      if (!selectedNodeKey) return;
      const node = cy.getElementById(selectedNodeKey);
      if (!node.empty()) {
        node.select();
      }
    });
  }, [selectedNodeKey]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    const hadNodes = cy.nodes().length > 0;
    const topologyChanged = topologySignatureRef.current !== topologySignature;
    topologySignatureRef.current = topologySignature;
    const previousPositions = new Map<string, { x: number; y: number }>();
    if (hadNodes) {
      cy.nodes().forEach((node) => {
        previousPositions.set(node.id(), node.position());
      });
    }

    cy.batch(() => {
      cy.elements().remove();
      cy.add(elements as never);
      if (previousPositions.size > 0) {
        cy.nodes().forEach((node) => {
          const previous = previousPositions.get(node.id());
          if (previous) {
            node.position(previous);
          }
        });
      }
    });

    if (cy.nodes().length > 0) {
      if (!hadNodes || topologyChanged) {
        const layout = cy.layout({
          name: "cose",
          animate: false,
          fit: false,
          randomize: false,
          padding: GRAPH_FIT_PADDING,
          nodeRepulsion: 1800,
          edgeElasticity: 90,
          idealEdgeLength: 58,
          gravity: 0.25,
        });
        layout.run();
      }
      fitToCanvas(cy);
    }

    const preservedSelection = selectedNodeKeyRef.current;
    if (preservedSelection && cy.getElementById(preservedSelection).empty()) {
      setSelectedNodeKey(null);
      return;
    }
    if (preservedSelection) {
      cy.batch(() => {
        cy.nodes().unselect();
        cy.getElementById(preservedSelection).select();
      });
    }
  }, [elements, topologySignature]);

  useEffect(() => {
    if (activeTab !== "graph") return;
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver(() => {
      const cy = cyRef.current;
      if (!cy) return;
      fitToCanvas(cy);
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [activeTab]);

  function handleFit() {
    const cy = cyRef.current;
    if (!cy || cy.nodes().length === 0) return;
    fitToCanvas(cy);
  }

  function handleRecenter() {
    const cy = cyRef.current;
    if (!cy || cy.nodes().length === 0) return;
    cy.center(cy.elements());
  }

  return (
    <aside className="kg-panel">
      <div className="kg-panel__header">
        <div className="kg-panel__header-main">
          <div>
            <h3 className="kg-panel__title">
              {activeTab === "graph" ? "Knowledge Graph" : "Evidence Intelligence"}
            </h3>
            <p className="kg-panel__subtitle">
              {activeTab === "graph"
                ? `${graph.summary.node_count} nodes, ${graph.summary.edge_count} edges`
                : evidence
                  ? `Score ${Math.round(evidence.summary.score)} (${evidence.summary.label}, ${evidence.summary.confidence})`
                  : "No structured evidence data yet"}
            </p>
          </div>
          <div className="kg-panel__tabs">
            <button
              type="button"
              className={`kg-panel__tab ${activeTab === "graph" ? "kg-panel__tab--active" : ""}`}
              onClick={() => setActiveTab("graph")}
            >
              Graph
            </button>
            <button
              type="button"
              className={`kg-panel__tab ${activeTab === "evidence" ? "kg-panel__tab--active" : ""}`}
              onClick={() => setActiveTab("evidence")}
              disabled={!hasEvidence}
              title={!hasEvidence ? "Evidence tab is available after evidence_render_report runs." : undefined}
            >
              Evidence
            </button>
          </div>
        </div>
        {activeTab === "graph" && (
          <div className="kg-panel__actions">
            <button type="button" onClick={handleRecenter} className="kg-panel__btn">
              Recenter
            </button>
            <button type="button" onClick={handleFit} className="kg-panel__btn kg-panel__btn--primary">
              Fit graph
            </button>
          </div>
        )}
      </div>

      <div className={`kg-panel__graph-view ${activeTab !== "graph" ? "kg-panel__view--hidden" : ""}`}>
        <div className="kg-panel__legend">
          <div className="kg-panel__legend-line">
            <span className="kg-panel__top-dot" />
            <span className="kg-panel__legend-label">Most important node in each type (yellow ring)</span>
          </div>
          <div className="kg-panel__types">
            {Object.entries(nodeTypeColors).map(([nodeType, color]) => (
              <span key={nodeType} className="kg-panel__type-chip">
                <span className="kg-panel__type-dot" style={{ backgroundColor: color }} />
                {nodeType}
              </span>
            ))}
          </div>
        </div>

        <div className="kg-panel__canvas-wrap">
          {!hasGraph && (
            <div className="kg-panel__empty">
              Graph will populate after successful KG tool calls in this thread.
            </div>
          )}
          <div ref={containerRef} className={`kg-panel__canvas ${!hasGraph ? "kg-panel__canvas--hidden" : ""}`} />
        </div>

        <div className="kg-panel__inspector">
          {!selectedNode && <p className="kg-panel__inspector-empty">Select a node to inspect metrics.</p>}
          {selectedNode && (
            <>
              <div className="kg-panel__inspector-header">
                <div className="kg-panel__inspector-name">{selectedNode.name}</div>
                <div className="kg-panel__inspector-rank">
                  {selectedNode.node_type} rank #{selectedNode.score.rank_in_type}
                </div>
              </div>
              <div className="kg-panel__inspector-grid">
                <div>Score: {selectedNode.score.importance_score.toFixed(3)}</div>
                <div>PageRank: {selectedNode.metrics.pagerank.toFixed(3)}</div>
                <div>Weighted degree: {selectedNode.metrics.weighted_degree.toFixed(3)}</div>
                <div>Edge-type diversity: {selectedNode.metrics.edge_type_diversity}</div>
                <div>In / Out degree: {selectedNode.metrics.in_degree} / {selectedNode.metrics.out_degree}</div>
                <div>
                  Weighted In / Out: {selectedNode.metrics.weighted_in_degree.toFixed(2)} /{" "}
                  {selectedNode.metrics.weighted_out_degree.toFixed(2)}
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      <div className={`kg-panel__evidence-view ${activeTab !== "evidence" ? "kg-panel__view--hidden" : ""}`}>
        {evidence ? (
          <>
            <EvidenceStrengthPanel evidence={evidence} />
            <EvidenceTimelinePanel evidence={evidence} />
          </>
        ) : (
          <div className="evidence-card__empty">No structured evidence data is available for this thread.</div>
        )}
      </div>
    </aside>
  );
}
