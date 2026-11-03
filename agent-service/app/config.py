"""Configuration from environment variables (pydantic-settings)."""

from datetime import timedelta

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuleConfig(BaseModel):
    """Detector thresholds. Durations are in *production* seconds; see ``time_scale``."""

    # Divides every duration. 1 = real time. The simulator compresses time (default 3600: one simulated
    # hour per real second), and the detector must use the same scale so "24h" means the same thing.
    time_scale: float = Field(default=1.0, gt=0)

    velocity_window_s: int = 3600
    velocity_out_count: int = 8
    velocity_in_count: int = 15

    structuring_threshold_minor: int = 1_000_000  # ₹10,000 reporting threshold, in paise
    structuring_band: float = 0.9  # "just under" = [90%, 100%) of the threshold
    structuring_count: int = 3
    structuring_window_s: int = 86_400

    fan_in_max_account_age_s: int = 7 * 86_400
    fan_in_senders: int = 5
    fan_in_window_s: int = 86_400

    fanout_window_s: int = 7_200
    fanout_min_credit_minor: int = 500_000  # ₹5,000
    fanout_ratio: float = 0.8

    round_trip_min_hops: int = 3
    round_trip_max_hops: int = 5
    round_trip_window_s: int = 48 * 3600
    round_trip_min_amount_minor: int = 500_000
    round_trip_min_retention: float = 0.8

    def window(self, seconds: int) -> timedelta:
        return timedelta(seconds=seconds / self.time_scale)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    agent_database_url: str = "postgresql://agent:agent@localhost:5432/agent"

    # detector
    kafka_bootstrap_servers: str = "localhost:9094"
    ledger_events_topic: str = "ledger.events"
    detector_group_id: str = "detector"
    detector_time_scale: float = Field(default=1.0, gt=0)

    # LLM. The model is never hardcoded: LLM_MODEL must be set for real providers.
    llm_provider: str = "anthropic"
    llm_model: str = ""
    llm_timeout_s: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)
    llm_retry_backoff_s: float = Field(default=1.0, ge=0)
    # Cost estimate inputs (USD per million tokens). Leave 0 if unknown: cost is then reported as 0.
    llm_input_usd_per_mtok: float = Field(default=0.0, ge=0)
    llm_output_usd_per_mtok: float = Field(default=0.0, ge=0)

    # agent
    mcp_url: str = "http://localhost:8000/mcp"
    agent_max_tool_calls: int = Field(default=25, ge=1)
    agent_investigate_timeout_s: float = Field(default=120.0, gt=0)
    agent_auto_clear_max_risk: float = Field(default=0.2, ge=0, le=1)
    agent_auto_clear_min_confidence: float = Field(default=0.8, ge=0, le=1)
    agent_concurrency: int = Field(default=4, ge=1)
    agent_settle_seconds: float = Field(default=10.0, ge=0)
    # Tells the model that timestamps are compressed (simulation). 1 = real time.
    sim_time_scale: float = Field(default=1.0, gt=0)

    # cases API → ledger. ADMIN key: freeze after a human decision, nothing else. READONLY key: UI reads.
    ledger_url: str = "http://localhost:8080"
    ledger_admin_api_key: str = ""
    ledger_readonly_api_key: str = ""

    def rules(self) -> RuleConfig:
        return RuleConfig(time_scale=self.detector_time_scale)
