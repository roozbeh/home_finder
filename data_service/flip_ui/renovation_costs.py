"""Calculate renovation budgets from explicitly scoped, sourced line items."""
from decimal import Decimal, InvalidOperation


def calculate_budget(items, selections):
    """Return selected item extensions and totals, rejecting ambiguous scopes."""
    lines = []
    selected_groups = set()
    totals = {"low": Decimal("0"), "high": Decimal("0")}
    for item in items:
        group = item.get("alternative_group")
        if group and selections.get(group) != item.get("alternative"):
            continue
        if not group and not item.get("included", True):
            continue
        if group:
            selected_groups.add(group)
        if not all(item.get(key) for key in ("room", "task", "unit", "source")):
            raise ValueError("Every selected item needs a room, task, unit and source")
        try:
            quantity = Decimal(str(item["quantity"]))
            quantity_high = Decimal(str(item.get("quantity_high", item["quantity"])))
            low = Decimal(str(item["unit_low"]))
            high = Decimal(str(item["unit_high"]))
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise ValueError("Invalid quantity or unit price") from exc
        if not all(value.is_finite() for value in (quantity, quantity_high, low, high)) or not 0 < quantity <= quantity_high or not 0 <= low <= high:
            raise ValueError("Invalid quantity or unit price range")
        line = {**item, "low": quantity * low, "high": quantity_high * high}
        lines.append(line)
        totals["low"] += line["low"]
        totals["high"] += line["high"]
    if set(selections) != selected_groups:
        raise ValueError("Each alternative selection must match a cost item")
    return {"items": lines, **totals}
