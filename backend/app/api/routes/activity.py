"""Signed-in user's activity feed: their tracked tasks and failed requests (``/api/activity``)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.app.api.deps import Container, User
from backend.app.core.errors import NotFoundError
from backend.app.models.base import ApiModel
from backend.app.models.task import RequestPage, TaskPage, TaskRecord

router = APIRouter(prefix="/activity", tags=["activity"])


class ActivityFeed(ApiModel):
    """The caller's recent tasks and failed requests for the activity panel.

    Attributes:
        tasks: Recent tasks, newest first, with running and failed counts.
        errors: Recent failed requests, newest first.
    """

    tasks: TaskPage
    errors: RequestPage


@router.get("", response_model=ActivityFeed)
def my_activity(
    user: User,
    container: Container,
    task_hours: int = Query(default=24 * 7, ge=1, le=24 * 30, description="Task window in hours."),
    error_hours: int = Query(default=24, ge=1, le=24 * 14, description="Error window in hours."),
    limit: int = Query(default=30, ge=1, le=100),
) -> ActivityFeed:
    """The caller's own tasks and failed requests. Other users' activity is never returned.

    Any signed-in user.

    Args:
        user: The caller.
        container: Service container.
        task_hours: How far back to list tasks.
        error_hours: How far back to list failed requests.
        limit: Most tasks and most errors to return.

    Returns:
        The activity feed.
    """
    return ActivityFeed(
        tasks=container.tasks.for_actor(user.user_name, hours=task_hours, limit=limit),
        errors=container.performance.requests_for_user(
            user.user_name, hours=error_hours, failed_only=True, limit=limit
        ),
    )


@router.get("/tasks/{task_id}", response_model=TaskRecord)
def my_task(task_id: str, user: User, container: Container) -> TaskRecord:
    """One of the caller's tasks with its steps.

    Any signed-in user; tasks started by someone else return 404.

    Args:
        task_id: Task identifier.
        user: The caller.
        container: Service container.

    Returns:
        The task.

    Raises:
        NotFoundError: If the task does not exist or belongs to another user.
    """
    task = container.tasks.get(task_id)
    if task is None or task.actor.lower() != user.user_name.lower():
        raise NotFoundError(f"Task {task_id} not found")
    return task
