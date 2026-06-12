from typing import Optional
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.domains.csv_import import service
from app.domains.csv_import.schemas import CSVConfirmResult, CSVPreviewResponse, PlatformInfo
from app.domains.users.models import User
from app.shared.deps import get_current_user
from app.shared.utils.uploads import read_upload_within_limit

router = APIRouter(prefix="/csv-import", tags=["csv-import"])


@router.get("/platforms", response_model=list[PlatformInfo])
def get_supported_platforms() -> list[PlatformInfo]:
    """Get list of supported CSV/XLSX platforms with instruction details."""
    return [
        PlatformInfo(
            id="mt5",
            name="MetaTrader 5",
            description="Import your trade history from an MT5 report file (.xlsx)",
            supported_extensions=[".xlsx"],
            export_instructions=(
                "1. Open your MetaTrader 5 Desktop Terminal.\n"
                "2. Go to the 'History' tab at the bottom.\n"
                "3. Right-click inside the History list -> select 'All History' (critical to include your entire trade data).\n"
                "4. Right-click again -> select 'Report' -> click 'Open XML (MS Excel)'.\n"
                "5. Save the generated report file as an Excel workbook (.xlsx).\n"
                "6. Upload the saved workbook here."
            ),
            max_file_size_mb=10,
        )
    ]


@router.post("/preview", response_model=CSVPreviewResponse)
@limiter.limit(settings.RATE_LIMIT_UPLOADS)
async def preview_csv_import(
    request: Request,
    file: UploadFile = File(...),
    platform_id: str = Form(...),
    timezone: str = Form(...),
    current_user: User = Depends(get_current_user),
) -> CSVPreviewResponse:
    """Parse uploaded file and return preview statistical breakdown (no DB updates)."""
    # Verify file extension
    filename = file.filename or ""
    if not filename.endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file format. Please upload a valid MetaTrader 5 report file (.xlsx).",
        )

    content = await read_upload_within_limit(file)

    return await service.preview_import(content, platform_id, timezone)


@router.post("/confirm", response_model=CSVConfirmResult)
@limiter.limit(settings.RATE_LIMIT_UPLOADS)
async def confirm_csv_import(
    request: Request,
    file: UploadFile = File(...),
    platform_id: str = Form(...),
    timezone: str = Form(...),
    display_name: str = Form(...),
    account_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CSVConfirmResult:
    """Commit trade data from the file and establish/sync account."""
    # Verify file extension
    filename = file.filename or ""
    if not filename.endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file format. Please upload a valid MetaTrader 5 report file (.xlsx).",
        )

    content = await read_upload_within_limit(file)

    parsed_account_id = None
    if account_id and account_id.strip() and account_id not in ("null", "undefined"):
        try:
            parsed_account_id = uuid.UUID(account_id.strip())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid account_id format.",
            )

    return await service.confirm_import(
        db,
        content,
        platform_id,
        timezone,
        display_name,
        parsed_account_id,
        current_user,
    )
