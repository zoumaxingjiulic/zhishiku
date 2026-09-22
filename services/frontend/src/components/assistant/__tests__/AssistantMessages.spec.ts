import { fireEvent, render, screen } from "@testing-library/vue";
import { describe, expect, it } from "vitest";
import AssistantMessages from "../AssistantMessages.vue";

describe("AssistantMessages", () => {
  it("renders server citations and capability labels with a collapsed execution timeline", async () => {
    render(AssistantMessages, {
      props: {
        messages: [{ id: 3, role: "assistant", content: "上一轮回答", citations: [], tool_calls: [] }, {
          id: 7, role: "assistant", content: "库存 12 件",
          citations: [{ document_id: 11, title: "库存管理制度", page: 3 }],
          tool_calls: [{ connector_tool_id: 21, connector_name: "ERP", tool: "get_inventory", success: true, called_at: "2026-09-22T10:30:00Z", argument_keys: ["material_code", "organization"] }],
        }],
        task: {
          id: "t1", session_id: "s1", status: "succeeded", stage: "完成", assistant_message_id: 7,
          execution_summary: {
            intent_type: "multi_capability", confidence: 0.94,
            selection: { knowledge_base_ids: [1], tool_ids: [21], agent_ids: [31], skill_ids: [41] },
            missing_parameters: [], needs_clarification: false, risk: "low", reason: "需要联合查询",
          },
        },
        capabilities: {
          knowledge_bases: [{ id: 1, code: "POLICY", name: "制度库" }],
          tools: [{ id: 21, code: "ERP_STOCK", name: "ERP 库存", connector_id: 2, read_only: true, input_schema_summary: {} }],
          agents: [{ id: 31, code: "ERP_AGENT", name: "ERP 助手" }],
          skills: [{ id: 41, code: "STOCK_CHECK", name: "库存查询 Skill" }],
        },
      },
    });

    expect(screen.getByText("《库存管理制度》")).toHaveAttribute("href", "/api/v1/documents/11/download");
    for (const label of ["制度库", "ERP 库存", "ERP 助手", "库存查询 Skill"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    const timeline = screen.getByText("执行时间线").closest("details");
    expect(timeline).not.toHaveAttribute("open");
    await fireEvent.click(screen.getByText("执行时间线"));
    expect(screen.getByText("需要联合查询")).toBeInTheDocument();
    expect(screen.getByText(/ERP \/ get_inventory/)).toBeInTheDocument();
    expect(screen.getByText(/material_code、organization/)).toBeInTheDocument();
    expect(screen.getByText(/2026-09-22T10:30:00Z/)).toBeInTheDocument();
  });

  it("binds execution provenance to message ids and never guesses from the latest answer", () => {
    render(AssistantMessages, {
      props: {
        messages: [
          { id: 3, role: "assistant", content: "历史回答", execution_summary: { intent_type: "agent_task", selection: { agent_ids: [31] } } },
          { id: 7, role: "assistant", content: "最新回答" },
        ],
        task: {
          id: "t-new", session_id: "s1", status: "succeeded", assistant_message_id: 999,
          execution_summary: { intent_type: "multi_capability", selection: { skill_ids: [41] } },
        },
        capabilities: {
          knowledge_bases: [], tools: [],
          agents: [{ id: 31, code: "EXPERT", name: "历史专家" }],
          skills: [{ id: 41, code: "NEW_SKILL", name: "不应串到最新回答" }],
        },
      },
    });

    expect(screen.getByText("历史专家")).toBeInTheDocument();
    expect(screen.queryByText("不应串到最新回答")).not.toBeInTheDocument();
  });

  it("shows an accessible empty state when a conversation has no messages", () => {
    render(AssistantMessages, { props: { messages: [], task: null, capabilities: null } });
    expect(screen.getByRole("heading", { name: "从一个问题开始" })).toBeInTheDocument();
  });
});
