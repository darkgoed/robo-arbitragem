import hashlib
import hmac
import json
import os
import secrets
import time
from collections import OrderedDict, deque
from pathlib import Path


class Auth:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        path = directory / "auth.json"
        if not path.exists():
            password = secrets.token_urlsafe(24)
            salt = secrets.token_hex(16)
            record = {"username": "admin", "salt": salt, "hash": self.derive(password, salt)}
            for target, value in [
                (path, json.dumps(record)),
                (directory / "initial-password.txt", password + "\n"),
            ]:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(value)
        self.record = json.loads(path.read_text())
        self.sessions = OrderedDict()
        self.rates = OrderedDict()

    @staticmethod
    def derive(password, salt):
        return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310000).hex()

    def permit(self, key, max_count, seconds):
        now = time.monotonic()
        q = self.rates.setdefault(key, deque())
        self.rates.move_to_end(key)
        while q and q[0] <= now - seconds:
            q.popleft()
        if len(self.rates) > 512:
            self.rates.popitem(last=False)
        if len(q) >= max_count:
            return False
        q.append(now)
        return True

    def login(self, username, password):
        valid = hmac.compare_digest(self.derive(password, self.record["salt"]), self.record["hash"])
        if not hmac.compare_digest(username, self.record["username"]) or not valid:
            return None
        token = secrets.token_urlsafe(32)
        session = {"csrf": secrets.token_urlsafe(32), "expires": time.monotonic() + 8 * 3600}
        self.sessions[hashlib.sha256(token.encode()).hexdigest()] = session
        while len(self.sessions) > 32:
            self.sessions.popitem(last=False)
        return token, session

    def session(self, token):
        key = hashlib.sha256((token or "").encode()).hexdigest()
        session = self.sessions.get(key)
        if session and session["expires"] > time.monotonic():
            return session
        self.sessions.pop(key, None)
        return None

    def logout(self, token):
        self.sessions.pop(hashlib.sha256((token or "").encode()).hexdigest(), None)
