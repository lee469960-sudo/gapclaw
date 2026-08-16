from typing import Any, Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    code: int = 0
    msg: str = "操作成功"
    data: T | None = None


def ok(data: Any = None, msg: str = "操作成功") -> dict:
    return {"code": 0, "msg": msg, "data": data}


def fail(msg: str = "操作失败", code: int = 1, data: Any = None) -> dict:
    return {"code": code, "msg": msg, "data": data}
