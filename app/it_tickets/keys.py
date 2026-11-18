"""Pure helpers for Jira keys, imported dates and charts cells. No database access."""
import datetime as dt
import re

_KEY_RE = re.compile(r"VTEC\s*[-_]?\s*(\d+)", re.IGNORECASE)
_STANDALONE_KEY_RE = re.compile(r"^\s*VTEC\s*[-_]?\s*(\d+)\s*$", re.IGNORECASE)


def normalize_jira_key(value):
    """'VTEC 14444' / 'vtec13133' / ' VTEC-6314 ' -> 'VTEC-14444'. None if not a single key."""
    if value is None:
        return None
    match = _STANDALONE_KEY_RE.match(str(value))
    return f"VTEC-{int(match.group(1))}" if match else None


def split_jira_keys(value):
    """Every key in a tracker cell, normalised and de-duplicated in order.

    'VTEC-6314 & VTEC 18982' -> ['VTEC-6314', 'VTEC-18982'];
    'VTEC-12265,VTEC-16606 -09/08/2026' -> ['VTEC-12265', 'VTEC-16606'].
    """
    keys = []
    for match in _KEY_RE.finditer(str(value or "")):
        key = f"VTEC-{int(match.group(1))}"
        if key not in keys:
            keys.append(key)
    return keys


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y", "%Y/%m/%d")
_EXCEL_EPOCH = dt.date(1899, 12, 30)


def parse_import_date(value):
    """Dates as they arrive from a parsed workbook: ISO strings, US dates,
    'YYYY-MM-DDTHH:MM:SS' timestamps or Excel serial numbers. None if empty or unreadable."""
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _EXCEL_EPOCH + dt.timedelta(days=int(value)) if 20000 < value < 80000 else None
    text = str(value).strip()
    if re.fullmatch(r"\d{5}(\.\d+)?", text):
        return parse_import_date(float(text))
    text = text.split("T", 1)[0].split(" 00:00", 1)[0].strip()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_charts_cell(value):
    """(charts_available, not_in_kairon) from a tracker cell: a number, or text like 'Not available in Kairon'."""
    if value in (None, ""):
        return None, False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(int(value), 0), False
    text = str(value).strip()
    digits = text.replace(",", "")
    if re.fullmatch(r"\d+(\.0+)?", digits):
        return int(float(digits)), False
    if "not available" in text.lower() or "not in kairon" in text.lower():
        return None, True
    return None, False


def is_resolved_status(value):
    return bool(value) and "resolved" in str(value).lower()
