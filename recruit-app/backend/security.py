"""密钥与脱敏工具。密钥文件 data/secret.key 机器绑定，不提交 git。"""
import re
from pathlib import Path
from cryptography.fernet import Fernet

SECRET_FIELD_RE = re.compile(r"电话|手机|邮箱|联系方式|微信|身份证")
_KEY_PATH = Path(__file__).resolve().parent.parent / "data" / "secret.key"


def get_fernet() -> Fernet:
    if not _KEY_PATH.exists():
        _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _KEY_PATH.write_bytes(Fernet.generate_key())
        _KEY_PATH.chmod(0o600)
    return Fernet(_KEY_PATH.read_bytes())


def encrypt_str(s: str) -> str:
    return get_fernet().encrypt(s.encode("utf-8")).decode("utf-8")


def decrypt_str(s: str) -> str:
    return get_fernet().decrypt(s.encode("utf-8")).decode("utf-8")
