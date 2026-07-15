from app.core.celery_app import celery_app


@celery_app.task(
    name="copy_trading.send_execution_email",
    bind=True,
    ignore_result=True,
    max_retries=2,
    default_retry_delay=30,
    autoretry_for=(Exception,),
)
def send_execution_email_task(self, to_email: str, subject: str, details: dict) -> None:
    from app.shared.utils.email import send_copy_trading_email

    send_copy_trading_email(to_email, subject=subject, details=details)
