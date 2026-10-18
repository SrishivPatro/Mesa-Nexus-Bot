"""APScheduler jobs, all in IST."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .jobs import run_safely
from .services import Services
from .timeutil import IST

log = logging.getLogger(__name__)

SCHEDULE = {
    # name: (cron kwargs, description)
    "morning": ({"hour": 8, "minute": 0}, "Morning brief · daily 8:00 am IST"),
    "deadlines": ({"minute": 5}, "Deadline alert · every hour at :05"),
    "grades": ({"hour": 18, "minute": 0}, "Grade release alert · daily 6:00 pm IST"),
    "weekly": ({"day_of_week": "sun", "hour": 21, "minute": 0}, "Weekly summary · Sunday 9:00 pm IST"),
}


def build_scheduler(svc: Services) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(
        timezone=IST,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 45 * 60},
    )
    for name, (cron, description) in SCHEDULE.items():
        scheduler.add_job(run_safely, CronTrigger(timezone=IST, **cron), args=[name, svc],
                          id=name, name=description, replace_existing=True)
    return scheduler


def describe(scheduler: BackgroundScheduler) -> list[dict[str, str]]:
    return [{"id": j.id, "name": j.name,
             "next_run": j.next_run_time.isoformat() if j.next_run_time else "paused"}
            for j in scheduler.get_jobs()]
