from datetime import datetime

from pydantic import BaseModel, Field


class DepartmentCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=128)
    parent_id: int | None = 1


class DepartmentView(BaseModel):
    id: int
    code: str
    name: str
    parent_id: int | None
    status: int
    created_at: datetime | None = None


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{2,63}$")
    display_name: str = Field(min_length=2, max_length=128)
    email: str | None = None
    department_id: int


class UserUpdate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{2,63}$")
    display_name: str = Field(min_length=2, max_length=128)
    email: str | None = None
    department_id: int


class UserStatusUpdate(BaseModel):
    status: int = Field(ge=0, le=1)


class UserSummary(BaseModel):
    id: int
    username: str
    display_name: str
    email: str | None = None
    status: int
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    department_id: int | None = None
    department_code: str | None = None
    department_name: str | None = None


class UserCreated(BaseModel):
    id: int
    username: str
    display_name: str
    status: int
    temporary_password: str


class TemporaryPasswordResponse(BaseModel):
    status: str
    temporary_password: str
