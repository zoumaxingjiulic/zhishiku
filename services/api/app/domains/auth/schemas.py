from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class UserDepartment(BaseModel):
    id: int
    code: str
    name: str
    is_primary: int | bool


class AuthenticatedUser(BaseModel):
    id: int
    username: str
    display_name: str
    email: str | None = None
    status: int
    last_login_at: datetime | None = None
    password_changed_at: datetime | None = None
    departments: list[UserDepartment]
    department_ids: list[int]
    is_platform_admin: bool


class LoginResponse(BaseModel):
    user: AuthenticatedUser
