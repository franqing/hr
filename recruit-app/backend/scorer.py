"""M5：LLM 五维评分。对 run 内过 hard_gate 的候选逐条打分，LLM 不可用降级规则分。

只把卡片可读字段 + 简历文本发给 LLM（均已在源头脱敏，不含手机/邮箱）。
"""
from __future__ import annotations

import json
import logging

from . import db
from . import matcher
from .llm_client import LLMUnavailable, chat_json

log = logging.getLogger("recruit.scorer")

_DIMS = ["专业能力", "技术领导力", "经营思维", "资源整合", "岗位契合度"]


def _card_brief(cand: dict) -> str:
    """候选卡片 → 供 LLM 阅读的简短文本。"""
    card = cand.get("card") or {}
    rows = [
        ("姓名", card.get("name") or cand.get("name")),
        ("期望职位", card.get("desired_title")),
        ("当前公司", card.get("current_company")),
        ("期望薪资", card.get("desired_salary")),
        ("现居地", card.get("current_city")),
        ("工作年限", card.get("work_years")),
        ("学历", card.get("edu")),
        ("学校", card.get("school")),
        ("年龄", card.get("age")),
    ]
    return "\n".join(f"{k}：{v}" for k, v in rows if v not in (None, ""))


def _resume_excerpt(cand: dict, max_chars: int = 3000) -> str:
    resume = cand.get("resume") or {}
    if isinstance(resume, dict):
        text = resume.get("text") or ""
    else:
        text = str(resume)
    return (text or "")[:max_chars]


def _llm_score(profile: dict | None, cand: dict) -> dict:
    prof_txt = json.dumps(profile, ensure_ascii=False) if profile else "（使用默认画像）"
    prompt = (
        "请根据岗位画像与候选人信息，对候选人进行五维评分，每维 0-20 分整数。\n\n"
        f"【岗位画像】\n{prof_txt}\n\n"
        f"【候选人卡片】\n{_card_brief(cand)}\n\n"
        f"【简历摘要】\n{_resume_excerpt(cand)}\n\n"
        "严格输出 JSON：{\"专业能力\":0,\"技术领导力\":0,\"经营思维\":0,\"资源整合\":0,"
        "\"岗位契合度\":0,\"理由\":\"一句话\"}\n"
        "评分依据：专业能力看芯片设计/流片/器件/功率半导体深度；技术领导力看带团队/管理/"
        "项目主导；经营思维看成本/风险/ROI；资源整合看供应链/客户/外部对接；"
        "岗位契合度看厦门意愿/稳定性/价值观。"
    )
    out = chat_json(prompt, system="你是招聘专家，为候选人五维打分，只输出合法 JSON。")
    dims = {}
    for d in _DIMS:
        v = out.get(d)
        try:
            dims[d] = max(0, min(20, int(float(v))))
        except (TypeError, ValueError):
            dims[d] = 0
    weights = (profile or {}).get("五维权重") or {}
    total = min(100.0, round(5 * sum(int(weights.get(d, 0) or 0) * dims[d]
                                      for d in _DIMS) / 100.0, 1))
    reason = str(out.get("理由") or "").strip()
    return {"dims": dims, "total": total, "bonus": 0,
            "source": f"LLM 深度评分", "explanation": {"LLM 理由": reason}}


def score_run(run_id: int) -> dict:
    """对 run 内过 hard_gate 的候选逐条评分。返回统计。"""
    run = db.get_run(run_id)
    if not run:
        raise ValueError(f"运行不存在: {run_id}")
    sp = db.get_search_profile(run.get("search_profile_id")) if run.get("search_profile_id") else None
    jp = db.get_job_profile(sp["job_profile_id"]) if sp and sp.get("job_profile_id") else None
    profile = (jp or {}).get("profile") if isinstance(jp, dict) else None

    cands = [db.attach_scores([db.get_candidate(i)])[0]
             for i in db.list_gated_candidates(run_id)]
    if not cands:
        return {"run_id": run_id, "scored": 0, "llm": 0, "fallback": 0,
                "message": "该 run 暂无通过硬性门槛的候选"}

    scored = llm_ok = fallback = 0
    for cand in cands:
        try:
            res = _llm_score(profile, cand)
            db.save_score(cand["id"], "llm", res["dims"], res["total"], res["source"],
                          res["explanation"])
            llm_ok += 1
        except LLMUnavailable as e:
            sc = matcher.rule_score(profile, cand.get("card") or {}, cand.get("resume") or None)
            db.save_score(cand["id"], "rule", sc["dims"], sc["total"],
                          f"规则兜底（LLM 不可用：{e}）", sc["explanation"])
            fallback += 1
        scored += 1
    return {"run_id": run_id, "scored": scored, "llm": llm_ok, "fallback": fallback}
