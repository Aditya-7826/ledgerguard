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
    kafka_bootstrap_servers: str = "localhost:9094"
    ledger_events_topic: str = "ledger.events"
    detector_group_id: str = "detector"
    detector_time_scale: float = Field(default=1.0, gt=0)

    def rules(self) -> RuleConfig:
        return RuleConfig(time_scale=self.detector_time_scale)
