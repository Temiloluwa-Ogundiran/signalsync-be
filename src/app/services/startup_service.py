from sqlalchemy.orm import Session

from app.repositories import journal_template_repo


def seed_system_journal_templates(db: Session) -> dict[str, int]:
    created, skipped = journal_template_repo.seed_system_templates(db)

    if created:
        db.commit()

    return {
        "created": created,
        "skipped_existing": skipped,
    }
