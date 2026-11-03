"""Kafka consumer: ledger.events → detector. Run with ``python -m app.detector``."""

import asyncio
import json
import logging
import signal

import psycopg
from aiokafka import AIOKafkaConsumer

from app.config import Settings
from app.db import connect, migrate
from app.detector.store import process_event

log = logging.getLogger("detector")


def _process_batch(url_holder: dict, events: list[dict], settings: Settings) -> int:
    """Processes events in order, reconnecting once if the DB connection dropped."""
    cfg = settings.rules()
    processed = 0
    for event in events:
        for attempt in (1, 2):
            try:
                if process_event(url_holder["conn"], event, cfg) is not None:
                    processed += 1
                break
            except psycopg.OperationalError:
                if attempt == 2:
                    raise
                log.warning("db connection lost; reconnecting")
                url_holder["conn"] = connect(settings.agent_database_url)
    return processed


async def run() -> None:
    settings = Settings()
    applied = migrate(settings.agent_database_url)
    if applied:
        log.info("applied migrations: %s", applied)
    holder = {"conn": connect(settings.agent_database_url)}

    consumer = AIOKafkaConsumer(
        settings.ledger_events_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.detector_group_id,
        enable_auto_commit=False,  # commit offsets only after the DB transaction committed
        auto_offset_reset="earliest",
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    await consumer.start()
    log.info("detector consuming %s (time_scale=%s)", settings.ledger_events_topic, settings.detector_time_scale)
    try:
        while not stop.is_set():
            batches = await consumer.getmany(timeout_ms=500, max_records=500)
            if not batches:
                continue
            events: list[dict] = []
            for records in batches.values():
                for rec in records:
                    try:
                        events.append(json.loads(rec.value))
                    except (ValueError, TypeError):
                        log.error("skipping undecodable message at %s:%s", rec.partition, rec.offset)
            n = await asyncio.to_thread(_process_batch, holder, events, settings)
            await consumer.commit()
            if n:
                log.debug("processed %d new events", n)
    finally:
        await consumer.stop()
        holder["conn"].close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
