from app.domains.csv_import.parsers.base import PlatformParser
from app.domains.csv_import.parsers.mt5_report import MT5ReportParser

_PARSERS: dict[str, PlatformParser] = {
    "mt5": MT5ReportParser(),
}

def get_parser(platform_id: str) -> PlatformParser:
    parser = _PARSERS.get(platform_id.lower())
    if not parser:
        raise ValueError(f"Unsupported trading platform: {platform_id}")
    return parser

def list_parsers() -> list[PlatformParser]:
    return list(_PARSERS.values())
