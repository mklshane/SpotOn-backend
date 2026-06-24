"""Declarative base. Models mirror the live Supabase schema — we never emit DDL."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
