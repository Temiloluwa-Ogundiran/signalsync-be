"""
Journal service package.
All public names are re-exported here so callers use:
    from app.domains.journal import service as journal_service
    journal_service.get_or_create_daily_journal(...)
"""
from ._helpers import extract_tags  # noqa: F401 — used by tests and router
from ._journals import (  # noqa: F401
    create_daily_journal_message,
    create_trade_journal_message,
    delete_message,
    get_adjacent_traded_dates,
    get_day_note,
    get_or_create_daily_journal,
    get_or_create_trade_journal,
    list_daily_journal_feed,
    mark_daily_journal_reviewed,
    mark_trade_journal_reviewed,
    save_day_note,
    update_message,
)
from ._trades import (  # noqa: F401
    list_account_open_positions,
    list_account_trades,
)
from ._templates import (  # noqa: F401
    create_template,
    delete_template,
    list_templates,
    seed_system_journal_templates,
)
from ._analytics import (  # noqa: F401
    get_analytics_curve,
    get_analytics_dashboard,
    get_analytics_equity_curve,
    get_analytics_evaluation,
    get_analytics_intraday_curves,
    get_analytics_summary,
    get_analytics_time_performance,
)
from ._manual_trades import (  # noqa: F401
    create_manual_trade,
    delete_manual_trade,
    update_manual_trade,
)
from ._tags import (  # noqa: F401
    create_custom_category,
    create_custom_option,
    delete_custom_category,
    delete_custom_option,
    get_trade_tags,
    list_user_tags_config,
    update_trade_assessment,
    update_trade_rating,
    update_trade_tags,
)
