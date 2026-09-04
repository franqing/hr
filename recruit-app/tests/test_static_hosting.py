"""静态托管 /api 重写 / platform_native（spec §4.3）：确定性断言，不起真实服务。"""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from backend.main import _StripApiPrefix, _find_web_dir


def test_find_web_dir_prefers_env(tmp_path, monkeypatch):
    env_web = tmp_path / "env-web"
    env_web.mkdir()
    (env_web / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setenv("RECRUIT_WEB_DIR", str(env_web))
    assert _find_web_dir() == env_web


def test_find_web_dir_falls_back_to_dist_then_web(tmp_path, monkeypatch):
    monkeypatch.delenv("RECRUIT_WEB_DIR", raising=False)
    for sub in ("frontend/dist", "web"):
        (tmp_path / sub).mkdir(parents=True)
        (tmp_path / sub / "index.html").write_text("x", encoding="utf-8")
    assert _find_web_dir(tmp_path) == tmp_path / "frontend" / "dist"
    (tmp_path / "frontend" / "dist").rename(tmp_path / "frontend" / "dist-gone")
    assert _find_web_dir(tmp_path) == tmp_path / "web"


def test_find_web_dir_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("RECRUIT_WEB_DIR", raising=False)
    assert _find_web_dir(tmp_path) is None


def _mini_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["*"])
    app.add_middleware(_StripApiPrefix)

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


def test_api_prefix_rewritten_to_route():
    c = TestClient(_mini_app())
    assert c.get("/api/health").json() == {"ok": True}   # /api 前缀被剥 → 命中 /health
    assert c.get("/health").json() == {"ok": True}       # 无前缀原样可通


def test_get_settings_exposes_platform_native():
    import backend.main as main_mod
    c = TestClient(main_mod.app)
    body = c.get("/settings").json()
    assert body["platform_native"] is (os.name == "nt")
