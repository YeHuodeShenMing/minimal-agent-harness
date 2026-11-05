"""Small, single-process web adapter. The CLI runtime remains framework-free."""
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import load_env
from .model import ChatModel
from .runtime import Runtime, size
from .store import Store
from .titles import automatic_title, fallback_title, summarize_title


class NewSession(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=60)


class RenameSession(BaseModel):
    title: str = Field(min_length=1, max_length=60)


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=12000)


def create_app(*, model=None, db_path=None, secret=None, secure=None):
    load_env()
    secret = secret or os.environ.get("WEB_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("Set WEB_SECRET (32+ chars)")
    secure = os.environ.get("COOKIE_SECURE", "1") == "1" if secure is None else secure
    allowed_hosts = {x.strip() for x in os.environ.get("WEB_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")}
    store = Store(db_path or os.environ.get("AGENT_DB", "data/web.db"))
    runtime = Runtime(model or ChatModel(), store)
    app = FastAPI(title="Minimal Agent", docs_url=None, redoc_url=None, openapi_url=None)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent")
    guard = threading.Lock()
    running = set()
    visitor_attempts = {}
    static = Path(__file__).with_name("static")
    with store.connect() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS web_sessions (
                user_id TEXT NOT NULL, id TEXT PRIMARY KEY, title TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS web_sessions_user ON web_sessions(user_id);
            CREATE TABLE IF NOT EXISTS web_usage (
                day TEXT NOT NULL, user_id TEXT NOT NULL, turns INTEGER NOT NULL,
                PRIMARY KEY(day,user_id));
        ''')

    # Migrate existing databases without replacing conversations or custom titles.
    with store.connect() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(web_sessions)")}
        if "title_mode" not in columns:
            db.execute("ALTER TABLE web_sessions ADD COLUMN title_mode TEXT NOT NULL DEFAULT 'manual'")
            for row in db.execute("SELECT id,title FROM web_sessions").fetchall():
                if automatic_title(row[1]):
                    db.execute("UPDATE web_sessions SET title_mode='pending' WHERE id=?", (row[0],))
        db.execute("UPDATE web_sessions SET title_mode='pending' WHERE title_mode='generating'")

    def digest(value):
        return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()

    def cookie_value(user):
        value = f"{user}.{int(time.time()) + 7 * 86400}"
        return value + "." + digest(value)

    def user_id(request: Request):
        token = request.cookies.get("agent_session", "")
        try:
            user, expiry, signature = token.split(".")
            if (len(user) != 32 or int(expiry) < time.time()
                    or not hmac.compare_digest(signature, digest(user + "." + expiry))):
                raise ValueError
            return user
        except (ValueError, TypeError):
            raise HTTPException(401, "会话已过期，请刷新页面") from None

    def csrf(request: Request, user=Depends(user_id)):
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), digest("csrf:" + user)):
            raise HTTPException(403, "请求验证失败，请刷新页面")
        return user

    def owned(session, user):
        with store.connect() as db:
            row = db.execute("SELECT title FROM web_sessions WHERE id=? AND user_id=?", (session, user)).fetchone()
        if row is None:
            raise HTTPException(404, "会话不存在")
        return row[0]

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        host = urlsplit("//" + request.headers.get("host", "")).hostname
        if host not in allowed_hosts:
            return JSONResponse({"detail": "Host not allowed"}, status_code=400)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
            try:
                if int(request.headers.get("content-length", "0")) > 20000:
                    return JSONResponse({"detail": "请求过长"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Invalid length"}, status_code=400)
            # Also bound chunked bodies, which do not declare Content-Length.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 20000:
                    return JSONResponse({"detail": "请求过长"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers.update({
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            "Cache-Control": "no-store",
        })
        return response

    @app.get("/healthz")
    def health():
        return {"ok": True}

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.post("/api/visitor")
    def visitor(request: Request, response: Response):
        try:
            user = user_id(request)
            return {"ok": True, "csrf": digest("csrf:" + user)}
        except HTTPException:
            pass
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        with guard:
            attempts = [t for t in visitor_attempts.get(ip, []) if now - t < 600]
            if len(attempts) >= 10:
                raise HTTPException(429, "尝试过于频繁，请十分钟后重试")
            if len(visitor_attempts) > 5000:
                for key in list(visitor_attempts):
                    if not visitor_attempts[key] or now - visitor_attempts[key][-1] >= 600:
                        visitor_attempts.pop(key)
                if len(visitor_attempts) > 5000:
                    raise HTTPException(429, "请稍后重试")
            attempts.append(now)
            visitor_attempts[ip] = attempts
        user = secrets.token_hex(16)
        response.set_cookie("agent_session", cookie_value(user), max_age=7 * 86400,
                            httponly=True, secure=secure, samesite="lax", path="/")
        return {"ok": True, "csrf": digest("csrf:" + user)}

    @app.get("/api/me")
    def me(user=Depends(user_id)):
        return {"csrf": digest("csrf:" + user), "model": runtime.model.model if hasattr(runtime.model, "model") else "test-model",
                "max_steps": runtime.max_steps, "budget_bytes": runtime.budget}

    @app.get("/api/sessions")
    def sessions(user=Depends(user_id)):
        with store.connect() as db:
            rows = db.execute("SELECT id,title,updated FROM web_sessions WHERE user_id=? ORDER BY updated DESC", (user,)).fetchall()
        return [{"id": r[0], "title": r[1], "updated": r[2]} for r in rows]

    @app.post("/api/sessions", status_code=201)
    def new_session(payload: NewSession, user=Depends(csrf)):
        session = secrets.token_hex(16)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT count(*) FROM web_sessions WHERE user_id=?", (user,)).fetchone()[0]
            if count >= 20:
                raise HTTPException(429, "演示最多创建 20 个会话")
            title = payload.title.strip() or "新对话"
            db.execute("INSERT INTO web_sessions(user_id,id,title,created,updated,title_mode) VALUES(?,?,?,?,?,?)",
                       (user, session, title, time.time(), time.time(), 'pending' if automatic_title(title) else 'manual'))
        return {"id": session}

    @app.patch("/api/sessions/{session}")
    def rename_session(session: str, payload: RenameSession, user=Depends(csrf)):
        owned(session, user)
        title = ' '.join(payload.title.split())
        if not title:
            raise HTTPException(422, "标题不能为空")
        with store.connect() as db:
            db.execute("UPDATE web_sessions SET title=?,title_mode='manual' WHERE id=? AND user_id=?",
                       (title, session, user))
        return {"id": session, "title": title}

    @app.get("/api/sessions/{session}")
    def snapshot(session: str, user=Depends(user_id)):
        title = owned(session, user)
        state = store.load(user, session)
        events = store.events(user, session, 80)
        # Compaction archives can be large. Show its real summary and count, not a full duplicate archive.
        for event in events:
            payload = event["payload"]
            if event["kind"] == "compaction":
                payload["archived_count"] = len(payload.pop("archived_messages", []))
            payload.pop("decision_summary", None)
        with guard:
            busy = (user, session) in running
        return {"id": session, "title": title, "messages": state["messages"], "todos": state["todos"],
                "summary": state["summary"], "active": state["active"], "busy": busy,
                "steps": state["steps"], "context_bytes": size(runtime.prompt(state)) + size(runtime.registry.specs()),
                "budget_bytes": runtime.budget, "events": events}

    def generate_title(user, session):
        state = store.load(user, session)
        first = next((m['content'] for m in state['messages'] if m['role'] == 'user'), '')
        if not first:
            return
        with store.connect() as db:
            claimed = db.execute("UPDATE web_sessions SET title=?,title_mode='generating' WHERE id=? AND user_id=? AND title_mode='pending'",
                                 (fallback_title(first), session, user)).rowcount
        if not claimed:
            return
        answer = next((m.get('content') or '' for m in reversed(state['messages'])
                       if m['role'] == 'assistant' and not m.get('tool_calls')), '')
        try:
            title = summarize_title(runtime.model, first, answer)
        except Exception:
            # A title failure must not fail a successful conversation or leak provider errors.
            title = fallback_title(first)
        with store.connect() as db:
            db.execute("UPDATE web_sessions SET title=?,title_mode='auto' WHERE id=? AND user_id=? AND title_mode='generating'",
                       (title, session, user))

    def work(user, session, text):
        try:
            runtime.chat(user, session, text)
        except Exception:
            logging.exception("Agent execution interrupted; session can be resumed")
        finally:
            with guard:
                running.discard((user, session))
        try:
            generate_title(user, session)
        except Exception:
            logging.warning("Session title generation unavailable")

    def submit(user, session, text):
        owned(session, user)
        if text is not None and (not text.strip() or len(text.encode()) > 12000):
            raise HTTPException(422, "请输入内容，最多 12000 UTF-8 字节")
        with guard:
            if (user, session) in running:
                raise HTTPException(409, "当前会话正在执行，请等待完成")
            if len(running) >= 2:
                raise HTTPException(429, "演示服务正在处理其他请求，请稍后重试")
            state = store.load(user, session)
            if text is not None and state["active"]:
                raise HTTPException(409, "有中断的任务，请先点击恢复任务")
            if text is None and not state["active"]:
                raise HTTPException(409, "没有需要恢复的任务")
            day = time.strftime("%Y-%m-%d", time.gmtime())
            with store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                total = db.execute("SELECT coalesce(sum(turns),0) FROM web_usage WHERE day=?", (day,)).fetchone()[0]
                row = db.execute("SELECT turns FROM web_usage WHERE day=? AND user_id=?", (day, user)).fetchone()
                if total >= int(os.environ.get("WEB_DAILY_LIMIT", "80")) or (row and row[0] >= 20):
                    raise HTTPException(429, "今日演示调用额度已用完")
                db.execute("INSERT INTO web_usage VALUES(?,?,1) ON CONFLICT(day,user_id) DO UPDATE SET turns=turns+1", (day, user))
                db.execute("UPDATE web_sessions SET updated=? WHERE id=? AND user_id=?", (time.time(), session, user))
                if text is not None and not state['messages']:
                    db.execute("UPDATE web_sessions SET title=? WHERE id=? AND user_id=? AND title_mode='pending'",
                               (fallback_title(text), session, user))
            running.add((user, session))
            executor.submit(work, user, session, text)
        return {"accepted": True}

    @app.post("/api/sessions/{session}/messages", status_code=202)
    def send(session: str, payload: Message, user=Depends(csrf)):
        return submit(user, session, payload.text)

    @app.post("/api/sessions/{session}/resume", status_code=202)
    def resume(session: str, user=Depends(csrf)):
        return submit(user, session, None)

    app.mount("/static", StaticFiles(directory=static), name="static")
    app.state.executor = executor
    app.state.store = store
    return app
