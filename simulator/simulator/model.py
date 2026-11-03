"""Plan data model. All times are simulated seconds from t=0; all money is int paise."""

from dataclasses import dataclass, field
from typing import Literal

DAY = 86_400.0
HOUR = 3_600.0
MINUTE = 60.0
RUPEE = 100
MAX_MEMO = 280

Label = Literal["FRAUD", "LEGIT"]
Role = Literal["baseline", "subject", "counterparty"]


@dataclass(frozen=True)
class AccountSpec:
    alias: str
    owner_name: str
    created_at: float  # 0.0 = created before the run starts ("old" by the time scenarios begin)
    label: Label
    pattern: str
    role: Role
    scenario_id: str | None = None
    injected: bool = False


@dataclass(frozen=True)
class Step:
    at: float
    kind: Literal["create", "deposit", "transfer"]
    to: str  # alias (the account to create / credit)
    src: str | None = None  # alias debited (transfers only)
    amount_minor: int = 0
    memo: str | None = None


@dataclass
class Script:
    """Steps run sequentially, each no earlier than its time. One actor (or one scenario) per script."""

    script_id: str
    steps: list[Step] = field(default_factory=list)


@dataclass(frozen=True)
class ScenarioMeta:
    scenario_id: str
    pattern: str
    label: Label
    start: float
    injected: bool
    injection_text: str | None


@dataclass
class Plan:
    seed: int
    sim_seconds: float
    accounts: dict[str, AccountSpec]
    scripts: list[Script]
    scenarios: list[ScenarioMeta]

    def steps(self) -> list[tuple[str, Step]]:
        return [(s.script_id, st) for s in self.scripts for st in s.steps]
