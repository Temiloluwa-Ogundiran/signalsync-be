import json
from typing import Annotated, List, Optional

from langchain_core.tools import tool


# Chart shapes the FE (ai-chart.tsx) can render.
_ALLOWED_TYPES = {"line", "area", "bar", "pie"}
_ALLOWED_FORMATS = {"currency", "percent", "number"}


@tool
def build_chart(
    labels: Annotated[List[str], "X-axis / category labels, in order. e.g. ['2025-05-01','2025-05-02'] or ['EURUSD','GBPUSD']"],
    values: Annotated[List[float], "The numeric value for each label, same length/order as labels."],
    chart_type: Annotated[Optional[str], "Preferred chart: 'line', 'area', 'bar', or 'pie'. If omitted, a sensible type is chosen from the data."] = None,
    title: Annotated[Optional[str], "Short chart title, e.g. 'Net P&L curve' or 'P&L by symbol'."] = None,
    value_format: Annotated[Optional[str], "How to format values: 'currency' (P&L, $), 'percent' (rates), or 'number' (counts)."] = None,
    is_cumulative: Annotated[bool, "True if values are already a running/cumulative total (e.g. an equity curve). If the values are per-period and you want a curve, set this False and the tool will accumulate them."] = False,
) -> str:
    """Render a chart from data you already have.

    Call this AFTER a data tool (get_equity_curve, get_breakdown, etc.) when a
    visual would help — a trend over time, a comparison across categories, or a
    share-of-total. Pass the labels + values; the tool picks a good chart type if
    you don't specify one and returns a ```chart block. Include that block
    VERBATIM in your reply (the UI turns it into an interactive chart). Then add
    your text takeaway around it as usual.

    Type guidance: time series -> 'line' or 'area'; category comparison -> 'bar';
    share of a whole (<=6 slices) -> 'pie'.
    """
    if not labels or not values or len(labels) != len(values):
        return "build_chart error: 'labels' and 'values' must be non-empty and the same length."

    # Coerce values to float; bail clearly if something isn't numeric.
    try:
        nums = [float(v) for v in values]
    except (TypeError, ValueError):
        return "build_chart error: every entry in 'values' must be a number."

    # Accumulate per-period values into a running total when asked for a curve.
    if is_cumulative is False and (chart_type in ("line", "area")):
        running = 0.0
        cumulated = []
        for v in nums:
            running += v
            cumulated.append(round(running, 2))
        nums = cumulated

    # Choose a chart type if the model didn't, from the shape of the data.
    ct = (chart_type or "").strip().lower()
    if ct not in _ALLOWED_TYPES:
        n = len(labels)
        looks_temporal = all("-" in str(l) and str(l)[:4].isdigit() for l in labels)
        if looks_temporal and n >= 3:
            ct = "area"
        elif 2 <= n <= 6 and all(v >= 0 for v in nums):
            ct = "pie"
        else:
            ct = "bar"

    fmt = (value_format or "").strip().lower()
    if fmt not in _ALLOWED_FORMATS:
        fmt = "number"

    spec = {
        "type": ct,
        "title": title or None,
        "format": fmt,
        "points": [{"label": str(l), "value": round(float(v), 2)} for l, v in zip(labels, nums)],
    }
    # The FE parses the JSON inside this fence. Return it ready to paste verbatim.
    return "```chart\n" + json.dumps(spec) + "\n```"
