#!/usr/bin/env python3
"""Private video library: dependency-free HTTP API and static frontend."""
import hashlib, hmac, http.cookies, json, mimetypes, os, secrets, sqlite3, time, uuid
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('STREAMFLIX_DATA', ROOT / 'data')).resolve()
MEDIA = DATA / 'media'
DB = DATA / 'streamflix.sqlite3'
HOST, PORT = os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '8000'))
SUPPORTED_VIDEO_EXTS = {'.mp4', '.m4v', '.mkv', '.webm', '.mov', '.avi', '.wmv', '.flv', '.mpg', '.mpeg', '.ogv', '.ogg', '.3gp', '.ts', '.mts', '.m2ts', '.vob', '.asf', '.rm', '.rmvb', '.divx'}
for ext, mime in {'.mkv':'video/x-matroska','.avi':'video/x-msvideo','.wmv':'video/x-ms-wmv','.flv':'video/x-flv','.m4v':'video/x-m4v','.ogv':'video/ogg','.3gp':'video/3gpp','.ts':'video/mp2t','.mts':'video/mp2t','.m2ts':'video/mp2t','.vob':'video/mpeg','.asf':'video/x-ms-asf','.rm':'video/vnd.rn-realvideo','.rmvb':'video/vnd.rn-realvideo','.divx':'video/x-divx'}.items():
    mimetypes.add_type(mime, ext)
SECRET = os.environ.get('SESSION_SECRET', '')
if not SECRET:
    SECRET = (DATA / '.secret').read_text() if (DATA / '.secret').exists() else secrets.token_hex(32)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / '.secret').write_text(SECRET)
    os.chmod(DATA / '.secret', 0o600)
DATA.mkdir(parents=True, exist_ok=True); MEDIA.mkdir(parents=True, exist_ok=True)

def connect():
    c = sqlite3.connect(DB, timeout=30); c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA journal_mode=WAL'); return c

def init():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,salt TEXT NOT NULL,created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE,expires_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS series(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE,title TEXT NOT NULL,description TEXT DEFAULT '',category TEXT DEFAULT '',cover TEXT,created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS episodes(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE,series_id INTEGER REFERENCES series ON DELETE SET NULL,title TEXT NOT NULL,description TEXT DEFAULT '',filename TEXT NOT NULL UNIQUE,size INTEGER NOT NULL,duration REAL DEFAULT 0,created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS flags(user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE,episode_id INTEGER NOT NULL REFERENCES episodes ON DELETE CASCADE,kind TEXT NOT NULL CHECK(kind IN ('favorite','watchlist')),PRIMARY KEY(user_id,episode_id,kind));
        CREATE TABLE IF NOT EXISTS progress(user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE,episode_id INTEGER NOT NULL REFERENCES episodes ON DELETE CASCADE,position REAL NOT NULL DEFAULT 0,duration REAL NOT NULL DEFAULT 0,updated_at INTEGER NOT NULL,completed INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,episode_id));
        CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE,name TEXT NOT NULL,size INTEGER NOT NULL,received INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,series_id INTEGER REFERENCES series ON DELETE SET NULL,episode_title TEXT NOT NULL,created_at INTEGER NOT NULL);
        ''')

def pw_hash(password, salt=None):
    if salt and salt.startswith('pbkdf2$'):
        raw_salt = bytes.fromhex(salt.split('$', 1)[1])
        return hashlib.pbkdf2_hmac('sha256', password.encode(), raw_salt, 600_000).hex(), salt
    if hasattr(hashlib, 'scrypt'):
        salt = salt or 'scrypt$' + secrets.token_hex(16)
        raw_salt = bytes.fromhex(salt.split('$', 1)[-1])
        return hashlib.scrypt(password.encode(), salt=raw_salt, n=2**14, r=8, p=1).hex(), salt
    salt = salt or 'pbkdf2$' + secrets.token_hex(16)
    raw_salt = bytes.fromhex(salt.split('$', 1)[-1])
    return hashlib.pbkdf2_hmac('sha256', password.encode(), raw_salt, 600_000).hex(), salt

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(ROOT),**kw)
    def log_message(self,*a): pass
    def send_json(self, obj, status=200):
        b=json.dumps(obj).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(b)
    def body(self, limit=2_000_000):
        n=int(self.headers.get('Content-Length','0'))
        if n>limit: raise ValueError('Request too large')
        return json.loads(self.rfile.read(n) or b'{}')
    def user(self):
        raw=http.cookies.SimpleCookie(self.headers.get('Cookie','')).get('sid')
        if not raw: return None
        with connect() as c:
            row=c.execute('SELECT user_id FROM sessions WHERE token_hash=? AND expires_at>?',(hashlib.sha256(raw.value.encode()).hexdigest(),int(time.time()))).fetchone()
            return row['user_id'] if row else None
    def require_user(self):
        uid=self.user()
        if uid is None: self.send_json({'error':'Bitte anmelden.'},401)
        return uid
    def cookie(self, token, clear=False):
        val='sid=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0' if clear else f'sid={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000'
        if os.environ.get('COOKIE_SECURE','0')=='1': val+='; Secure'
        self.send_header('Set-Cookie',val)
    def do_GET(self):
        p=urlparse(self.path); path=p.path
        try:
            if path=='/api/me':
                uid=self.user()
                if uid is None: return self.send_json({'user':None})
                with connect() as c: row=c.execute('SELECT id,email FROM users WHERE id=?',(uid,)).fetchone()
                return self.send_json({'user':dict(row)})
            if path=='/api/library': return self.library()
            if path.startswith('/api/video/'):
                uid=self.require_user()
                if uid is None:return
                eid=int(path.rsplit('/',1)[1])
                with connect() as c: ep=c.execute('SELECT filename,size FROM episodes WHERE id=? AND user_id=?',(eid,uid)).fetchone()
                if not ep:return self.send_json({'error':'Nicht gefunden'},404)
                return self.video(MEDIA/ep['filename'],ep['size'])
            if path.startswith('/api/cover/'):
                uid=self.require_user()
                if uid is None:return
                sid=int(path.rsplit('/',1)[1])
                with connect() as c: row=c.execute('SELECT cover FROM series WHERE id=? AND user_id=?',(sid,uid)).fetchone()
                if row and row['cover'] and (MEDIA/row['cover']).is_file(): return self.file(MEDIA/row['cover'])
                return self.send_json({'error':'Nicht gefunden'},404)
            if path.startswith('/api/uploads/'):
                uid=self.require_user()
                if uid is None:return
                upid=path.split('/')[3]
                with connect() as c: row=c.execute('SELECT * FROM uploads WHERE id=? AND user_id=?',(upid,uid)).fetchone()
                return self.send_json(dict(row) if row else {'error':'Upload nicht gefunden'},200 if row else 404)
            return super().do_GET()
        except Exception as e: self.send_json({'error':str(e)},400)
    def video(self, file, size):
        if not file.is_file(): return self.send_json({'error':'Videodatei fehlt'},404)
        r=self.headers.get('Range'); start,end=0,size-1; code=200
        if r:
            try:
                a,b=r.replace('bytes=','').split('-',1); start=int(a) if a else max(0,size-int(b)); end=int(b) if b else size-1
                if start> end or end>=size: raise ValueError()
                code=206
            except: self.send_response(416); self.send_header('Content-Range',f'bytes */{size}'); self.end_headers(); return
        self.send_response(code); self.send_header('Content-Type',mimetypes.guess_type(file.name)[0] or 'application/octet-stream'); self.send_header('Accept-Ranges','bytes'); self.send_header('Content-Length',str(end-start+1))
        if code==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.send_header('Cache-Control','private, no-store'); self.end_headers()
        with file.open('rb') as f:
            f.seek(start); remain=end-start+1
            while remain:
                data=f.read(min(1024*1024,remain))
                if not data:break
                try:self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError):break
                remain-=len(data)
    def file(self,name):
        file=MEDIA/name; mime=mimetypes.guess_type(name)[0] or 'application/octet-stream'; b=file.read_bytes()
        self.send_response(200); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def library(self):
        uid=self.require_user()
        if uid is None:return
        with connect() as c:
            series=[dict(r) for r in c.execute('SELECT s.*,COUNT(e.id) episode_count FROM series s LEFT JOIN episodes e ON e.series_id=s.id WHERE s.user_id=? GROUP BY s.id ORDER BY s.created_at DESC',(uid,))]
            eps=[]
            for r in c.execute('''SELECT e.*,s.title series_title,p.position,p.duration saved_duration,p.updated_at,p.completed,
             EXISTS(SELECT 1 FROM flags f WHERE f.user_id=e.user_id AND f.episode_id=e.id AND f.kind='favorite') favorite,
             EXISTS(SELECT 1 FROM flags f WHERE f.user_id=e.user_id AND f.episode_id=e.id AND f.kind='watchlist') watchlist
             FROM episodes e LEFT JOIN series s ON e.series_id=s.id LEFT JOIN progress p ON p.episode_id=e.id AND p.user_id=e.user_id WHERE e.user_id=? ORDER BY e.created_at DESC''',(uid,)):
                d=dict(r); d['video_url']=f"/api/video/{d['id']}"; eps.append(d)
            ups=[dict(r) for r in c.execute('SELECT * FROM uploads WHERE user_id=? AND status IN (\'uploading\',\'finalizing\')',(uid,))]
        self.send_json({'series':series,'episodes':eps,'uploads':ups})
    def do_POST(self):
        path=urlparse(self.path).path
        try:
            if path in ('/api/register','/api/login'):
                d=self.body(); email=str(d.get('email','')).strip().lower(); password=str(d.get('password',''))
                if len(email)>254 or '@' not in email or len(password)<10:return self.send_json({'error':'Gültige E-Mail und mindestens 10 Zeichen langes Passwort erforderlich.'},400)
                with connect() as c:
                    row=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
                    if path.endswith('register'):
                        if row:return self.send_json({'error':'Für diese E-Mail gibt es bereits ein Konto.'},409)
                        ph,salt=pw_hash(password); cur=c.execute('INSERT INTO users(email,password_hash,salt,created_at) VALUES(?,?,?,?)',(email,ph,salt,int(time.time()))); uid=cur.lastrowid
                    else:
                        if not row:return self.send_json({'error':'E-Mail oder Passwort stimmt nicht.'},401)
                        ph,_=pw_hash(password,row['salt'])
                        if not hmac.compare_digest(ph,row['password_hash']):return self.send_json({'error':'E-Mail oder Passwort stimmt nicht.'},401)
                        uid=row['id']
                    tok=secrets.token_urlsafe(32); c.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(tok.encode()).hexdigest(),uid,int(time.time())+2592000))
                self.send_response(200); self.cookie(tok); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(json.dumps({'user':{'id':uid,'email':email}}).encode()); return
            if path=='/api/logout':
                raw=http.cookies.SimpleCookie(self.headers.get('Cookie','')).get('sid')
                if raw:
                    with connect() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(raw.value.encode()).hexdigest(),))
                self.send_response(200); self.cookie('',True); self.end_headers(); return
            uid=self.require_user()
            if uid is None:return
            d=self.body()
            if path=='/api/series':
                title=str(d.get('title','')).strip()
                if not title:return self.send_json({'error':'Serientitel erforderlich'},400)
                with connect() as c: cur=c.execute('INSERT INTO series(user_id,title,description,category,created_at) VALUES(?,?,?,?,?)',(uid,title,d.get('description',''),d.get('category',''),int(time.time())))
                return self.send_json({'id':cur.lastrowid})
            if path.startswith('/api/episodes/') and path.endswith('/update'):
                eid=int(path.split('/')[-2]); title=str(d.get('title','')).strip(); sid=d.get('series_id') or None
                with connect() as c:
                    if sid and not c.execute('SELECT 1 FROM series WHERE id=? AND user_id=?',(sid,uid)).fetchone():return self.send_json({'error':'Serie nicht gefunden'},404)
                    c.execute('UPDATE episodes SET title=?,description=?,series_id=? WHERE id=? AND user_id=?',(title,d.get('description',''),sid,eid,uid))
                return self.send_json({'ok':True})
            if path.startswith('/api/episodes/') and path.endswith('/flag'):
                eid=int(path.split('/')[-2]); kind=d.get('kind'); enabled=bool(d.get('enabled'))
                if kind not in ('favorite','watchlist'):return self.send_json({'error':'Ungültige Markierung'},400)
                with connect() as c:
                    if not c.execute('SELECT 1 FROM episodes WHERE id=? AND user_id=?',(eid,uid)).fetchone():return self.send_json({'error':'Video nicht gefunden'},404)
                    if enabled:c.execute('INSERT OR IGNORE INTO flags VALUES(?,?,?)',(uid,eid,kind))
                    else:c.execute('DELETE FROM flags WHERE user_id=? AND episode_id=? AND kind=?',(uid,eid,kind))
                return self.send_json({'ok':True})
            if path.startswith('/api/episodes/') and path.endswith('/progress'):
                eid=int(path.split('/')[-2]); pos=max(0,float(d.get('position',0))); dur=max(0,float(d.get('duration',0))); completed=bool(d.get('completed')) or (dur>0 and pos/dur>.95)
                with connect() as c:
                    if not c.execute('SELECT 1 FROM episodes WHERE id=? AND user_id=?',(eid,uid)).fetchone():return self.send_json({'error':'Video nicht gefunden'},404)
                    c.execute('INSERT INTO progress VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,episode_id) DO UPDATE SET position=excluded.position,duration=excluded.duration,updated_at=excluded.updated_at,completed=excluded.completed',(uid,eid,pos,dur,int(time.time()),int(completed)))
                    c.execute('UPDATE episodes SET duration=? WHERE id=? AND duration=0',(dur,eid))
                return self.send_json({'ok':True})
            if path=='/api/uploads':
                name=os.path.basename(str(d.get('name','video.mp4'))); size=int(d.get('size',0)); sid=d.get('series_id') or None
                if Path(name).suffix.lower() not in SUPPORTED_VIDEO_EXTS or size<1:return self.send_json({'error':'Bitte eine unterstützte Videodatei auswählen.'},400)
                if size>50*1024**3:return self.send_json({'error':'Maximale Dateigröße ist 50 GB.'},413)
                with connect() as c:
                    if sid and not c.execute('SELECT 1 FROM series WHERE id=? AND user_id=?',(sid,uid)).fetchone():return self.send_json({'error':'Serie nicht gefunden'},404)
                    upid=uuid.uuid4().hex; c.execute('INSERT INTO uploads(id,user_id,name,size,status,series_id,episode_title,created_at) VALUES(?,?,?,?,?,?,?,?)',(upid,uid,name,size,'uploading',sid,str(d.get('title') or Path(name).stem),int(time.time())))
                (DATA/(upid+'.part')).touch(); return self.send_json({'id':upid,'received':0},201)
            if path.startswith('/api/uploads/') and path.endswith('/complete'):
                upid=path.split('/')[-2]
                with connect() as c:
                    up=c.execute('SELECT * FROM uploads WHERE id=? AND user_id=?',(upid,uid)).fetchone()
                    if not up:return self.send_json({'error':'Upload nicht gefunden'},404)
                    if up['received']!=up['size']:return self.send_json({'error':'Upload noch unvollständig'},409)
                    suffix=Path(up['name']).suffix.lower(); filename=uuid.uuid4().hex+suffix; os.replace(DATA/(upid+'.part'),MEDIA/filename)
                    cur=c.execute('INSERT INTO episodes(user_id,series_id,title,filename,size,created_at) VALUES(?,?,?,?,?,?)',(uid,up['series_id'],up['episode_title'],filename,up['size'],int(time.time())))
                    c.execute('DELETE FROM uploads WHERE id=?',(upid,))
                return self.send_json({'id':cur.lastrowid},201)
            return self.send_json({'error':'Unbekannte Route'},404)
        except Exception as e:self.send_json({'error':str(e)},400)
    def do_PATCH(self):
        path=urlparse(self.path).path
        if not path.startswith('/api/uploads/') or not path.endswith('/chunk'):return self.send_json({'error':'Unbekannte Route'},404)
        uid=self.require_user()
        if uid is None:return
        upid=path.split('/')[-2]
        try:
            offset=int(self.headers.get('Upload-Offset','-1')); length=int(self.headers.get('Content-Length','0'))
            with connect() as c: up=c.execute('SELECT * FROM uploads WHERE id=? AND user_id=?',(upid,uid)).fetchone()
            if not up:return self.send_json({'error':'Upload nicht gefunden'},404)
            if offset!=up['received'] or length>8*1024*1024 or offset+length>up['size']:
                # Drain a bounded request body before replying so HTTP clients
                # receive the 409 instead of a broken pipe on stale offsets.
                if 0<=length<=8*1024*1024:self.rfile.read(length)
                return self.send_json({'error':'Offset oder Chunkgröße ungültig','received':up['received']},409)
            data=self.rfile.read(length)
            with (DATA/(upid+'.part')).open('ab') as f:f.write(data); f.flush(); os.fsync(f.fileno())
            with connect() as c:c.execute('UPDATE uploads SET received=? WHERE id=? AND user_id=?',(offset+len(data),upid,uid))
            return self.send_json({'received':offset+len(data)})
        except Exception as e:return self.send_json({'error':str(e)},400)
    def do_DELETE(self):
        uid=self.require_user()
        if uid is None:return
        path=urlparse(self.path).path; upid=path.split('/')[-1]
        with connect() as c:c.execute('DELETE FROM uploads WHERE id=? AND user_id=?',(upid,uid))
        (DATA/(upid+'.part')).unlink(missing_ok=True); self.send_json({'ok':True})

if __name__=='__main__':
    init(); print(f'StreamFlix läuft: http://{HOST}:{PORT}'); ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
