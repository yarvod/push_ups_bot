from datetime import time
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    token: SecretStr
    owner_id: int = 718724903
    spreadsheet_id: str = "1mDJHvCvF7imaVYL1uQikAIhmH3GugNAgMMYu7-uHorU"
    timezone: str = "Europe/Moscow"
    deadline_time: time = time(23, 59)
    reminder_time: time = time(13)
    summary_time: time = time(18)
    min_video_seconds: int = Field(default=50, ge=1)
    max_video_seconds: int = Field(default=600, ge=1)
    google_application_credentials: str = "secrets/google-service-account.json"
    redis_url: str = "redis://localhost:6379/0"

    @field_validator("deadline_time", "reminder_time", "summary_time")
    @classmethod
    def minute_precision(cls, value: time) -> time:
        if value.second or value.microsecond or value.tzinfo:
            raise ValueError("Use local HH:MM without seconds or offset")
        return value

    @model_validator(mode="after")
    def valid_rules(self) -> "Settings":
        ZoneInfo(self.timezone)
        if self.min_video_seconds > self.max_video_seconds:
            raise ValueError("MIN_VIDEO_SECONDS must not exceed MAX_VIDEO_SECONDS")
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)
