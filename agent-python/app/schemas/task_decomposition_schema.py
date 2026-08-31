from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TaskType = Literal["LEAVE_REQUEST", "EXPENSE_CLAIM"]
DecompositionKind = Literal["single", "multi", "unsupported"]


class TaskSpec(BaseModel):
    """纯分解输出；生命周期归 Java Task Runtime 负责。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    task_type: TaskType
    task_text: str = Field(min_length=1, max_length=8000)
    sequence: int = Field(ge=1, le=2)

    @field_validator("task_text")
    @classmethod
    def reject_blank_task_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task_text must not be blank")
        return value


class TaskDecompositionResult(BaseModel):
    """不包含队列或执行状态的确定性解析结果。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: DecompositionKind
    tasks: list[TaskSpec] = Field(default_factory=list, max_length=2)
    reason: str = ""

    @model_validator(mode="after")
    def validate_shape(self) -> "TaskDecompositionResult":
        if self.kind == "unsupported" and self.tasks:
            raise ValueError("unsupported decomposition must not contain tasks")
        if self.kind == "multi" and len(self.tasks) != 2:
            raise ValueError("multi decomposition must contain exactly two tasks")
        if self.kind == "single" and len(self.tasks) > 1:
            raise ValueError("single decomposition must contain at most one task")

        expected_sequences = list(range(1, len(self.tasks) + 1))
        actual_sequences = [task.sequence for task in self.tasks]
        if actual_sequences != expected_sequences:
            raise ValueError("task sequences must be contiguous and ordered")
        return self


class TaskDecompositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value
