from uuid import UUID

from pydantic import BaseModel, model_validator

from app.models.test_audience import AudienceType


class TestAudienceCreate(BaseModel):
    audience_type: AudienceType
    provider_id: UUID | None = None
    branch_id: UUID | None = None
    batch_id: UUID | None = None
    student_id: str | None = None

    @model_validator(mode="after")
    def validate_audience(self):
        if self.audience_type == AudienceType.PROVIDER:
            if not self.provider_id or self.branch_id or self.batch_id or self.student_id:
                raise ValueError(
                    "PROVIDER audience requires provider_id only"
                )

        elif self.audience_type == AudienceType.BRANCH:
            if not self.provider_id or not self.branch_id or self.batch_id or self.student_id:
                raise ValueError(
                    "BRANCH audience requires provider_id and branch_id"
                )

        elif self.audience_type == AudienceType.BATCH:
            if not self.provider_id or not self.batch_id or self.student_id:
                raise ValueError(
                    "BATCH audience requires provider_id and batch_id"
                )

        elif self.audience_type == AudienceType.STUDENT:
            if not self.student_id:
                raise ValueError(
                    "STUDENT audience requires student_id"
                )

        return self


class TestAudienceResponse(BaseModel):
    test_audience_id: UUID
    test_id: UUID
    audience_type: AudienceType
    provider_id: UUID | None
    branch_id: UUID | None
    batch_id: UUID | None
    student_id: str | None