export type AssistantTaskStatus = "queued" | "running" | "cancel_requested" | "succeeded" | "failed" | "cancelled";

export interface CapabilityRef {
  id: number;
  code: string;
  name: string;
  description?: string | null;
}

export interface ToolCapabilityRef extends CapabilityRef {
  connector_id: number;
  read_only: boolean;
  input_schema_summary: Record<string, unknown>;
}

export interface AssistantCapabilities {
  knowledge_bases: CapabilityRef[];
  tools: ToolCapabilityRef[];
  agents: CapabilityRef[];
  skills: CapabilityRef[];
}

export interface CapabilitySelection {
  knowledge_base_ids?: number[];
  tool_ids?: number[];
  agent_ids?: number[];
  skill_ids?: number[];
}

export interface ExecutionSummary {
  intent_type: "general_chat" | "knowledge_query" | "system_query" | "agent_task" | "multi_capability" | "clarification" | "forbidden";
  confidence?: number;
  selection?: CapabilitySelection;
  missing_parameters?: string[];
  needs_clarification?: boolean;
  risk?: "low" | "medium" | "high";
  reason?: string;
}

export interface AssistantTask {
  id: string;
  session_id: string;
  status: AssistantTaskStatus;
  stage?: string | null;
  partial_answer?: string | null;
  error_code?: string | null;
  updated_at?: string | null;
  execution_summary?: ExecutionSummary | null;
}

export interface AssistantSession {
  id: string;
  title: string;
  message_count?: number;
  created_at?: string;
  updated_at?: string;
  last_role?: "user" | "assistant" | null;
  latest_task_status?: AssistantTaskStatus | null;
  latest_task?: AssistantTask | null;
}

export interface AssistantCitation {
  document_id: number;
  title?: string;
  page?: number | null;
  page_end?: number | null;
}

export interface AssistantToolCall {
  connector_tool_id?: number | null;
  connector_name?: string;
  connector?: string;
  tool?: string;
  success?: boolean;
  error?: string | null;
}

export interface AssistantMessage {
  id: number | string;
  role: "user" | "assistant";
  content: string;
  citations?: AssistantCitation[];
  tool_calls?: AssistantToolCall[];
  created_at?: string;
  optimistic?: boolean;
}

export interface AssistantConversationDetail {
  id: string;
  title?: string;
  latest_task_status?: AssistantTaskStatus | null;
  latest_task?: AssistantTask | null;
  messages: AssistantMessage[];
}
