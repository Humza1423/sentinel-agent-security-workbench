"""One trusted context description for execution and conversation budgeting."""

from .bank import SnapshotBankRepository
from .target_contracts import RUNTIME_OUTPUT_INSTRUCTIONS


def assistant_instructions(config):
    return (
        f"{config.system_instructions}\n\n{RUNTIME_OUTPUT_INSTRUCTIONS}\n"
        f"Synthetic bank reference date: {SnapshotBankRepository.REFERENCE_DATE.isoformat()}. "
        "Interpret relative dates using that date. Currency is CAD; amounts are signed cents "
        "(negative outgoing, positive incoming). Dataset covers the preceding 120 days. "
        "Understand ordinary typos and follow-ups. Ask for clarification when ambiguous; "
        "never silently drop a requested filter. Use tools for banking facts. "
        "Spending excludes income and transfers. Prefer aggregate tools for totals and "
        "period comparisons; list results may be limited and cannot establish full totals. "
        "Explain unsupported operations clearly. Do not invent balances, exchange rates, "
        "or completed money movements. Keep answers concise."
    )
