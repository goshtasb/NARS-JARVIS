"""Catalog tests: typed parse, and VIOLENT rejection of anything outside the closed catalog."""
import logging

from execution.catalog import (
    AppEnum,
    Operation,
    OpName,
    UnregisteredOperationError,
    parse_operation,
)


def test_valid_operation_parses_to_typed() -> None:
    assert parse_operation("open_app", "slack") == Operation(OpName.OPEN_APP, AppEnum.SLACK)


def test_unregistered_operation_rejected() -> None:
    try:
        parse_operation("run_shell", "rm -rf /")
    except UnregisteredOperationError:
        return
    raise AssertionError("unregistered operation MUST be rejected")


def test_unregistered_argument_rejected() -> None:
    try:
        parse_operation("open_app", "keychain_dump")  # not an AppEnum member
    except UnregisteredOperationError:
        return
    raise AssertionError("unregistered argument MUST be rejected")


def test_rejection_logs_security_violation() -> None:
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger = logging.getLogger("execution.security")
    logger.addHandler(handler)
    try:
        try:
            parse_operation("exfiltrate", "ssh_keys")
        except UnregisteredOperationError:
            pass
    finally:
        logger.removeHandler(handler)
    assert any(r.levelno == logging.CRITICAL for r in records), "must log a CRITICAL security violation"


if __name__ == "__main__":
    test_valid_operation_parses_to_typed()
    test_unregistered_operation_rejected()
    test_unregistered_argument_rejected()
    test_rejection_logs_security_violation()
    print("execution/test_catalog: OK")
