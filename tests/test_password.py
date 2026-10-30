import importlib.util
from pathlib import Path
from robo.security import Auth


def test_password_rotation_revokes_bootstrap_and_stores_only_hash(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "password_rotation", Path(__file__).parents[1] / "scripts/change-password.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    Auth(tmp_path)
    previous = (tmp_path / "initial-password.txt").read_text().strip()
    new = "synthetic-test-password-only"
    module.rotate(tmp_path, new)
    auth = Auth(tmp_path)
    assert auth.login("admin", previous) is None
    assert auth.login("admin", new) is not None
    assert not (tmp_path / "initial-password.txt").exists()
    assert new not in (tmp_path / "auth.json").read_text()
    assert (tmp_path / "auth.json").stat().st_mode & 0o777 == 0o600
