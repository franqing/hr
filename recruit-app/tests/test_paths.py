"""数据路径中心化（spec §4.2）回归：env 覆盖后各消费者落点跟随；绝不写真实 data/。"""
from __future__ import annotations

from pathlib import Path

from backend.paths import data_dir, program_root
from backend import db, security
from backend.liepin import adapter


def test_program_root_is_repo_root():
    # 程序根 = backend/ 的上一级（开发机即仓库根）
    assert program_root() == Path(db.__file__).resolve().parent.parent


def test_data_dir_default_is_program_root_data(monkeypatch):
    # conftest 的 autouse 夹具设了 RECRUIT_DATA_DIR——先删除，验证「未设 env → 程序根/data」
    monkeypatch.delenv("RECRUIT_DATA_DIR", raising=False)
    assert data_dir() == program_root() / "data"


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "d"))
    assert data_dir() == tmp_path / "d"


def test_db_file_lands_under_env_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "d"))
    conn = db.get_conn()
    conn.close()
    assert (tmp_path / "d" / "app.db").is_file()


def test_fernet_key_lands_under_env_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "d"))
    key = security.get_fernet()
    assert key.encrypt(b"x") != b"x"
    assert (tmp_path / "d" / "secret.key").is_file()


def test_upload_dir_under_env_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "d"))
    from backend import profile_parser
    assert profile_parser.upload_dir() == tmp_path / "d" / "uploads"


def test_user_data_dir_under_env_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("RECRUIT_DATA_DIR", str(tmp_path / "d"))
    assert adapter.user_data_dir() == tmp_path / "d" / "browser_profile"
