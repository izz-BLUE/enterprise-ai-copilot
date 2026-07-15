from pydantic import BaseModel


class VersionResponse(BaseModel):
    service: str
    version: str
    gitCommit: str
    buildTime: str
