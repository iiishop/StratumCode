from __future__ import annotations

from .investigation_use_case import InvestigatingUseCase

_USE_CASE = InvestigatingUseCase()


def prepare_investigation(run):
    yield from _USE_CASE.prepare(run)


def handle(run):
    yield from _USE_CASE.run(run)
