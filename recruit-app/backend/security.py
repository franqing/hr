"""密钥与脱敏工具。密钥文件 data/secret.key 机器绑定，不提交 git。"""
import re

from cryptography.fernet import Fernet

from .paths import data_dir

SECRET_FIELD_RE = re.compile(r"电话|手机|邮箱|联系方式|微信|身份证")


def get_fernet() -> Fernet:
    """取（必要时生成）本机 Fernet 密钥。密钥只在 data_dir 下，每机独立。"""
    key_path = data_dir() / "secret.key"
    if not key_path.exists():
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(Fernet.generate_key())
        try:
            key_path.chmod(0o600)
        except OSError:  # noqa: BLE001 —— Windows 无 POSIX 权限位，忽略
            pass
    return Fernet(key_path.read_bytes())


def encrypt_str(s: str) -> str:
    return get_fernet().encrypt(s.encode("utf-8")).decode("utf-8")


def decrypt_str(s: str) -> str:
    return get_fernet().decrypt(s.encode("utf-8")).decode("utf-8")
