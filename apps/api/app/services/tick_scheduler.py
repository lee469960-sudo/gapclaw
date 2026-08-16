import asyncio
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database import SessionLocal
from app.models import Agent, AgentTick
from app.services.agent_runtime import run_agent

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None
_job_map: dict[str, str] = {}


def _run_tick(tick_id: str):
    db = SessionLocal()
    try:
        tick = db.query(AgentTick).filter(AgentTick.tick_id == tick_id, AgentTick.enabled == True).first()
        if not tick:
            return
        agent = db.query(Agent).filter(Agent.id == tick.agent_id).first()
        if not agent:
            return
        asyncio.run(run_agent(db, agent, tick.session_id, tick.message or "定时任务", tick.creator or "system"))
    except Exception as e:
        logger.exception("tick %s failed", tick_id)
    finally:
        db.close()


def start_scheduler():
    global _scheduler
    if _scheduler:
        return
    _scheduler = BackgroundScheduler()
    _scheduler.start()
    reload_all_ticks()


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        _job_map.clear()


def reload_all_ticks():
    if not _scheduler:
        return
    db = SessionLocal()
    try:
        for job_id in list(_job_map.values()):
            try:
                _scheduler.remove_job(job_id)
            except Exception:
                pass
        _job_map.clear()
        for tick in db.query(AgentTick).filter(AgentTick.enabled == True).all():
            add_tick_job(tick.tick_id, tick.cron)
    finally:
        db.close()


def add_tick_job(tick_id: str, cron: str):
    if not _scheduler:
        return
    if tick_id in _job_map:
        try:
            _scheduler.remove_job(_job_map[tick_id])
        except Exception:
            pass
    try:
        parts = cron.strip().split()
        if len(parts) == 5:
            trigger = CronTrigger(minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4])
        else:
            trigger = CronTrigger.from_crontab(cron)
        job = _scheduler.add_job(_run_tick, trigger, args=[tick_id], id=f"tick_{tick_id}", replace_existing=True)
        _job_map[tick_id] = job.id
    except Exception as e:
        logger.warning("invalid cron for tick %s: %s", tick_id, e)


def remove_tick_job(tick_id: str):
    if _scheduler and tick_id in _job_map:
        try:
            _scheduler.remove_job(_job_map[tick_id])
        except Exception:
            pass
        _job_map.pop(tick_id, None)
