import { useEffect, useRef } from "react";
import cytoscape, { type Core, type EventObjectNode } from "cytoscape";
import elk from "cytoscape-elk";
import type { Graph } from "../../api/types";
import { toProjectionElements, type ProjectionElementData } from "./toElements";

cytoscape.use(elk);

interface ProjectionGraphProps {
  graph: Graph;
  search: string;
  onSelect: (data: ProjectionElementData | null) => void;
}

export function ProjectionGraph({ graph, search, onSelect }: ProjectionGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Core | null>(null);
  const hasSearchResults = !search.trim() || graph.entities.some((entity) => `${entity.name} ${entity.type_key}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));

  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: toProjectionElements(graph, search),
      minZoom: 0.25,
      maxZoom: 2.2,
      style: [
        {
          selector: "node.entity",
          style: {
            "background-color": "#f8fbf8",
            "border-color": "#2f715d",
            "border-width": 2,
            color: "#17251f",
            label: "data(label)",
            shape: "round-rectangle",
            width: 142,
            height: 52,
            "font-size": 12,
            "font-weight": 600,
            "text-wrap": "wrap",
            "text-max-width": "126px",
          },
        },
        {
          selector: "node.relation",
          style: {
            "background-color": "#cf9b52",
            "border-color": "#8e642e",
            "border-width": 1,
            color: "#36220b",
            label: "data(label)",
            shape: "diamond",
            width: 62,
            height: 62,
            "font-size": 9,
            "text-wrap": "wrap",
            "text-max-width": "58px",
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.6,
            "line-color": "#9dad9f",
            "target-arrow-color": "#9dad9f",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: "data(role)",
            "font-size": 8,
            color: "#68756e",
            "text-background-color": "#f4f6f1",
            "text-background-opacity": 0.9,
            "text-background-padding": "2px",
          },
        },
        {
          selector: ".viewpoint-observed",
          style: { "border-color": "#2674a8", "line-color": "#7ca9c4" },
        },
        {
          selector: ".viewpoint-system_bound",
          style: { "border-color": "#7958a6", "line-color": "#a994c5" },
        },
        {
          selector: ":selected",
          style: { "border-color": "#e27746", "border-width": 4, "overlay-opacity": 0 },
        },
      ],
    });

    graphRef.current = cy;
    cy.on("tap", "node", (event: EventObjectNode) => {
      onSelect(event.target.data() as ProjectionElementData);
    });
    cy.on("tap", (event) => {
      if (event.target === cy) onSelect(null);
    });
    const layout = cy.layout({ name: "elk", fit: true, padding: 50 } as cytoscape.LayoutOptions);
    const layoutFrame = window.requestAnimationFrame(() => layout.run());

    return () => {
      window.cancelAnimationFrame(layoutFrame);
      layout.stop();
      cy.destroy();
      graphRef.current = null;
    };
  }, [graph, onSelect, search]);

  return (
    <div className="projection-canvas-wrap">
      <div ref={containerRef} className="projection-canvas" aria-label="企业投影关系图" />
      {!graph.entities.length && <div className="canvas-empty">当前版本还没有企业对象。</div>}
      {graph.entities.length > 0 && !hasSearchResults && <div className="canvas-empty">没有匹配的企业对象或关系参与者。</div>}
      <button className="canvas-control" onClick={() => graphRef.current?.fit(undefined, 48)}>适应画布</button>
    </div>
  );
}
