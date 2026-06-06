"""The Operation Catalog (ADR-002 codified, M3 Phase A). Finite, human-authored, CLOSED.

Zero generative string-execution pathways: the ONLY way a proposed action becomes an Operation
is parse_operation(), which validates names against strongly-typed enums. Anything not in the
catalog raises UnregisteredOperationError and is logged as a severe security violation. There is
no `^run_shell(string)` and no path to one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

_log = logging.getLogger("execution.security")


class OpName(Enum):
    OPEN_APP = "open_app"
    RUN_SAVED_COMMAND = "run_saved_command"


class AppEnum(Enum):
    SLACK = "slack"
    SLIDES = "slides"
    TERMINAL = "terminal"


class SavedCommandEnum(Enum):
    DISK_USAGE = "disk_usage"  # -> a vetted, version-controlled command text (wired later)
    LIST_PROCESSES = "list_processes"


# Each operation's single argument MUST belong to exactly this enum. Human-authored, closed.
_ARG_ENUM: dict[OpName, type[Enum]] = {
    OpName.OPEN_APP: AppEnum,
    OpName.RUN_SAVED_COMMAND: SavedCommandEnum,
}


class UnregisteredOperationError(Exception):
    """A proposed operation/argument is not in the closed catalog — a security violation."""


@dataclass(frozen=True)
class Operation:
    name: OpName
    arg: Enum  # a validated member of the operation's bound enum


def _violation(message: str) -> None:
    _log.critical("SECURITY VIOLATION: %s", message)


def parse_operation(op_name: str, arg_name: str) -> Operation:
    """Validate a proposed (op_name, arg_name) against the closed catalog. The interception point."""
    try:
        op = OpName(op_name)
    except ValueError:
        _violation(f"unregistered operation proposed: {op_name!r}")
        raise UnregisteredOperationError(f"operation {op_name!r} is not in the catalog")
    arg_enum = _ARG_ENUM[op]
    try:
        arg = arg_enum(arg_name)
    except ValueError:
        _violation(f"unregistered argument {arg_name!r} for operation {op_name!r}")
        raise UnregisteredOperationError(f"argument {arg_name!r} is not valid for {op_name!r}")
    return Operation(op, arg)
