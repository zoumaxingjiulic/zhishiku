export interface DepartmentDto {
  id: number;
  code: string;
  name: string;
  parent_id: number | null;
  status: number;
  created_at: string;
}

export interface UserDto {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  status: number;
  last_login_at: string | null;
  created_at: string;
  department_id: number | null;
  department_code: string | null;
  department_name: string | null;
}

export interface CreateUserResponse {
  id: number;
  username: string;
  display_name: string;
  status: number;
  temporary_password: string;
}

export interface ResetPasswordResponse {
  status: string;
  temporary_password: string;
}

export interface KnowledgeBasePermissionGrant {
  knowledge_base_id: number;
  code: string;
  name: string;
  permission: "read" | "manage";
  sources: Array<"department" | "direct">;
}

export interface DirectToolGrant {
  id: number;
  connector_id: number;
  connector_code: string;
  connector_name: string;
  tool_name: string;
  title: string | null;
}

export interface UserPermissionDetail {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  status: number;
  department_id: number | null;
  department_code: string | null;
  department_name: string | null;
  department_inherited_knowledge_base_grants: KnowledgeBasePermissionGrant[];
  direct_knowledge_base_grants: KnowledgeBasePermissionGrant[];
  effective_knowledge_base_grants: KnowledgeBasePermissionGrant[];
  direct_tools: DirectToolGrant[];
}
