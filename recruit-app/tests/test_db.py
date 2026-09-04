"""db：upsert 去重、gate 更新、评分存取、invite 幂等与状态。"""
from backend import db


def card(resume_id="R1", **over):
    c = {
        "resume_id": resume_id, "name": "张三", "desired_title": "研发总监",
        "desired_salary": "60-80K", "current_city": "厦门", "work_years": "12年",
        "edu": "硕士", "current_company": "三安光电", "school": "厦门大学",
        "age": 40, "active_status": "在职", "resume_url": "https://lpt.liepin.com/cv/x",
    }
    c.update(over)
    return c


def _run():
    return db.create_search_run(0, "manual")


def test_upsert_candidate_dedup():
    rid = _run()
    cid1, new1 = db.upsert_candidate(rid, card("R1"))
    assert new1 is True
    rid2 = _run()
    cid2, new2 = db.upsert_candidate(rid2, card("R1"))
    assert cid1 == cid2
    assert new2 is False                     # 跨轮次按 resume_id 去重
    rows = db.list_run_candidates(rid2)
    assert len(rows) == 1
    assert rows[0]["name"] == "张三"


def test_upsert_candidate_missing_resume_id_raises():
    rid = _run()
    try:
        db.upsert_candidate(rid, {"name": "无 resume_id"})
        raise AssertionError("应抛 ValueError")
    except ValueError:
        pass


def test_update_candidate_gate():
    rid = _run()
    cid, _ = db.upsert_candidate(rid, card("R2"))
    db.update_candidate_gate(cid, 1, "期望职位命中岗位词", 1, "命中目标公司且含项目主导")
    c = db.get_candidate(cid)
    assert c["quick_gate"] == 1
    assert c["hard_gate"] == 1
    assert "项目主导" in c["hard_reason"]


def test_list_gated_candidates_filters():
    rid = _run()
    cid_ok, _ = db.upsert_candidate(rid, card("R3"))
    cid_no, _ = db.upsert_candidate(rid, card("R4"))
    db.update_candidate_gate(cid_ok, 1, "", 1, "")
    db.update_candidate_gate(cid_no, 1, "", 0, "")
    gated = db.list_gated_candidates(rid)
    assert cid_ok in gated and cid_no not in gated


def test_save_and_get_score():
    rid = _run()
    cid, _ = db.upsert_candidate(rid, card("R5"))
    db.save_score(cid, "rule", {"专业能力": 20}, 85.5, "rule", {"附加分": "+2"})
    s = db.get_score(cid)
    assert s["method"] == "rule"
    assert s["total"] == 85.5
    assert s["dims"]["专业能力"] == 20
    db.save_score(cid, "llm", {"专业能力": 18}, 90.0, "deepseek-chat", {})  # 覆盖
    s = db.get_score(cid)
    assert s["method"] == "llm"
    assert s["total"] == 90.0


def test_attach_scores():
    rid = _run()
    c1, _ = db.upsert_candidate(rid, card("R6"))
    c2, _ = db.upsert_candidate(rid, card("R7"))
    db.save_score(c1, "rule", {}, 70.0, "", {})
    cands = db.attach_scores(db.list_run_candidates(rid))
    by_id = {c["id"]: c["score"] for c in cands}
    assert by_id[c1]["total"] == 70.0
    assert by_id[c2] is None


def test_create_invite_idempotent():
    rid = _run()
    cid, _ = db.upsert_candidate(rid, card("R8"))
    iid1, created1 = db.create_invite(cid, rid, "J1")
    iid2, created2 = db.create_invite(cid, rid, "J1")
    assert iid1 == iid2
    assert created1 is True and created2 is False
    assert db.get_invite(iid1)["status"] == "pending"


def test_invite_status_sent_sets_sent_at():
    rid = _run()
    cid, _ = db.upsert_candidate(rid, card("R9"))
    iid, _ = db.create_invite(cid, rid, "J2")
    db.set_invite_status(iid, "sent")
    inv = db.get_invite(iid)
    assert inv["status"] == "sent"
    assert inv["sent_at"] is not None
    assert inv["error"] is None


def test_invite_status_failed_clears_sent_at():
    rid = _run()
    cid, _ = db.upsert_candidate(rid, card("R10"))
    iid, _ = db.create_invite(cid, rid, "J3")
    db.set_invite_status(iid, "sent")
    db.set_invite_status(iid, "failed", "Cookie 失效")
    inv = db.get_invite(iid)
    assert inv["status"] == "failed"
    assert inv["sent_at"] is None                # failed 不保留发送时间
    assert inv["error"] == "Cookie 失效"


def test_list_invites_joins_candidate_name():
    rid = _run()
    cid, _ = db.upsert_candidate(rid, card("R11"))
    db.create_invite(cid, rid, "J4")
    invs = db.list_invites(rid)
    assert invs[0]["candidate_name"] == "张三"
