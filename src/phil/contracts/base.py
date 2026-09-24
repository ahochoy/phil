from pydantic import BaseModel, ConfigDict


class Part(BaseModel):
    """Nested contract component. Rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


class Contract(Part):
    """Top-level contract passed between agents or across the human boundary."""

    schema_version: int = 1
