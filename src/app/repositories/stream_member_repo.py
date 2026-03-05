import uuid
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.stream_member import MemberStatus, StreamMember


def get(db: Session, *, user_id: UUID, stream_id: UUID) -> Optional[StreamMember]:
    return (
        db.query(StreamMember)
        .filter(StreamMember.user_id == user_id, StreamMember.stream_id == stream_id)
        .first()
    )


def create(
    db: Session,
    *,
    user_id: UUID,
    stream_id: UUID,
    status: MemberStatus,
) -> StreamMember:
    member = StreamMember(user_id=user_id, stream_id=stream_id, status=status)
    db.add(member)
    db.flush()
    return member


def update_status(
    db: Session, member: StreamMember, status: MemberStatus
) -> StreamMember:
    member.status = status
    db.flush()
    return member


def delete(db: Session, member: StreamMember) -> None:
    db.delete(member)
    db.flush()


def list_pending(db: Session, *, stream_id: UUID) -> list[StreamMember]:
    return (
        db.query(StreamMember)
        .filter(
            StreamMember.stream_id == stream_id,
            StreamMember.status == MemberStatus.pending,
        )
        .all()
    )


def list_active(db: Session, *, stream_id: UUID) -> list[StreamMember]:
    return (
        db.query(StreamMember)
        .options(joinedload(StreamMember.user))
        .filter(
            StreamMember.stream_id == stream_id,
            StreamMember.status == MemberStatus.active,
        )
        .all()
    )
