"""
Compatibility shim for legacy imports.
Prefer importing routers from app.api.*.
"""
import sys
from importlib import import_module

# Import modules from the new location
from app.api import (  # noqa: F401
    admin_bookings,
    case_examples,
    chat,
    company_profile,
    company_reports,
    conversations,
    diagnosis,
    documents,
    experts,
    homework,
    memory,
    rag,
    report,
    reports,
)

# Expose as api.<module> for backward compatibility
_module_names = [
    "admin_bookings",
    "case_examples",
    "chat",
    "company_profile",
    "company_reports",
    "conversations",
    "diagnosis",
    "documents",
    "experts",
    "homework",
    "memory",
    "rag",
    "report",
    "reports",
]
for _name in _module_names:
    _mod = import_module(f"app.api.{_name}")
    sys.modules.setdefault(f"{__name__}.{_name}", _mod)

__all__ = _module_names
# Routers package for Yorizo API
