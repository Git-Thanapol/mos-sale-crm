"""Holiday/leave days used by the Daily Sell Matrix (/daily-sales-matrix/)
to gray out a whole day (scope=ALL, "วันหยุด") or a single person's cell
(scope=INDIVIDUAL, "วันลา") — see crm/matrix/selectors.py::daily_matrix.
"""

from __future__ import annotations

from django.db import models


class Holiday(models.Model):
    SCOPE_ALL = "ALL"
    SCOPE_INDIVIDUAL = "INDIVIDUAL"
    SCOPE_CHOICES = [(SCOPE_ALL, "ทุกคน"), (SCOPE_INDIVIDUAL, "รายคน")]

    STATUS_HOLIDAY = "HOLIDAY"
    STATUS_LEAVE = "LEAVE"
    STATUS_CHOICES = [(STATUS_HOLIDAY, "วันหยุด"), (STATUS_LEAVE, "วันลา")]

    date = models.DateField()
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_HOLIDAY)
    user = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.CASCADE, related_name="leave_days"
    )
    note = models.CharField(max_length=255, blank=True)
    created_by = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "crm_holiday"
        indexes = [models.Index(fields=["date"], name="ix_holiday_date")]
        ordering = ["date"]

    def __str__(self) -> str:
        who = self.user.staff_name if self.scope == self.SCOPE_INDIVIDUAL and self.user_id else "ทุกคน"
        return f"{self.date} {who} {self.status}"
