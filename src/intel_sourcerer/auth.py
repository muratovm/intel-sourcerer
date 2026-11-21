"""Password accounts and revocable sessions for the local application."""
from pathlib import Path
from contextlib import contextmanager, closing
import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid
import re
import shutil
from http.cookies import SimpleCookie
from .storage import BUSY_TIMEOUT, connect_database, use_write_ahead_log


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=32768,r=8,p=3,maxmem=64*1024*1024,dklen=32).hex()


class Accounts:
    def __init__(self,database,config):
        self.legacy=Path(database).resolve()
        self.original_config=Path(config).resolve()
        self.path=self.legacy.parent/'accounts.sqlite3'
        with self.transaction() as db:
            use_write_ahead_log(db)
            db.executescript('''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,salt TEXT NOT NULL,password_hash TEXT NOT NULL,admin INTEGER NOT NULL,database_path TEXT NOT NULL,config_path TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT,csrf TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(key TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS attempts_key_created ON attempts(key,created);
            CREATE INDEX IF NOT EXISTS sessions_expires ON sessions(expires);
            CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);''')
            row = db.execute("SELECT value FROM meta WHERE key='anon_key'").fetchone()
            if row:
                self._anon_key = bytes.fromhex(row['value'])
            else:
                key_hex = secrets.token_hex(32)
                db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES ('anon_key',?)", (key_hex,))
                self._anon_key = bytes.fromhex(db.execute("SELECT value FROM meta WHERE key='anon_key'").fetchone()['value'])

    def connect(self):
        db=sqlite3.connect(self.path,timeout=BUSY_TIMEOUT);db.row_factory=sqlite3.Row;return db

    @contextmanager
    def transaction(self):
        with closing(self.connect()) as db, db:
            yield db

    def change_password(self, user_id, current, new):
        if not isinstance(new, str) or len(new) < 12 or len(new.encode()) > 1024:
            raise ValueError("Use a password of at least 12 characters (maximum 1024 bytes).")
        if not isinstance(current, str) or len(current.encode()) > 1024:
            raise ValueError("The current password is incorrect.")
        with self.transaction() as db:
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not user or not secrets.compare_digest(password_hash(current, user["salt"]), user["password_hash"]):
                raise ValueError("The current password is incorrect.")
            salt = secrets.token_hex(16)
            db.execute("UPDATE users SET salt=?,password_hash=? WHERE id=?", (salt, password_hash(new, salt), user_id))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def delete_account(self, user_id, username, password):
        """Permanently remove an account and everything stored for it, once its owner proves who they are."""
        if not isinstance(password, str) or len(password.encode()) > 1024:
            raise ValueError("The username or password is incorrect.")
        with self.transaction() as db:
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if (
                not user
                or str(username).strip().lower() != user["username"]
                or not secrets.compare_digest(password_hash(password, user["salt"]), user["password_hash"])
            ):
                raise ValueError("The username or password is incorrect.")
            if user["admin"]:
                other_users = db.execute("SELECT COUNT(*) FROM users WHERE id<>?", (user_id,)).fetchone()[0]
                other_admins = db.execute("SELECT COUNT(*) FROM users WHERE id<>? AND admin=1", (user_id,)).fetchone()[0]
                if other_users > 0 and other_admins == 0:
                    raise ValueError("Cannot delete the only administrator while other accounts exist.")
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM users WHERE id=?", (user_id,))
        database, directory = Path(user["database_path"]), self.legacy.parent / "users" / user_id
        shutil.rmtree(directory, ignore_errors=True)
        if database.resolve() == self.legacy:
            # The first account's library sits beside the shared account data rather than in its own folder.
            for suffix in ("", "-wal", "-shm"):
                Path(str(self.legacy) + suffix).unlink(missing_ok=True)
            for name in ("backups", "media", "pdf-assets", "uploads", "transcribe-uploads"):
                shutil.rmtree(self.legacy.parent / name, ignore_errors=True)

    def empty(self):
        with self.transaction() as db:return not db.execute('SELECT 1 FROM users').fetchone()

    def create_user(self,username,password,first=False):
        username=str(username).strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,63}',username):
            raise ValueError('Use a username of 3–64 letters, numbers, dots, underscores or hyphens.')
        if not isinstance(password,str) or len(password)<12 or len(password.encode())>1024:
            raise ValueError('Use a password of at least 12 characters (maximum 1024 bytes).')
        salt=secrets.token_hex(16);hashed=password_hash(password,salt);identifier=uuid.uuid4().hex
        with self.transaction() as db:
            db.execute('BEGIN IMMEDIATE')
            if first and db.execute('SELECT 1 FROM users').fetchone():
                raise ValueError('Setup is already complete. Sign in.')
            if db.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():
                raise ValueError('That username is already taken.')
            directory=self.legacy.parent/'users'/identifier
            directory.mkdir(parents=True,exist_ok=True)
            database=self.legacy if first else directory/'articles.sqlite3'
            config=directory/'sources.toml'
            if self.original_config.is_file():shutil.copyfile(self.original_config,config)
            else:config.write_text('sources = []\n')
            connect_database(database).close()
            db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)',(identifier,username,salt,hashed,int(first),str(database),str(config)))
        return identifier

    def _sign_anon(self, csrf, expires):
        payload = f"{csrf}.{expires}".encode()
        return hmac.new(self._anon_key, payload, hashlib.sha256).hexdigest()

    def session(self,cookie):
        try:
            parsed=SimpleCookie();parsed.load(cookie or '')
            token=parsed['intel_session'].value
        except Exception:return None
        if token.startswith('anon.'):
            parts = token.split('.')
            if len(parts) != 4:
                return None
            _, csrf, exp_str, sig = parts
            try:
                expires = int(exp_str)
            except ValueError:
                return None
            if expires <= time.time() or not secrets.compare_digest(sig, self._sign_anon(csrf, expires)):
                return None
            return {
                'token_hash': None,
                'user_id': None,
                'csrf': csrf,
                'expires': float(expires),
                'username': None,
                'admin': 0,
                'database_path': None,
                'config_path': None,
            }
        with self.transaction() as db:
            row=db.execute('SELECT s.*,u.username,u.admin,u.database_path,u.config_path FROM sessions s LEFT JOIN users u ON u.id=s.user_id WHERE token_hash=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
            return dict(row) if row else None

    def new_session(self,user_id=None):
        csrf=secrets.token_urlsafe(32)
        if user_id is None:
            expires = int(time.time() + 1800)
            token = f"anon.{csrf}.{expires}.{self._sign_anon(csrf, expires)}"
            return token, csrf
        token=secrets.token_urlsafe(32)
        with self.transaction() as db:
            db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user_id,csrf,time.time()+8*3600))
        return token,csrf

    def revoke(self,session):
        if session and session.get('token_hash'):
            with self.transaction() as db:db.execute('DELETE FROM sessions WHERE token_hash=?',(session['token_hash'],))

    def active_sessions_count(self, user_id):
        if not user_id:
            return 0
        with self.transaction() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            return db.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?', (user_id,)).fetchone()[0]

    def revoke_other_sessions(self, session):
        if not session or not session.get('user_id') or not session.get('token_hash'):
            return 0
        with self.transaction() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            cur = db.execute(
                'DELETE FROM sessions WHERE user_id=? AND token_hash<>?',
                (session['user_id'], session['token_hash']),
            )
            return cur.rowcount

    def record_setup_attempt(self, address):
        key = 'setup:' + str(address)
        with self.transaction() as db:
            db.execute('DELETE FROM attempts WHERE created<?', (time.time() - 900,))
            if (
                db.execute('SELECT COUNT(*) FROM attempts WHERE key=?', (key,)).fetchone()[0] >= 10
                or db.execute("SELECT COUNT(*) FROM attempts WHERE key='setup:*'").fetchone()[0] >= 30
            ):
                raise ValueError('Too many setup attempts. Try again in 15 minutes.')
            now = time.time()
            db.execute('INSERT INTO attempts VALUES(?,?)', (key, now))
            db.execute('INSERT INTO attempts VALUES(?,?)', ('setup:*', now))

    def clear_setup_attempts(self, address):
        with self.transaction() as db:
            db.execute("DELETE FROM attempts WHERE key=? OR key='setup:*'", ('setup:' + str(address),))

    def login(self,username,password,address):
        username=str(username).strip().lower();key=address+':'+username
        with self.transaction() as db:
            db.execute('DELETE FROM attempts WHERE created<?',(time.time()-900,))
            if db.execute('SELECT COUNT(*) FROM attempts WHERE key=?',(key,)).fetchone()[0]>=5 or db.execute('SELECT COUNT(*) FROM attempts WHERE key=?',(address,)).fetchone()[0]>=30:
                raise ValueError('Too many attempts. Try again in 15 minutes.')
            db.execute('INSERT INTO attempts VALUES(?,?)',(key,time.time()))
            db.execute('INSERT INTO attempts VALUES(?,?)',(address,time.time()))
            user=db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        if len(password.encode())>1024:
            raise ValueError('Invalid username or password.')
        salt=user['salt'] if user else '00'*16
        hashed=password_hash(password,salt)
        if not user or not secrets.compare_digest(hashed,user['password_hash']):
            raise ValueError('Invalid username or password.')
        with self.transaction() as db:db.execute('DELETE FROM attempts WHERE key=?',(key,))
        return user['id']
