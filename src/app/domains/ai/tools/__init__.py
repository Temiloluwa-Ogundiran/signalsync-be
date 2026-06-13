from app.domains.ai.tools.trade_query import query_trades
from app.domains.ai.tools.risk import get_risk_snapshot
from app.domains.ai.tools.performance_metrics import get_performance_metrics
from app.domains.ai.tools.compare_periods import compare_periods
from app.domains.ai.tools.breakdown import get_breakdown
from app.domains.ai.tools.equity_curve import get_equity_curve
from app.domains.ai.tools.find_trades import find_trades
from app.domains.ai.tools.streaks_drawdown import get_streaks_and_drawdown
from app.domains.ai.tools.detect_patterns import detect_patterns
from app.domains.ai.tools.search_daily_journal import search_daily_journal
from app.domains.ai.tools.summarize_journal import summarize_journal
from app.domains.ai.tools.get_trade_notes import get_trade_notes
from app.domains.ai.tools.find_tagged_trades import find_tagged_trades

ALL_TOOLS = [
    query_trades,
    get_performance_metrics,
    compare_periods,
    get_breakdown,
    get_equity_curve,
    find_trades,
    get_streaks_and_drawdown,
    detect_patterns,
    get_risk_snapshot,
    search_daily_journal,
    summarize_journal,
    get_trade_notes,
    find_tagged_trades,
]
