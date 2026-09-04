"""领域数据模型（dataclass）。表结构见 db.py；此处只管 API 层数据形状。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class SearchQuery:
    """一次 BFF 搜索请求（从画像/关键词生成的 query 计划节点）。"""
    label: str                    # company / title / tech
    keyword: str                  # 本次搜索关键词（可为空=纯筛选）
    filters: dict = field(default_factory=dict)
    page: int = 1
    page_size: int = 20

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchResult:
    """一次搜索的解析结果。"""
    total: int = 0
    raw: list = field(default_factory=list)          # 原始卡片
    candidates: list = field(default_factory=list)   # 规范化卡片 dict
    error: str | None = None
    risk: bool = False
    cors_blocked: bool = False


@dataclass
class Candidate:
    """猎聘卡片/简历的规范化表示（最小必要字段，绝不含手机/邮箱）。"""
    resume_id: str
    name: str = ""
    user_id: str = ""
    im_id: str = ""
    resume_url: str = ""
    desired_title: str = ""
    desired_salary: str = ""
    current_city: str = ""
    work_years: str = ""
    edu: str = ""
    current_company: str = ""
    school: str = ""
    age: int | None = None
    active_status: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Invite:
    """站内邀请/打招呼。job_id 为猎聘岗位 ejobId（邀请投递目标）。"""
    candidate_id: int
    job_id: str
    status: str = "pending"   # pending / sent / failed
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class JobProfile:
    """人才画像（结构化 JSON，用户上传 PDF 解析后编辑保存）。"""
    id: int | None = None
    name: str = ""
    version: str = "1"
    is_default: bool = False
    profile: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
