import { describe, expect, it } from "vitest";
import type { Graph } from "../../api/types";
import { toProjectionElements } from "./toElements";

const graph: Graph = {
  project_id: "20000000-0000-4000-8000-000000000001",
  revision: 1,
  release_id: null,
  scenario_id: null,
  entities: [
    {
      id: "30000000-0000-4000-8000-000000000001",
      project_id: "20000000-0000-4000-8000-000000000001",
      type_key: "role",
      name: "销售经理",
      stable_key: null,
      design_membership: "MODELED",
      properties: {},
      viewpoint: "DESIGNED",
      evidence: [],
      status: "DRAFT",
      revision: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    {
      id: "30000000-0000-4000-8000-000000000002",
      project_id: "20000000-0000-4000-8000-000000000001",
      type_key: "work_activity",
      name: "确认订单",
      stable_key: null,
      design_membership: "MODELED",
      properties: {},
      viewpoint: "DESIGNED",
      evidence: [],
      status: "DRAFT",
      revision: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
  relations: [
    {
      id: "50000000-0000-4000-8000-000000000001",
      project_id: "20000000-0000-4000-8000-000000000001",
      type_key: "responsible_for",
      name: "负责",
      participants: [
        { role_key: "responsible", entity_id: "30000000-0000-4000-8000-000000000001", entity_name: "销售经理", entity_type_key: "role", ordinal: 0 },
        { role_key: "work", entity_id: "30000000-0000-4000-8000-000000000002", entity_name: "确认订单", entity_type_key: "work_activity", ordinal: 1 },
      ],
      properties: {},
      viewpoint: "DESIGNED",
      evidence: [],
      status: "DRAFT",
      revision: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
};

describe("toProjectionElements", () => {
  it("将多参与者关系转换为关系节点和参与边", () => {
    const elements = toProjectionElements(graph);
    expect(elements).toHaveLength(5);
    expect(elements.filter((element) => element.group === "nodes")).toHaveLength(3);
    expect(elements.filter((element) => element.group === "edges")).toHaveLength(2);
  });

  it("搜索时不会保留只有一个可见参与者的孤立关系", () => {
    const elements = toProjectionElements(graph, "销售经理");
    expect(elements).toHaveLength(1);
    expect(elements[0].data.id).toBe("entity:30000000-0000-4000-8000-000000000001");
  });
});
