import contextlib
import fcntl
import hashlib
import json
import os
import sqlite3
from pathlib import Path


class BusyError(RuntimeError):
    pass


class Store:
    def __init__(self, path='data/agent.db'):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.locks = self.path.parent / (self.path.name + '.locks')
        self.locks.mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    user_id TEXT NOT NULL, session_id TEXT NOT NULL, state TEXT NOT NULL,
                    PRIMARY KEY(user_id, session_id));
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
            ''')
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextlib.contextmanager
    def lock(self, user, session):
        name = hashlib.sha256(json.dumps([user, session]).encode()).hexdigest()
        with (self.locks / name).open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BusyError('Session busy; retry after the current turn completes.') from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def load(self, user, session):
        with self.connect() as db:
            row = db.execute('SELECT state FROM sessions WHERE user_id=? AND session_id=?',
                             (user, session)).fetchone()
        return json.loads(row[0]) if row else {'messages': [], 'summary': '', 'todos': [], 'active': False,
                                              'steps': 0, 'repairs': 0}

    def save(self, user, session, state, kind, payload):
        # State, tool side effect and trace commit together; todo never has a separate write.
        with self.connect() as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?) ON CONFLICT(user_id, session_id) '
                       'DO UPDATE SET state=excluded.state', (user, session, json.dumps(state)))
            db.execute('INSERT INTO events(user_id,session_id,kind,payload) VALUES(?,?,?,?)',
                       (user, session, kind, json.dumps(payload, ensure_ascii=False)))

    def events(self, user, session, limit=50):
        with self.connect() as db:
            rows = db.execute('SELECT seq,kind,payload,at FROM events WHERE user_id=? AND '
                              'session_id=? ORDER BY seq DESC LIMIT ?', (user, session, limit)).fetchall()
        return [{'seq': r[0], 'kind': r[1], 'payload': json.loads(r[2]), 'at': r[3]} for r in reversed(rows)]
