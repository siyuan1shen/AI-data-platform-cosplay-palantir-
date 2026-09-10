import type { ElementDefinition } from "cytoscape";
import type { Graph } from "../../api/types";

export interface ProjectionElementData {
  id: string;
  modelId: string;
  modelKind: "entity" | "relation";
  label: string;
  typeKey: string;
  status: string;
  viewpoint: string;
  properties: Record<string, unknown>;
  evidenceCount: number;
  designMembership?: string;
  observedName?: string | null;
  observedProperties?: Record<string, unknown>;
  comparison?: Record<string, unknown>;
  role?: string;
  source?: string;
  target?: string;
}

export function toProjectionElements(graph: Graph, search = ""): ElementDefinition[] {
  const query = search.trim().toLocaleLowerCase();
  const visibleEntities = graph.entities.filter((entity) => {
    if (!query) return true;
    return `${entity.name} ${entity.type_key}`.toLocaleLowerCase().includes(query);
  });
  const visibleIds = new Set(visibleEntities.map((entity) => entity.id));

  const nodes: ElementDefinition[] = visibleEntities.map((entity) => ({
    group: "nodes",
    data: {
      id: `entity:${entity.id}`,
      modelId: entity.id,
      modelKind: "entity",
      label: entity.name,
      typeKey: entity.type_key,
      status: entity.status,
      viewpoint: entity.viewpoint ?? "DESIGNED",
      properties: entity.properties ?? {},
      evidenceCount: entity.evidence?.length ?? 0,
      designMembership: entity.design_membership,
      observedName: entity.observed_name,
      observedProperties: entity.observed_properties ?? {},
      comparison: entity.comparison ?? {},
    } satisfies ProjectionElementData,
    classes: `entity viewpoint-${(entity.viewpoint ?? "DESIGNED").toLowerCase()}`,
  }));

  const relationNodes: ElementDefinition[] = [];
  const edges: ElementDefinition[] = [];
  graph.relations.forEach((relation) => {
    const participants = relation.participants.filter((item) => visibleIds.has(item.entity_id));
    if (participants.length < 2) return;
    const relationNodeId = `relation:${relation.id}`;
    relationNodes.push({
      group: "nodes",
      data: {
        id: relationNodeId,
        modelId: relation.id,
        modelKind: "relation",
        label: relation.name || relation.type_key,
        typeKey: relation.type_key,
        status: relation.status,
        viewpoint: relation.viewpoint,
        properties: relation.properties,
        evidenceCount: relation.evidence.length,
      } satisfies ProjectionElementData,
      classes: `relation viewpoint-${relation.viewpoint.toLowerCase()}`,
    });
    participants.forEach((participant, index) => {
      edges.push({
        group: "edges",
        data: {
          id: `participant:${relation.id}:${participant.entity_id}:${index}`,
          modelId: relation.id,
          modelKind: "relation",
          label: participant.role_key,
          typeKey: relation.type_key,
          status: relation.status,
          viewpoint: relation.viewpoint,
          properties: {},
          evidenceCount: 0,
          role: participant.role_key,
          source: `entity:${participant.entity_id}`,
          target: relationNodeId,
        } satisfies ProjectionElementData,
      });
    });
  });

  return [...nodes, ...relationNodes, ...edges];
}
