"""
Compatibility shim for legacy imports.
Routers/services were moved to app.services.*.
"""
import sys
from importlib import import_module

from app.services import chat_flow, company_report, financial_import, rag, reports  # noqa: F401

_module_names = ["chat_flow", "company_report", "financial_import", "rag", "reports"]
for _name in _module_names:
    _mod = import_module(f"app.services.{_name}")
    sys.modules.setdefault(f"{__name__}.{_name}", _mod)

__all__ = _module_names
