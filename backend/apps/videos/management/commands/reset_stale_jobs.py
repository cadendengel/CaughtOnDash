"""Management command to reset stale processing jobs."""

from django.core.management.base import BaseCommand
from apps.videos.worker_services import reset_stale_jobs


class Command(BaseCommand):
    help = 'Reset stale processing jobs back to pending status'

    def add_arguments(self, parser):
        parser.add_argument(
            '--timeout-minutes',
            type=int,
            default=2,
            help='Timeout in minutes to consider a job stale (default: 2)',
        )

    def handle(self, *args, **options):
        timeout_minutes = options['timeout_minutes']
        
        self.stdout.write(
            self.style.WARNING(f'Resetting stale jobs (timeout: {timeout_minutes} minutes)...')
        )
        
        result = reset_stale_jobs(timeout_minutes)
        count = result.get('reset_count', 0)
        
        if count > 0:
            self.stdout.write(
                self.style.SUCCESS(f'Successfully reset {count} stale job(s) back to pending.')
            )
        else:
            self.stdout.write(self.style.SUCCESS('No stale jobs found.'))
