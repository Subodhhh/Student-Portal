from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class StartAttemptRequest(BaseModel):
    test_id: UUID


class AttemptResponse(BaseModel):
    attempt_id: UUID
    test_id: UUID
    started_at: datetime
    expires_at: datetime
    status: str