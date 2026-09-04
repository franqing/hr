# 候选筛选 + 标签名单 + 批量邀请(2026-09-03)

## 目标

智能搜索页(run 候选表格)增加可组合筛选;筛选结果可存成**固定名单标签**(跨 run 攒人);标签抽屉管理成员;批量邀请页可在「按 run」与「按标签」两种来源间切换,选标签后跨 run 展示成员并照常发起批量沟通。

## 已确认决策(用户问答)

1. 标签本质 = **固定名单快照**:保存后不随条件变化;通过「另存为新标签」「追加进已有标签」跨 run 攒人。
2. 邀请页入口 = **来源二选一**:保留现有 run 模式,并排新增「按标签」模式;标签模式展示标签全部成员,**不卡 hard_gate**、不分 run。
3. 筛选维度 = **画像字段 + AI 评分 + 候选状态**;不含关键字/薪资文本解析。
4. 标签管理 = 搜索页**抽屉**:新建/重命名/删除/看成员/移除单人;邀请页只选标签不编辑。

## 数据模型(backend/db.py)

新增两张表(沿用 `_SCHEMA` 的 `CREATE TABLE IF NOT EXISTS`,启动自动建,不动旧表、无 ALTER):

```sql
CREATE TABLE IF NOT EXISTS tags (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL UNIQUE,          -- 名称唯一,防混淆
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS tag_members (
  tag_id       INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  candidate_id INTEGER NOT NULL REFERENCES candidates(id),
  added_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (tag_id, candidate_id)          -- 天然幂等,重复追加自动跳过
);
```

访问函数(风格对齐 db.py 现有函数):

- `list_tags()` → `[{id, name, created_at, member_count}]`(COUNT 子查询)
- `create_tag(name)` / `rename_tag(tag_id, name)`(撞名抛 ValueError)/ `delete_tag(tag_id)`
- `add_tag_members(tag_id, candidate_ids)` → `{added, skipped}`(INSERT OR IGNORE 后 rowcount 差)
- `list_tag_candidates(tag_id)` → join `candidates` 的完整候选行(形状与 run 候选行一致,见下)
- `remove_tag_member(tag_id, candidate_id)`

## 接口(backend/main.py)

| 接口 | 请求/响应 |
|---|---|
| `GET /tags` | 标签列表(含 member_count) |
| `POST /tags` | `{name}` → 201;撞名 409 |
| `PUT /tags/{id}` | `{name}` 重命名;撞名 409;不存在 404 |
| `DELETE /tags/{id}` | 删除,成员级联 |
| `POST /tags/{id}/members` | `{candidate_ids:[...]}` → `{added, skipped}`;tag 不存在 404 |
| `DELETE /tags/{id}/members/{candidate_id}` | 移除单人 |
| `GET /tags/{id}/members` | 成员完整候选行(含 invited 标记,见下) |
| `GET /runs/{rid}/candidates` | **扩展**:每行增加 `invited`(该候选是否已有过邀请记录,EXISTS 子查询),供「已沟通」筛选 |
| `GET /invites` | **扩展**:可选参数 `tag_id`,按候选 ∈ 标签过滤(跨 run) |

候选行统一形状:现有 run 候选行 + `invited` 布尔。`GET /tags/{id}/members` 返回相同形状
(global candidates 字段 + invited),邀请页标签模式与抽屉复用同一行结构。

## 搜索页 UI(CandidateList.vue,候选表格唯一属主)

**筛选栏**(表格上方一行,el-select/el-checkbox/el-input-number,全部带「不限」默认):

| 维度 | 控件 | 取值/逻辑 |
|---|---|---|
| 学历 | 多选 | 博士/硕士/本科/大专(按候选数据去重生成);空=不限;命中任一 |
| 工作年限 | 单选区间 | 应届/1-3/3-5/5-10/10+;work_years 文本("3年"/"10年以上")正则取首数字转数值后比区间 |
| 城市 | 多选 | 候选 current_city 去重生成;空=不限;命中任一 |
| 年龄 | 起止数字 | ≥min 且 ≤max(填了才生效) |
| AI 评分 | 单选 | 不限/高/中/低/未评分;高/中/低 取 `score.total` 档;「未评分」= 无评分记录者(有分的人不显示) |
| 状态 | 多选 | 新简历(is_new)/ 简历已抓取(resume_fetched)/ 已沟通过(invited)/ 硬门槛通过(hard_gate);空=不限;命中任一 |

组合语义:维度间 AND,维度内 OR。任一筛选项非默认 → 显示「已筛选 N / 共 M」+「重置」。
筛选是**纯前端计算**,作用于当前已加载的候选行(不动后端、不改轮询)。

**存标签**(筛选栏右侧按钮):

- 「存为新标签」:dialog 输名称(必填)→ `POST /tags` → `POST /tags/{id}/members` 带当前**筛选后展示行**的 candidate_ids → toast「已存入「X」N 人」
- 「加入已有标签」:dialog 选标签(带成员数下拉)→ POST members → toast「新增 A 人,跳过已在标签的 B 人」
- 展示为 0 时按钮禁用;说明文字「保存当前筛选结果(本 run 的 N 人)」

**标签抽屉**:筛选栏旁「标签管理」按钮 → el-drawer(右侧)。左侧标签列表(名 + 成员数),
操作:重命名(弹输入)、删除(el-popconfirm)。点标签 → 成员表(复用行风格:姓名/期望/公司/
学历/年限/城市 + 移除按钮,popconfirm),成员跨 run。空态引导「去筛选后存为新标签」。

## 邀请页 UI(InvitePanel.vue)

顶部来源切换 el-radio-group:「按 Run(现状)」|「按标签」。

- run 模式:UI 与逻辑零改动
- 标签模式:隐藏 run 选择,显示标签下拉(空成员标签置灰不可选);载入 `GET /tags/{id}/members`
  → 候选表(`candidatesWithGate` 逻辑不适用,**展示全部成员,不卡 hard_gate**)→ 勾选 →
  选岗位 → 招呼语(保持现有行为)→「批量邀请」`POST /invites/batch`
  (candidate_ids 照传;invite 记录的 run_id 记 NULL——跨 run 无主 run;`UNIQUE(candidate_id, job_id)`
  幂等不变,重复对同一岗位发起由后端跳过并计入现有错误流)
- 下方邀请记录区:标签模式下轮询 `GET /invites?tag_id=X`,其余展示逻辑与 run 模式一致

## 错误与边界

- 名称撞名(建/改)后端 409,前端 toast 提示
- 删除标签时若邀请页正选中 → 轮询 404 → toast「标签已删除」+ 刷新下拉
- 追加成员:INSERT OR IGNORE 幂等,已存在不计入 added;candidate_id 不存在同样静默跳过(skipped 只统计「已在标签内」)
- 并行/重复点击:SQLite 顺序执行,按钮 loading 防连点

## 测试

- 后端脚本(仿现有 tests/ 风格):tags CRUD、撞名 409、级联删除、幂等追加 added/skipped、
  tag members 形状含 invited、/invites?tag_id 过滤正确
- 前端:改完重启后 curl 全接口冒烟;页面由用户手动过:筛选组合 → 存标签 → 跨 run 追加 →
  抽屉移除 → 邀请页标签模式发起

## 不做(明确出范围)

- 筛选条件保存复用(预设) — 需要时再加
- 关键字/薪资文本筛选、逐人手动打标签、run 模式邀请页内显示标签
