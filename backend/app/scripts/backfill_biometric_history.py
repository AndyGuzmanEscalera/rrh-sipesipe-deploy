"""One-time, manual RAW biometric history backfill orchestrator."""
import argparse
from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterable

from sqlalchemy import select

from app.biometric.models import Sync
from app.biometric.worker import enqueue, run_once
from app.core.clock import local_today
from app.core.config import get_settings
from app.db.session import get_engine, session_factory


DEFAULT_FROM = date(2026, 1, 1)


@dataclass(frozen=True)
class MonthlyRange:
    start: date
    end: date

    @property
    def label(self): return self.start.strftime('%Y-%m')


@dataclass(frozen=True)
class BackfillSummary:
    start: date; end: date; months_processed: int; months_completed: int
    received: int; inserted: int; duplicates: int; errors: int


class BackfillFailed(RuntimeError): pass


def monthly_ranges(start: date, end: date) -> Iterable[MonthlyRange]:
    if start > end: raise ValueError('--from no puede ser posterior a --to.')
    current = start
    while current <= end:
        next_month = date(current.year + 1, 1, 1) if current.month == 12 else date(current.year, current.month + 1, 1)
        yield MonthlyRange(current, min(date.fromordinal(next_month.toordinal() - 1), end))
        current = next_month


def run_backfill(start: date, end: date, *, factory=session_factory, bind=None, service_factory=None,
                 output: Callable[[str], None] = print) -> BackfillSummary:
    settings, ranges = get_settings(), tuple(monthly_ranges(start, end))
    completed = received = inserted = duplicates = errors = 0
    execution_bind = bind if bind is not None else get_engine()
    for interval in ranges:
        output(f'[{interval.label}] {interval.start.isoformat()} -> {interval.end.isoformat()}')
        with factory() as db:
            job = enqueue(db, settings.biometric_device_id, interval.start, interval.end, force=True); job_id = job.id
        kwargs = dict(factory=factory, bind=execution_bind)
        if service_factory is not None: kwargs['service_factory'] = service_factory
        run_once(**kwargs)
        with factory() as db:
            job = db.scalar(select(Sync).where(Sync.id == job_id))
            if job is None: raise BackfillFailed(f'No se encontró el lote {interval.label}.')
            received += job.records_received; inserted += job.records_inserted
            duplicates += job.records_duplicated; errors += job.error_records
            for name, value in [('received',job.records_received),('inserted',job.records_inserted),
                                ('duplicates',job.records_duplicated),('errors',job.error_records),('status',job.status)]:
                output(f'{name}: {value}')
            if job.status != 'completed': raise BackfillFailed(f'Falló el lote {interval.label}: {job.error_code or "biometric_sync_failed"}.')
        completed += 1
    return BackfillSummary(start, end, len(ranges), completed, received, inserted, duplicates, errors)


def _parse_date(value: str) -> date:
    try: return date.fromisoformat(value)
    except ValueError as exc: raise argparse.ArgumentTypeError('Use YYYY-MM-DD.') from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from', dest='start', type=_parse_date, default=DEFAULT_FROM)
    parser.add_argument('--to', dest='end', type=_parse_date, default=None)
    args = parser.parse_args(argv)
    try: summary = run_backfill(args.start, args.end or local_today())
    except (BackfillFailed, ValueError) as exc:
        print(f'BACKFILL FALLIDO: {exc}'); return 1
    print('BACKFILL COMPLETADO')
    for label, value in [('Periodo',f'{summary.start.isoformat()} -> {summary.end.isoformat()}'),('Meses procesados',summary.months_processed),('Meses completados',summary.months_completed),('Recibidos',summary.received),('Nuevos',summary.inserted),('Duplicados',summary.duplicates),('Errores',summary.errors)]: print(f'{label}: {value}')
    return 0


if __name__ == '__main__': raise SystemExit(main())
