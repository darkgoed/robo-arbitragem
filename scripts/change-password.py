"""Rotate this application's password, never exchange credentials."""

import getpass
import json
import os
import secrets
import subprocess
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robo.security import Auth


def rotate(directory, password):
    if not 12 <= len(password) <= 200:
        raise ValueError("Password must contain 12–200 characters")
    directory = Path(directory)
    record = json.loads((directory / "auth.json").read_text())
    record["salt"] = secrets.token_hex(16)
    record["hash"] = Auth.derive(password, record["salt"])
    temporary = directory / (".auth-" + secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(record))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, directory / "auth.json")
    (directory / "initial-password.txt").unlink(missing_ok=True)


if __name__ == "__main__":
    password = getpass.getpass("Nova senha (12–200 caracteres): ")
    if password != getpass.getpass("Confirmar nova senha: "):
        raise SystemExit("Senhas diferentes; nada alterado")
    rotate(Path(__file__).resolve().parents[1] / "secrets", password)
    subprocess.run(
        ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "restart", "robo-arbitrage.service"], check=True
    )  # noqa: S603
    print("Senha atualizada, bootstrap removido, sessões revogadas; segredo não exibido.")
