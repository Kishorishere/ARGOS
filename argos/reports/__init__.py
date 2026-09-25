"""Reporting — daily / weekly / monthly aggregates + NEPSE watch dashboard."""

from argos.reports.daily import daily_report
from argos.reports.weekly import weekly_report
from argos.reports.monthly import monthly_report
from argos.reports.dashboard import nepse_watch_dashboard

__all__ = ["daily_report", "weekly_report", "monthly_report", "nepse_watch_dashboard"]
