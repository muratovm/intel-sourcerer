"""Password accounts and revocable sessions for the local application."""
from pathlib import Path
from contextlib import contextmanager, closing
import hashlib
import secrets
import sqlite3
import time
import uuid
import re
import shutil
from http.cookies import SimpleCookie
from .storage import connect_database


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=32768,r=8,p=3,maxmem=64*1024*1024,dklen=32).hex()


class Accounts:
    def __init__(self,database,config):
        self.legacy=Path(database).resolve()
        self.original_config=Path(config).resolve()
        self.path=self.legacy.parent/'accounts.sqlite3'
        with self.transaction() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,salt TEXT NOT NULL,password_hash TEXT NOT NULL,admin INTEGER NOT NULL,database_path TEXT NOT NULL,config_path TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT,csrf TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(key TEXT NOT NULL,created REAL NOT NULL);''')

    def connect(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;return db

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

    def session(self,cookie):
        try:
            parsed=SimpleCookie();parsed.load(cookie or '')
            token=parsed['intel_session'].value
        except Exception:return None
        with self.transaction() as db:
            row=db.execute('SELECT s.*,u.username,u.admin,u.database_path,u.config_path FROM sessions s LEFT JOIN users u ON u.id=s.user_id WHERE token_hash=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
            return dict(row) if row else None

    def new_session(self,user_id=None):
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        with self.transaction() as db:
            db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user_id,csrf,time.time()+8*3600 if user_id else time.time()+1800))
        return token,csrf

    def revoke(self,session):
        if session:
            with self.transaction() as db:db.execute('DELETE FROM sessions WHERE token_hash=?',(session['token_hash'],))

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
        with self.transaction() as db:db.execute('DELETE FROM attempts WHERE key=? OR key=?',(key,address))
        return user['id']
