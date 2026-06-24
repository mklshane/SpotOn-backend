"""Shared dependencies — pagination and common query params."""
from dataclasses import dataclass

from fastapi import Query


@dataclass
class Pagination:
    limit: int
    offset: int


def pagination(
    limit: int = Query(20, ge=1, le=100, description="Max rows to return (1-100)."),
    offset: int = Query(0, ge=0, description="Rows to skip."),
) -> Pagination:
    return Pagination(limit=limit, offset=offset)
