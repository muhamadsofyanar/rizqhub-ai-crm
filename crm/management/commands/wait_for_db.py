import time
from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = "Wait until database is available"

    def handle(self, *args, **options):
        self.stdout.write("Waiting for database...")
        for attempt in range(60):
            try:
                connections["default"].cursor()
                self.stdout.write(self.style.SUCCESS("Database available"))
                return
            except OperationalError:
                time.sleep(2)
        raise OperationalError("Database unavailable after 120 seconds")
