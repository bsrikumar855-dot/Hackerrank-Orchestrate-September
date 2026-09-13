"""Per-row checkpoint/resume for the 250-row batch. output.csv is written to
incrementally (flushed after every row) so a crash or rate limit partway
through does not require a full rerun -- request_ids already present are
skipped on the next invocation.
"""
from __future__ import annotations

import csv
from pathlib import Path

OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]


def load_completed(output_path: Path) -> dict[str, dict]:
    if not output_path.exists():
        return {}
    with output_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return {row["request_id"]: row for row in reader if row.get("request_id")}


class OutputWriter:
    """Append-only during a run; main.py does a final ordered rewrite once
    every request_id in requests.csv has a row, so the delivered output.csv
    always matches requests.csv's row order regardless of how many resumed
    runs it took to complete.
    """

    def __init__(self, output_path: Path):
        self.output_path = output_path
        self._completed = load_completed(output_path)
        resuming = bool(self._completed)
        self._fh = output_path.open("a" if resuming else "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=OUTPUT_COLUMNS)
        if not resuming:
            self._writer.writeheader()
            self._fh.flush()

    def already_done(self, request_id: str) -> bool:
        return request_id in self._completed

    def write(self, row: dict) -> None:
        self._writer.writerow(row)
        self._fh.flush()
        self._completed[row["request_id"]] = row

    def close(self) -> None:
        self._fh.close()
