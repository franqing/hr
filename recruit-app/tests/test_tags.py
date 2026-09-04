"""标签（固定名单快照）：CRUD、撞名、级联删除、幂等追加、成员形状、跨 run 邀请过滤。"""
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


def _cand(rid, resume_id, **over):
    return db.upsert_candidate(rid, card(resume_id, **over))[0]


def test_tag_crud_rename_and_delete():
    tid = db.create_tag("A 组")
    assert db.get_tag(tid)["name"] == "A 组"
    rows = db.list_tags()
    assert any(t["id"] == tid and t["member_count"] == 0 for t in rows)

    db.rename_tag(tid, "A 组-改名")
    assert db.get_tag(tid)["name"] == "A 组-改名"

    assert db.delete_tag(tid) is True
    assert db.delete_tag(tid) is False        # 已删再删返回 False
    assert db.get_tag(tid) is None


def test_create_tag_duplicate_raises():
    db.create_tag("重名")
    try:
        db.create_tag("重名")
        raise AssertionError("应抛 ValueError")
    except ValueError as e:
        assert "标签已存在" in str(e)


def test_rename_tag_duplicate_and_missing():
    db.create_tag("甲")
    db.create_tag("乙")
    a = db.list_tags()
    # 撞名
    try:
        db.rename_tag([t["id"] for t in a if t["name"] == "甲"][0], "乙")
        raise AssertionError("撞名应抛 ValueError")
    except ValueError as e:
        assert "标签已存在" in str(e)
    # 不存在
    try:
        db.rename_tag(99999, "丙")
        raise AssertionError("不存在应抛 ValueError")
    except ValueError as e:
        assert "标签不存在" in str(e)


def test_add_members_idempotent_added_skipped():
    tid = db.create_tag("攒人")
    rid = _run()
    a = _cand(rid, "A1", name="甲")
    b = _cand(rid, "A2", name="乙")
    rid2 = _run()
    c = _cand(rid2, "A3", name="丙")          # 跨 run 的候选同样可入标签

    r1 = db.add_tag_members(tid, [a, b, a, c])   # 列表内重复也幂等
    assert r1 == {"added": 3, "skipped": 1}
    r2 = db.add_tag_members(tid, [a, b])          # 全都在 → 全跳过
    assert r2 == {"added": 0, "skipped": 2}
    r3 = db.add_tag_members(tid, [a, c])
    assert r3 == {"added": 0, "skipped": 2}

    rows = db.list_tags()
    assert [t["member_count"] for t in rows if t["id"] == tid][0] == 3


def test_delete_tag_cascades_members():
    tid = db.create_tag("将删")
    rid = _run()
    cid = _cand(rid, "C1")
    db.add_tag_members(tid, [cid])
    db.delete_tag(tid)
    assert db.list_tag_candidates(tid) == []    # 成员随标签级联删除


def test_tag_members_shape_cross_run_with_invited():
    tid = db.create_tag("面试组")
    rid = _run()
    invited = _cand(rid, "M1", name="已邀")
    rid2 = _run()
    fresh = _cand(rid2, "M2", name="新来")       # 别的 run 的候选，加入同一标签
    db.add_tag_members(tid, [invited, fresh])
    iid, _ = db.create_invite(invited, rid, "J-GROUP")

    rows = db.list_tag_candidates(tid)
    assert len(rows) == 2
    by_id = {r["id"]: r for r in rows}
    assert by_id[invited]["invited"] == 1       # 有邀请记录 → invited=1（SQLite EXISTS 返回 0/1）
    assert by_id[fresh]["invited"] == 0
    assert by_id[invited]["name"] == "已邀"     # 完整候选行字段
    assert by_id[invited]["work_years"] == "12年"
    scored = db.attach_scores(rows)             # 与 run 候选行一致可接打分
    assert scored[0]["score"] is None
    assert db.get_invite(iid)["job_id"] == "J-GROUP"


def test_list_invites_tag_filter_cross_run():
    tid = db.create_tag("邀请名单")
    rid = _run()
    inside1 = _cand(rid, "T1", name="组内1")
    rid2 = _run()
    inside2 = _cand(rid2, "T2", name="组内2")
    outside = _cand(rid, "T3", name="组外")
    db.add_tag_members(tid, [inside1, inside2])

    db.create_invite(inside1, rid, "J1")
    db.create_invite(inside2, 0, "J2")          # run_id=0：标签模式「无 run」哨兵
    db.create_invite(outside, rid, "J3")

    got = db.list_invites(tag_id=tid)           # 只含标签内成员的邀请，跨 run
    assert [g["candidate_name"] for g in got] == ["组内2", "组内1"]  # id DESC
    all_inv = db.list_invites()
    assert len(all_inv) == 3                     # 全局不受影响
    by_run = db.list_invites(rid)
    assert {g["candidate_name"] for g in by_run} == {"组内1", "组外"}
