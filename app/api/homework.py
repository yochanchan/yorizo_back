from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import case
from sqlalchemy.orm import Session

from app.schemas.homework import (
    HomeworkTaskCreate,
    HomeworkTaskRead,
    HomeworkTaskUpdate,
    HomeworkTaskBase,
)
from app.models.enums import HomeworkStatus
from database import get_db
from app.models import HomeworkTask, User, Conversation

router = APIRouter()


def _ensure_user(db: Session, user_id: str) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(id=user_id, nickname="guest")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@router.get("/homework", response_model=List[HomeworkTaskRead])
def list_homework_tasks(
    user_id: str = Query(..., description="User ID"),
    status_filter: Optional[HomeworkStatus] = Query(
        None, description="Filter by status (pending|done)"
    ),
    db: Session = Depends(get_db),
) -> List[HomeworkTask]:
    query = db.query(HomeworkTask).filter(HomeworkTask.user_id == user_id)
    if status_filter:
        query = query.filter(HomeworkTask.status == status_filter.value)

    status_order = case((HomeworkTask.status == HomeworkStatus.PENDING.value, 0), else_=1)
    due_date_nulls_last = case((HomeworkTask.due_date.is_(None), 1), else_=0)
    tasks = (
        query.order_by(
            status_order,
            due_date_nulls_last,
            HomeworkTask.due_date.asc(),
            HomeworkTask.created_at,
        )
        .all()
    )
    return tasks


@router.post("/homework", response_model=HomeworkTaskRead, status_code=status.HTTP_201_CREATED)
def create_homework_task(payload: HomeworkTaskCreate, db: Session = Depends(get_db)) -> HomeworkTask:
    _ensure_user(db, payload.user_id)
    conversation_id = payload.conversation_id or None
    if conversation_id:
        exists = db.query(Conversation.id).filter(Conversation.id == conversation_id).first()
        if not exists:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="指定された会話が見つかりません。conversation_idを確認してください。",
            )
    task = HomeworkTask(
        user_id=payload.user_id,
        conversation_id=conversation_id,
        title=payload.title,
        detail=payload.detail,
        category=payload.category,
        status=(payload.status or HomeworkStatus.PENDING).value,
        timeframe=payload.timeframe,
        due_date=payload.due_date,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


class HomeworkSuggestionItem(BaseModel):
    title: str
    detail: Optional[str] = None
    category: Optional[str] = None
    due_date: Optional[date] = None  # suggestions typically omit due dates


class HomeworkBulkCreate(BaseModel):
    user_id: str
    conversation_id: Optional[str] = None
    tasks: List[HomeworkSuggestionItem]


@router.post("/homework/bulk-from-suggestions", response_model=List[HomeworkTaskRead])
def bulk_create_homework_tasks(payload: HomeworkBulkCreate, db: Session = Depends(get_db)) -> List[HomeworkTask]:
    _ensure_user(db, payload.user_id)
    conversation_id = payload.conversation_id or None
    if conversation_id:
        exists = db.query(Conversation.id).filter(Conversation.id == conversation_id).first()
        if not exists:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="指定された会話が見つかりません。conversation_idを確認してください。",
            )
    created: List[HomeworkTask] = []
    now = datetime.utcnow()
    for item in payload.tasks:
        task = HomeworkTask(
            user_id=payload.user_id,
            conversation_id=conversation_id,
            title=item.title,
            detail=item.detail,
            category=item.category,
            status=HomeworkStatus.PENDING.value,
            timeframe=None,
            due_date=item.due_date,
            created_at=now,
            updated_at=now,
        )
        db.add(task)
        created.append(task)
    db.commit()
    for task in created:
        db.refresh(task)
    return created


@router.patch("/homework/{task_id}", response_model=HomeworkTaskRead)
def update_homework_task(task_id: int, payload: HomeworkTaskUpdate, db: Session = Depends(get_db)) -> HomeworkTask:
    task = db.query(HomeworkTask).filter(HomeworkTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Homework task not found")

    if payload.title is not None:
        task.title = payload.title
    if payload.detail is not None:
        task.detail = payload.detail
    if payload.category is not None:
        task.category = payload.category
    if payload.timeframe is not None:
        task.timeframe = payload.timeframe
    if payload.due_date is not None:
        task.due_date = payload.due_date
    if payload.status is not None:
        new_status = payload.status
        if new_status == HomeworkStatus.DONE and task.status != HomeworkStatus.DONE.value:
            task.completed_at = datetime.utcnow()
        if new_status == HomeworkStatus.PENDING:
            task.completed_at = None
        task.status = new_status.value

    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    return task


@router.delete("/homework/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_homework_task(task_id: int, db: Session = Depends(get_db)) -> Response:
    task = db.query(HomeworkTask).filter(HomeworkTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Homework task not found")
    db.delete(task)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
