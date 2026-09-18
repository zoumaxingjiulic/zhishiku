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
