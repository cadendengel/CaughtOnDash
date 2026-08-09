"""Liveness endpoint for an external uptime monitor.

Deliberately not in an app: this reports on the deployment, not on videos or
accounts, and an operational endpoint buried in a feature module is one that
gets forgotten.

Two decisions here are the whole point of the file, and both come from the
outage on 8 August 2026:

It runs a real query. Under a pooler, `connection` can exist while the pooler
refuses to hand out a backend -- that is exactly the shape of EMAXCONNSESSION.
A check that only inspected the connection object would have answered "ok"
for every one of those hours, which is worse than having no check at all.

Unapplied migrations fail the check rather than appearing as a field. Production
once ran for a day against a schema its code did not match, and every video
endpoint returned 500. Monitors act on status codes and nothing else, so a
condition that has already caused an outage has to be a 503.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET


def _pending_migrations() -> list[str]:
    """App labels and names of migrations that exist but have not been applied.

    Reuses the connection the caller has already proved is alive.
    """
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    return [f'{migration.app_label}.{migration.name}' for migration, _backwards in executor.migration_plan(targets)]


# csrf_exempt so a non-GET is answered by require_GET with 405 rather than by
# the CSRF middleware with 403. It grants nothing: require_GET already refuses
# every method that could write, and this view has no side effects.
@csrf_exempt
@require_GET
@never_cache
def health(request) -> JsonResponse:
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 -- reporting failure is the job
        # The exception text can name the host and the pooler error code, which
        # is most of the diagnosis. It reveals nothing an attacker could not
        # learn from a 500, and this endpoint returns no data.
        return JsonResponse(
            {
                'status': 'error',
                'database': 'error',
                'migrations': 'unknown',
                'detail': str(exc),
            },
            status=503,
        )

    try:
        pending = _pending_migrations()
    except Exception as exc:  # noqa: BLE001
        # The database answered, so this is a migration-graph problem rather
        # than an outage. Say so instead of claiming the database is down.
        return JsonResponse(
            {
                'status': 'error',
                'database': 'ok',
                'migrations': 'unknown',
                'detail': str(exc),
            },
            status=503,
        )

    if pending:
        return JsonResponse(
            {
                'status': 'error',
                'database': 'ok',
                'migrations': 'pending',
                'pending': pending,
            },
            status=503,
        )

    return JsonResponse({'status': 'ok', 'database': 'ok', 'migrations': 'applied'})
