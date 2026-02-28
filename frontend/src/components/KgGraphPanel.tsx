"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { Core, EventObjectNode } from "cytoscape";

import { buildCytoscapeElements } from "@/lib/kgGraph";
import { KgMergedGraph } from "@/types/kgGraph";

type KgGraphPanelProps = {
  graph: KgMergedGraph;
};

export function KgGraphPanel({ graph }: KgGraphPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const selectedNodeKeyRef = useRef<string | null>(null);
  const topologySignatureRef = useRef<string>("");
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);

  const { elements, nodeTypeColors } = useMemo(() => buildCytoscapeElements(graph), [graph]);
  const topologySignature = useMemo(() => {
    const nodeKeys = Object.keys(graph.nodes_by_key).sort();
    const edgeKeys = Object.keys(graph.edges_by_key).sort();
    return `n:${nodeKeys.join("|")}::e:${edgeKeys.join("|")}`;
  }, [graph.nodes_by_key, graph.edges_by_key]);
  const selectedNode = selectedNodeKey ? graph.nodes_by_key[selectedNodeKey] ?? null : null;
  const hasGraph = graph.summary.node_count > 0;

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
        style: [
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
              color: "#ececf1",
              "text-outline-width": 1,
              "text-outline-color": "#0f1318",
              "background-color": "data(color)",
              "border-width": "mapData(score, 0, 1, 1.3, 3.8)",
              "border-color": "data(border_color)",
            },
          },
          {
            selector: "node[is_top = 1]",
            style: {
              "border-width": 4.8,
              "border-color": "#ffe08c",
            },
          },
          {
            selector: "node:selected",
            style: {
              "overlay-opacity": 0,
              "border-width": 5.2,
              "border-color": "#fff5cf",
              "z-index": 9999,
            },
          },
          {
            selector: "edge",
            style: {
              width: "data(width)",
              "line-color": "#5f6572",
              "curve-style": "bezier",
              "target-arrow-shape": "triangle",
              "target-arrow-color": "#5f6572",
              opacity: 0.78,
            },
          },
        ],
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
    const previousZoom = cy.zoom();
    const previousPan = cy.pan();
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
          fit: !hadNodes,
          randomize: false,
          padding: 36,
          nodeRepulsion: 4200,
          edgeElasticity: 120,
          idealEdgeLength: 90,
        });
        layout.run();
        if (!hadNodes) {
          cy.fit(cy.elements(), 36);
        } else {
          cy.zoom(previousZoom);
          cy.pan(previousPan);
        }
      } else {
        cy.zoom(previousZoom);
        cy.pan(previousPan);
      }
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

  function handleFit() {
    const cy = cyRef.current;
    if (!cy || cy.nodes().length === 0) return;
    cy.fit(cy.elements(), 36);
  }

  function handleRecenter() {
    const cy = cyRef.current;
    if (!cy || cy.nodes().length === 0) return;
    cy.center(cy.elements());
  }

  return (
    <aside className="kg-panel">
      <div className="kg-panel__header">
        <div>
          <h3 className="kg-panel__title">Knowledge Graph</h3>
          <p className="kg-panel__subtitle">
            {graph.summary.node_count} nodes, {graph.summary.edge_count} edges
          </p>
        </div>
        <div className="kg-panel__actions">
          <button type="button" onClick={handleRecenter} className="kg-panel__btn">
            Recenter
          </button>
          <button type="button" onClick={handleFit} className="kg-panel__btn kg-panel__btn--primary">
            Fit graph
          </button>
        </div>
      </div>

      <div className="kg-panel__legend">
        <div className="kg-panel__legend-line kg-panel__legend-line--importance">
          <span className="kg-panel__legend-label">Importance</span>
          <div className="kg-panel__gradient-wrap">
            <span className="kg-panel__gradient" />
            <span className="kg-panel__gradient-ticks">
              <span>0.0</span>
              <span>0.5</span>
              <span>1.0</span>
            </span>
          </div>
          <span className="kg-panel__legend-label">Heat + size</span>
        </div>
        <div className="kg-panel__legend-line">
          <span className="kg-panel__top-dot" />
          <span className="kg-panel__legend-label">Top-ranked node per type</span>
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
    </aside>
  );
}
