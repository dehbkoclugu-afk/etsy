from __future__ import annotations

import os
import socket
import time
from typing import cast
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError, CommandParser

from pinforge_web.jobs import claim_due_job
from pinforge_web.etsy_sync import execute_claimed_etsy_sync
from pinforge_web.models import Job
from pinforge_web.publishing import execute_claimed_pinterest_publish
from pinforge_web.rendering import execute_claimed_job


class Command(BaseCommand):
    help = "Run the durable PinForge job worker."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--max-jobs", type=int, default=0)
        parser.add_argument("--poll-seconds", type=float, default=2.0)
        parser.add_argument("--worker-id", default="")

    def handle(self, *args: object, **options: object) -> None:
        once = cast(bool, options["once"])
        max_jobs = cast(int, options["max_jobs"])
        poll_seconds = cast(float, options["poll_seconds"])
        if max_jobs < 0:
            raise CommandError("--max-jobs cannot be negative")
        if not 0.1 <= poll_seconds <= 60:
            raise CommandError("--poll-seconds must be between 0.1 and 60")
        worker_id = cast(str, options["worker_id"]).strip() or _default_worker_id()
        processed = 0

        while True:
            job = claim_due_job(worker_id=worker_id)
            if job is None:
                if once or (max_jobs and processed >= max_jobs):
                    break
                time.sleep(poll_seconds)
                continue
            if job.kind == Job.Kind.RENDER_CREATIVE:
                execute_claimed_job(job, worker_id=worker_id)
            elif job.kind == Job.Kind.SYNC_ETSY:
                execute_claimed_etsy_sync(job, worker_id=worker_id)
            elif job.kind == Job.Kind.PUBLISH_PINTEREST:
                execute_claimed_pinterest_publish(job, worker_id=worker_id)
            else:
                raise CommandError(f"unsupported job kind: {job.kind}")
            processed += 1
            if once or (max_jobs and processed >= max_jobs):
                break

        noun = "job" if processed == 1 else "jobs"
        self.stdout.write(self.style.SUCCESS(f"processed {processed} {noun}"))


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"[:128]
