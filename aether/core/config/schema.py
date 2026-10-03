from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic import ConfigDict
from typing import List, Optional, Dict, Any, Literal
from urllib.parse import urlparse


class AIProviderConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = False
    models: List[str] = Field(default_factory=list)


class AIConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    providers: Dict[str, AIProviderConfig] = Field(default_factory=dict)
    chain: List[str] = Field(default_factory=lambda: ["Claude", "OpenAI", "Gemini", "NVIDIA", "OpenRouter", "Local"])


class OllamaConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = False
    base_url: str = "http://127.0.0.1:11434"
    model: str = ""
    keep_alive: str = "5m"

    @model_validator(mode="after")
    def require_model_when_enabled(self):
        if self.enabled and not self.model.strip():
            raise ValueError("model_manager.ollama.model is required when Ollama is enabled")
        return self


class ModelManagerConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    chain: List[str] = Field(default_factory=lambda: ["Ollama", "Local"])
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)


class OandaConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    api_key_env: Optional[str] = None
    account_id_env: Optional[str] = None
    base_url: str = ""


class DataProviderConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = True
    api_key_env: Optional[str] = None
    account_id_env: Optional[str] = None


class DataConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    provider_chain: List[str] = Field(default_factory=lambda: ["TwelveData", "AlphaVantage", "Yahoo", "Oanda"])
    providers: Dict[str, DataProviderConfig] = Field(default_factory=dict)
    oanda: Optional[OandaConfig] = None


class ContextConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    adx_threshold: int
    atr_period: int
    atr_percentiles: List[int]


class FeatureConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: List[str]


class StrategyItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    enabled: bool
    config_path: str


class ValidationConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    min_confidence: int
    max_spread_multiplier: float
    cooldown: Dict[str, int]


class PriceAlertConfig(BaseModel):
    id: str
    symbol: str
    condition: Literal["above", "below"]
    price: float
    enabled: bool = True


class NewsAlertConfig(BaseModel):
    enabled: bool = False
    pairs: List[str] = Field(default_factory=list)
    minimum_impact: Literal["low", "medium", "high"] = "high"
    source_url: Optional[str] = None
    source_timezone: str = "America/New_York"
    poll_interval_seconds: int = Field(default=300, ge=30, le=86400)
    lead_minutes: int = Field(default=60, ge=0, le=10080)

    @model_validator(mode="after")
    def validate_enabled_source(self):
        if self.source_url:
            parsed = urlparse(self.source_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("alerts.news.source_url must be an HTTPS URL")
        return self


class AlertsConfig(BaseModel):
    price: List[PriceAlertConfig] = Field(default_factory=list)
    news: NewsAlertConfig = Field(default_factory=NewsAlertConfig)


class DeliveryDetails(BaseModel):
    model_config = ConfigDict(extra="allow")
    token_env: Optional[str] = None
    chat_id_env: Optional[str] = None
    auth_token_env: Optional[str] = None
    phone_number_env: Optional[str] = None
    enabled: bool = True
    port: Optional[int] = None
    commands_registered: bool = False


class DeliveryConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    telegram: DeliveryDetails
    discord: Optional[DeliveryDetails] = None
    web: Optional[DeliveryDetails] = None
    whatsapp: Optional[DeliveryDetails] = None


class OpsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    health_check_interval: int
    auto_restart: bool
    anomaly_detection: bool


class AetherConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: int
    ai: AIConfig = Field(default_factory=AIConfig)
    model_manager: ModelManagerConfig = Field(default_factory=ModelManagerConfig)
    data: DataConfig
    context: ContextConfig
    features: FeatureConfig
    strategies: List[StrategyItem]
    validation: ValidationConfig
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    delivery: DeliveryConfig
    ops: OpsConfig
    watchlist: List[str] = Field(default_factory=list)
    config_version: Optional[int] = None

    @field_validator("schema_version")
    @classmethod
    def check_version(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Unsupported schema version")
        return v
