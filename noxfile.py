from __future__ import annotations

import nox

nox.options.sessions = ["lint", "tests"]
nox.options.error_on_missing_interpreters = False


@nox.session(python=["3.11", "3.12", "3.13", "3.14"])
def tests(session: nox.Session) -> None:
    session.install(".[dev]")
    session.run("pytest", "-q")


@nox.session
def lint(session: nox.Session) -> None:
    session.install(".[dev]")
    session.run("ruff", "check", ".")


@nox.session
def format(session: nox.Session) -> None:
    session.install(".[dev]")
    session.run("ruff", "format", ".")
    session.run("ruff", "check", "--fix", ".")
