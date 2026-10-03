from pydantic import BaseModel, Field, field_validator
from pydantic import ConfigDict
from typing import List, Optional, Dict, Any


class AIProviderConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = False
    models: List[str] = Field(default_factory=list)


class AIConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    providers: Dict[str, AIProviderConfig] = Field(default_factory=dict)
    chain: List[str] = Field(default_factory=lambda: ["Claude", "OpenAI", "Gemini", "NVIDIA", "OpenRouter", "Local"])


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
    data: DataConfig
    context: ContextConfig
    features: FeatureConfig
    strategies: List[StrategyItem]
    validation: ValidationConfig
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
