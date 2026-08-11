# -*- coding: utf-8 -*-
"""APScheduler 集成：任务持久化、启动 reconcile、立即运行。

调度器使用同一 SQLite 库的 apscheduler_jobs 表持久化，重启后任务不丢；
启动时按 DB 中启用的 Task 行重建作业。立即运行由路由起后台线程执行。
"""
import atexit
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
import pytz

from .extensions import db
from .models import Task
from . import crawler
from config import DB_URI

_scheduler = None
TIMEZONE = pytz.timezone("Asia/Shanghai")

# 模块级 app 引用：APScheduler 持久化作业的回调必须是可被 import 引用的模块级函数，
# 不能用闭包（无法 pickle）。回调运行时通过此引用压入 application context。
_APP = None


def set_app(app):
    global _APP
    _APP = app


def scheduled_runner(task_id):
    """APScheduler 作业入口（模块级，可持久化引用）。"""
    if _APP is not None:
        with _APP.app_context():
            crawler.run_task(task_id)


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        jobstore = SQLAlchemyJobStore(url=DB_URI, tablename="apscheduler_jobs")
        _scheduler = BackgroundScheduler(
            jobstores={"default": jobstore},
            timezone=TIMEZONE,
            job_defaults={"max_instances": 1, "coalesce": True},
        )
        _scheduler.start()
        atexit.register(lambda: _scheduler.shutdown(wait=False) if _scheduler.running else None)
    return _scheduler


def _job_id(task_id):
    return f"task_{task_id}"


def _add_or_replace(sched, task):
    h, m = task.hour_minute
    sched.add_job(
        scheduled_runner, trigger=CronTrigger(hour=h, minute=m, timezone=TIMEZONE),
        args=[task.id], id=_job_id(task.id), replace_existing=True,
    )


def sync_task(app, task):
    """任务变更后调用：启用则注册/更新作业，禁用则移除。"""
    # 确保 app 引用已登记，供 scheduled_runner 在调度回调中压入上下文
    set_app(app)
    sched = get_scheduler()
    jid = _job_id(task.id)
    if task.enabled:
        _add_or_replace(sched, task)
    else:
        if sched.get_job(jid):
            sched.remove_job(jid)


def remove_task(task_id):
    sched = get_scheduler()
    jid = _job_id(task_id)
    if sched.get_job(jid):
        sched.remove_job(jid)


def reconcile(app):
    """启动时：按 DB 启用任务重建作业，清理无效作业。"""
    set_app(app)
    with app.app_context():
        sched = get_scheduler()
        valid_ids = set()
        for t in Task.query.all():
            valid_ids.add(_job_id(t.id))
            if t.enabled:
                _add_or_replace(sched, t)
            else:
                jid = _job_id(t.id)
                if sched.get_job(jid):
                    sched.remove_job(jid)
        # 清理 DB 中已不存在的任务作业
        for j in list(sched.get_jobs()):
            if j.id.startswith("task_") and j.id not in valid_ids:
                sched.remove_job(j.id)


def run_now(app, source_id, task_id=None, overwrite=False, since_date=None, limit=None):
    """立即在后台线程抓取某来源（不影响页面响应）。

    overwrite=True 时覆盖已有文章；since_date(YYYY-MM-DD) 非空时回溯抓取（见 crawler.run_source）。
    limit 为本次最多抓取篇数（界面输入），None 时用解析器默认上限。
    """
    def _bg():
        with app.app_context():
            crawler.run_source(source_id, task_id=task_id,
                               overwrite=overwrite, since_date=since_date, limit=limit)
    threading.Thread(target=_bg, daemon=True).start()


def next_run_time(task_id):
    """返回某任务下次运行时间的字符串（供页面展示），无则空。"""
    sched = get_scheduler()
    j = sched.get_job(_job_id(task_id))
    if j and j.next_run_time:
        return j.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    return ""
