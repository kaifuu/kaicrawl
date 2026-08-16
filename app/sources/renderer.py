# -*- coding: utf-8 -*-
"""Playwright 页面渲染：SPA / 反爬站点的取页手段。

render_mode=browser 的数据源经 render_html() 取页面 HTML：无头 Chromium
真实执行 JS，等待目标区域（XPath / CSS）出现或网络空闲后返回完整 DOM，
随后由解析器按普通 HTML 处理（list_xpath / content_xpath 均可用）。

Playwright sync API 的对象绑定创建线程，跨线程复用会报 greenlet 错误
（Cannot switch to a different thread）——手动抓取、调度器补跑、并发抓取
各自在不同线程触发渲染，因此这里维护一组**常驻渲染线程**（上限
config.RENDER_WORKERS，懒启动按需增长）统一持有各自的 playwright 与
浏览器实例：调用方把任务投递进共享队列并等待结果，Playwright 的整个
生命周期（启动/使用/崩溃重启/销毁）都发生在线程自己身上，天然线程安全。

每个渲染线程持有**持久 context**（跨任务复用，共享 HTTP 缓存，SPA 的
JS 包只下载一次）；任务自身只 new/close page。任务异常后检查浏览器
连通性，崩溃则原地重启 browser+context；每 100 个任务换一次 context
防长驻泄漏。
playwright 未安装 / 浏览器未下载时给出可操作的 ParserError 提示。
"""
import logging
import queue
import threading
import time

from config import DEFAULT_UA, RENDER_WORKERS
from ..utils import ParserError

_log = logging.getLogger(__name__)

_job_q = queue.Queue()            # 共享任务队列：所有渲染线程共同消费（一任务只被一线程取走）
_workers = []                     # [_RenderWorker, ...]，懒启动增长到 RENDER_WORKERS
_worker_guard = threading.Lock()
_CONTEXT_RECYCLE_JOBS = 100       # 每处理多少个任务换一次 context（防内存/DOM 泄漏）

_MISSING = object()


def _try_close(x):
    """尽力关闭 page/context，忽略一切异常（崩溃后的清理不遮蔽原始错误）。"""
    try:
        x.close()
    except Exception:
        pass


def _browser_ok(browser):
    try:
        return browser is not None and browser.is_connected()
    except Exception:
        return False


def _launch_browser():
    """在当前渲染线程内启动 playwright + Chromium + 持久 context。失败抛异常。"""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(user_agent=DEFAULT_UA, locale="zh-CN")
    return pw, browser, context


class _RenderWorker:
    """单个常驻渲染线程的句柄：就绪事件 + 启动失败原因 + 线程对象。"""

    def __init__(self):
        self.ready = threading.Event()   # 初始化完成（成功或失败）后置位
        self.init_error = None           # 启动失败原因（ParserError）
        self.thread = None


def _worker_loop(w):
    """渲染线程主体：启动浏览器，然后逐个执行队列里的渲染任务，崩溃自愈。"""
    pw = browser = context = None
    try:
        pw, browser, context = _launch_browser()
    except ImportError:
        w.init_error = ParserError(
            "playwright 未安装：请执行 pip install playwright && python -m playwright install chromium")
    except Exception as e:
        w.init_error = ParserError(f"启动无头浏览器失败：{e}（若为浏览器缺失，"
                                  f"请执行 python -m playwright install chromium）")
    finally:
        w.ready.set()
    if w.init_error is not None:
        return

    jobs_done = 0
    while True:
        job = _job_q.get()
        if job is None:
            break
        fn, box = job
        try:
            if context is None:  # 上轮 context 换新失败，先补建
                context = browser.new_context(user_agent=DEFAULT_UA, locale="zh-CN")
            box["ok"] = fn(context)
        except Exception as e:
            box["err"] = e
        finally:
            box["ev"].set()      # 无论成败立即置位，调用方绝不悬挂
        if not _browser_ok(browser):
            # 浏览器进程崩溃：复用同一 playwright 重启 browser + context
            _try_close(context)
            context = None
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as e:
                # 彻底起不来：若已无其它存活线程，把队列剩余任务全部标记失败防悬挂
                with _worker_guard:
                    alone = not _live_workers_locked()
                if alone:
                    _drain_queue(ParserError(f"渲染线程崩溃且无法重启浏览器：{e}"))
                _log.error("渲染线程浏览器重启失败，线程退出：%s", e)
                return
            jobs_done = 0
            continue
        jobs_done += 1
        if jobs_done >= _CONTEXT_RECYCLE_JOBS:
            _try_close(context)
            context = None
            jobs_done = 0


def _drain_queue(err):
    """最后一个渲染线程濒死时，把队列中未被消费的任务标记失败，避免等到超时。"""
    while True:
        try:
            _, box = _job_q.get_nowait()
        except queue.Empty:
            return
        box["err"] = err
        box["ev"].set()


def _spawn_one_locked():
    """在 _worker_guard 内启动一个新渲染线程（不等待就绪）。"""
    w = _RenderWorker()
    w.thread = threading.Thread(target=_worker_loop, args=(w,),
                                name=f"playwright-renderer-{len(_workers)}", daemon=True)
    _workers.append(w)
    w.thread.start()
    return w


def _live_workers_locked():
    """存活线程列表；顺带清理已退出的条目（初始化失败/致命崩溃的线程会自然退出）。"""
    alive = [w for w in _workers if w.thread is not None and w.thread.is_alive()]
    _workers[:] = alive
    return alive


def _ensure_workers():
    """确保至少一个渲染线程存活并就绪；启动失败抛 ParserError（下次调用会重试）。"""
    with _worker_guard:
        if not _live_workers_locked():
            w = _spawn_one_locked()
            if not w.ready.wait(timeout=90):
                raise ParserError("渲染线程启动超时，请重试")
            if w.init_error is not None:
                _workers.remove(w)   # 允许下次重试（例如装好 playwright 后）
                raise w.init_error


def _maybe_grow_locked():
    """队列有积压且未达上限时再起一个渲染线程（懒扩容：单篇任务不白开多个 Chromium）。"""
    if _job_q.qsize() > 0 and len(_live_workers_locked()) < max(1, RENDER_WORKERS):
        _spawn_one_locked()


def _submit(fn, timeout_s=600):
    """把渲染任务交给渲染线程执行，返回 fn(context) 的结果（纯数据，可跨线程）。"""
    _ensure_workers()
    box = {"ev": threading.Event(), "ok": _MISSING, "err": None}
    _job_q.put((fn, box))
    with _worker_guard:
        _maybe_grow_locked()
    if not box["ev"].wait(timeout=timeout_s):
        raise ParserError(f"渲染任务超时（>{timeout_s}s），请稍后重试")
    if box["err"] is not None:
        raise box["err"]
    if box["ok"] is _MISSING:
        raise ParserError("渲染线程异常退出，请重试")
    return box["ok"]


def render_html(url, *, wait_xpath=None, wait_css=None, timeout_ms=None):
    """渲染页面并返回完整 HTML。

    wait_xpath / wait_css 非空时等待该元素出现（正文容器就绪的标志），
    命中后仅短暂停顿即返回；等不到才退回 networkidle 兜底（超时按已加载
    内容返回，交上层判断匹配）。
    """
    def _job(context):
        page = context.new_page()
        try:
            page.goto(url, timeout=timeout_ms or 40000, wait_until="domcontentloaded")
            got = False
            if wait_xpath:
                try:
                    page.wait_for_selector(f"xpath={wait_xpath}", timeout=8000)
                    got = True
                except Exception:
                    pass  # 等不到就绪标志，按已加载内容继续
            elif wait_css:
                try:
                    page.wait_for_selector(wait_css, timeout=8000)
                    got = True
                except Exception:
                    pass
            if got:
                # 就绪标志已出现：短停顿补懒加载图片/异步片段，不再等必超时的 networkidle
                try:
                    page.wait_for_timeout(600)
                except Exception:
                    pass
            else:
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass
            return page.content()
        finally:
            _try_close(page)

    try:
        return _submit(_job)
    except ParserError:
        raise
    except Exception as e:
        raise ParserError(f"渲染失败：{url} -> {e}")


def collect_click_links(url, item_selector, *, max_items=30, page_timeout_ms=40000,
                        popup_timeout_ms=3000, poll_ms=120, settle_ms=1200):
    """渲染 SPA 页面并逐项点击列表元素，收集 window.open 弹出的目标 URL。

    适用于列表项无 <a href>、跳转由 JS window.open 触发的站点（如学习强国：
    项为 div + 点击弹窗，目标 URL 只存在于 JS 内部）。返回 [(项文本, URL), ...]，
    顺序与页面一致（假定按日期降序）。单项点击/弹窗失败仅跳过该项，不中断整体。

    弹窗等待为事件驱动：popup 事件回调只记录 Page 对象（回调内任何等待都会
    阻塞事件派发），主循环轮询其 URL——wait_for_timeout 兼作事件泵，推动弹窗
    从 about:blank 导航到目标地址，命中即继续（替代旧的固定 2.5s 睡眠）。
    """
    def _job(context):
        page = context.new_page()
        results = []
        try:
            popups = []

            def on_popup(p):
                popups.append(p)   # 立即记录，绝不等待

            page.on("popup", on_popup)   # 页面级监听：随 page 关闭自动清理，不污染持久 context
            page.goto(url, timeout=page_timeout_ms, wait_until="domcontentloaded")
            try:
                page.wait_for_selector(item_selector, timeout=25000)
            except Exception:
                raise ParserError(f"等待列表项超时（{item_selector}）：{url}")
            page.wait_for_timeout(settle_ms)

            idx = 0
            while idx < max_items:
                items = page.query_selector_all(item_selector)
                if idx >= len(items):
                    break  # 页面项数少于上限
                popups.clear()
                try:
                    text = (items[idx].inner_text() or "").strip()
                    items[idx].click()
                except Exception:
                    idx += 1
                    continue
                target = ""
                deadline = time.monotonic() + popup_timeout_ms / 1000
                while time.monotonic() < deadline:
                    if popups:
                        u = popups[0].url
                        if u and u != "about:blank":
                            target = u
                            break
                    try:
                        page.wait_for_timeout(poll_ms)
                    except Exception:
                        break
                while popups:  # 每项收尾关干净弹窗
                    _try_close(popups.pop())
                if target:
                    results.append((text, target))
                idx += 1
            return results
        finally:
            _try_close(page)

    try:
        return _submit(_job)
    except ParserError:
        raise
    except Exception as e:
        raise ParserError(f"渲染点击采集失败：{url} -> {e}")
