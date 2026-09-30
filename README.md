# 临时活动无线电频率申请 API

面向**临时活动**（演出、赛事、应急保障等）的无线电使用申请与审批服务。
使用 **Python + FastAPI + PostgreSQL** 构建，提供可直接调用的 HTTP API。

- 申请包含：平面坐标中的**覆盖圆**、频率**闭区间**、UTC **左闭右开**时段
- 非法申请（半径非正 / 频段下界大于上界 / 起点不早于终点）**整笔拒收**
- 干扰裁决：两项**有效**批准仅在 **时段相交 且 覆盖区域相交**，并且 **频段相交或最短频率间隔小于非负保护带宽** 时构成干扰
- 乐观修订号控制并发：预览返回当前修订号，批准必须携带该号并重新裁决；
  修订号过期或与并发批准相撞，均**不得占用频段**并返回原因
- **整组批准**：运营人员可携带一个预期修订号和非空、无重复的申请 ID 列表
  一次性批准整场活动的多台设备；同一事务内对**当前有效许可与组内申请**统一
  裁决，任一失败则整组不批准、修订号不变；全部通过则按输入顺序写入连续修订号
- 撤销只改变后续裁决所用的**有效集合**；按修订号查询可还原当时全部批准、撤销记录
  （整组批准产生的每个修订号都可查询，还原该设备获批那一刻的有效集合）

---

## 1. 一键启动（Docker Compose）

前置条件：Docker 与 Docker Compose v2。

```bash
docker compose up --build
```

启动后：

- API 地址：<http://localhost:8000>
- 交互式文档（Swagger UI）：<http://localhost:8000/docs>
- ReDoc：<http://localhost:8000/redoc>
- OpenAPI JSON：<http://localhost:8000/openapi.json>
- 健康检查：<http://localhost:8000/health>
- PostgreSQL：`localhost:5432`（库/用户/密码均为 `radio`）

容器启动时 API 会自动等待数据库就绪，然后**建表并初始化修订号 0**，
无需手工执行迁移。数据保存在命名卷 `radio_pgdata` 中；清空重来：

```bash
docker compose down -v
```

---

## 2. 本地开发运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 方式 A：使用本机 PostgreSQL
export DATABASE_URL="postgresql+psycopg2://radio:radio@localhost:5432/radio"
export GUARD_BAND="2.0"
uvicorn app.main:app --reload

# 方式 B：直接用 SQLite（免装数据库；行锁退化为唯一约束兜底，仅供本地/测试）
export DATABASE_URL="sqlite:///./radio.db"
export GUARD_BAND="2.0"
uvicorn app.main:app --reload
```

运行测试（自动使用临时 SQLite 库，共 51 个用例）：

```bash
pip install -r requirements-dev.txt
pytest -q
```

---

## 3. 配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg2://radio:radio@localhost:5432/radio` | SQLAlchemy 数据库连接串 |
| `GUARD_BAND` | `0` | **非负**保护带宽，与频率数字同单位（示例统一用 MHz，即 2.0 = 2 MHz） |
| `APP_HOST` | `0.0.0.0` | 监听地址 |
| `APP_PORT` | `8000` | 监听端口 |

`docker-compose.yml` 中已为容器设好上述变量；频率单位由调用方自行保持一致，
API 不关心具体单位（坐标与半径同理，如均为米）。

---

## 4. 数据模型与裁决规则

### 申请内容

```json
{
  "name": "城南音乐节",
  "circle":      {"cx": 0.0, "cy": 0.0, "radius": 1000.0},
  "frequency":   {"low": 100.0, "high": 110.0},
  "time_window": {"start": "2026-10-01T08:00:00Z", "end": "2026-10-01T12:00:00Z"}
}
```

- **覆盖圆**：圆心 `(cx, cy)` + 半径 `radius`，平面欧氏距离；
  两圆“圆心距 ≤ 半径之和”即相交（恰好相切算相交）。
- **频率闭区间** `[low, high]`：`low == high`（单一频点）合法；
  端点相碰（`110` 对 `[110,120]`）**算相交**。
- **UTC 左闭右开时段** `[start, end)`：时间须带时区（推荐 `Z`）；
  一个时段的结束恰为另一个的开始（`10:00` 对 `[10:00, 12:00)`）**不算相交**。

### 整笔拒收（HTTP 422，一次返回全部违例）

- `circle.radius <= 0` → `radius_not_positive`
- `frequency.low > frequency.high` → `frequency_low_above_high`（相等允许）
- `time_window.start >= time_window.end` → `time_start_not_before_end`（相等也拒绝）
- 非有限数值 → `value_not_finite`；不带时区的时间 → `naive_datetime`

### 干扰成立条件（三项同时满足）

1. `time_intersect`：左闭右开时段相交；
2. `area_intersect`：覆盖圆相交；
3. 频率满足下列之一：
   - `frequency_intersect`：闭区间频段相交（最短间隔视为 0）；
   - `within_guard_band`：频段不相交，但**最短间隔 < 保护带宽**
     （间隔**恰好等于**保护带宽时允许，即“严格小于”）。

裁决只考虑**有效集合**：已批准且未被撤销的申请。待审批、已撤销均不参与。

### 修订号与并发模型

- 初始修订号为 **0**；每次批准、**整组批准中的每项**或撤销都使修订号 +1，
  并追加一条不可变事件。
- `POST .../preview` 返回 `current_revision`、冲突对象及每个触发条件。
- `POST .../approve` 必须携带 `expected_revision`。服务在 PostgreSQL
  单行修订号表上 `SELECT ... FOR UPDATE` 串行化所有批准/撤销事务：
  - `expected_revision != 当前修订号` → **409 `stale_revision`**（修订过期/被并发抢先）；
  - 号对但锁内**重新裁决**发现冲突 → **409 `interference`**（不得占用频段，响应体附冲突）；
  - 通过 → 写入批准事件，占用新修订号。
- `POST .../batch-approve` 整组批准与单笔批准、撤销共享**同一把行锁与修订裁决**：
  - 锁内按输入顺序核对每项：ID 是否存在、是否仍为 `pending`，并对
    **当前有效许可**及**组内更早的申请**统一执行干扰规则（即使更早的组员
    自身也失败，仍参与后续成对裁决，确保一次性返回全部冲突）；
  - 任一项失败（ID 无效 / 状态不符 / 修订过期 / 任一干扰）→ 整组不批准、
    **修订号不变、无部分获批**，返回 **409 `batch_rejected`**，
    `details` 按失败类型分组列出具体申请（含 `batch_index`）与冲突对象；
  - 全部通过 → **一次提交**，按输入顺序为每项写入批准事件和**连续修订号**
    （`from_revision+1 … revision`）；按其中**任一修订号**查询即还原截至
    该项获批的有效集合（见 6.12）。
- `POST .../revoke` 只把申请移出有效集合（产生新修订号），历史不删除。
- `GET /api/revision/{n}` 还原修订号 **n 当时**的批准与撤销记录
  （`n` 超过当前修订号返回 404）。

---

## 5. HTTP 接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/applications` | 提交申请（语义违例整笔 422） |
| `GET` | `/api/applications?status=pending` | 列表（可选状态过滤） |
| `GET` | `/api/applications/{id}` | 查看单笔申请 |
| `POST` | `/api/applications/{id}/preview` | 冲突预览（返回冲突对象、触发条件、当前修订号） |
| `POST` | `/api/applications/{id}/approve` | 携带修订号批准（锁内重新裁决） |
| `POST` | `/api/applications/batch-approve` | **整组批准**：一个修订号 + 非空无重复 ID 列表，全有或全无 |
| `POST` | `/api/applications/{id}/revoke` | 撤销（仅影响后续有效集合） |
| `GET` | `/api/revision/current` | 当前修订号 + 有效集合 + 全部事件 |
| `GET` | `/api/revision/{revision}` | 按修订号还原当时的批准/撤销记录 |
| `GET` | `/health` | 存活探针 |

所有错误使用统一信封：

```json
{"error": {"code": "stale_revision", "message": "...", "details": {...}}}
```

| HTTP | `error.code` | 触发场景 |
| --- | --- | --- |
| 422 | `validation_failed` | 提交/参数校验失败（整笔拒收，`details` 为全部违例）；整组请求的空列表 `application_ids_empty`、重复 ID `application_ids_not_unique` 亦在此 |
| 404 | `not_found` | 申请不存在 |
| 404 | `revision_out_of_range` | 查询了未来修订号 |
| 409 | `not_pending` | 批准已处理申请 / 撤销待审批申请 |
| 409 | `already_revoked` | 重复撤销 |
| 409 | `stale_revision` | 修订号过期或被并发批准抢占 |
| 409 | `interference` | 重新裁决发现干扰，频段未占用 |
| 409 | `batch_rejected` | 整组批准任一失败：修订过期 / ID 无效 / 状态不符 / 组内外干扰，整组不批准且修订号不变 |

---

## 6. 调用示例（curl 完整走查）

下例保护带宽为 **2 MHz**（`GUARD_BAND=2.0`），两个活动同地同时段。

### 6.1 非法申请被整笔拒收

```bash
curl -i -X POST http://localhost:8000/api/applications \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "bad",
    "circle":    {"cx": 0, "cy": 0, "radius": -5},
    "frequency": {"low": 200, "high": 100},
    "time_window": {"start": "2026-10-01T10:00:00Z", "end": "2026-10-01T08:00:00Z"}
  }'
```

```json
HTTP/1.1 422 Unprocessable Entity
{"error": {"code": "validation_failed", "message": "the whole application was rejected due to invalid fields",
  "details": [
    {"field": "circle.radius", "code": "radius_not_positive", "message": "radius must be strictly positive"},
    {"field": "frequency.low", "code": "frequency_low_above_high", "message": "frequency low bound must not exceed high bound"},
    {"field": "time_window.start", "code": "time_start_not_before_end", "message": "time_window start must be strictly earlier than end"}
  ]}}
```

### 6.2 提交申请 A 并预览

```bash
curl -s -X POST http://localhost:8000/api/applications \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "音乐节 A",
    "circle":    {"cx": 0, "cy": 0, "radius": 1000},
    "frequency": {"low": 100, "high": 110},
    "time_window": {"start": "2026-10-01T08:00:00Z", "end": "2026-10-01T12:00:00Z"}
  }'
# -> {"id": 1, "status": "pending", ...}

curl -s -X POST http://localhost:8000/api/applications/1/preview
```

```json
{"application_id": 1, "current_revision": 0, "guard_band": 2.0,
 "has_conflicts": false, "conflicts": []}
```

### 6.3 携带修订号批准 A（占用修订号 1）

```bash
curl -s -X POST http://localhost:8000/api/applications/1/approve \
  -H 'Content-Type: application/json' -d '{"expected_revision": 0}'
```

```json
{"approved": true, "application_id": 1, "revision": 1, "guard_band": 2.0,
 "application": {"id": 1, "status": "approved", "approval_revision": 1, ...}}
```

### 6.4 提交 B：频段 111–112 MHz，与 A 最短间隔 1 MHz（< 2 MHz）

```bash
curl -s -X POST http://localhost:8000/api/applications \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "马拉松 B",
    "circle":    {"cx": 100, "cy": 0, "radius": 1000},
    "frequency": {"low": 111, "high": 112},
    "time_window": {"start": "2026-10-01T09:00:00Z", "end": "2026-10-01T11:00:00Z"}
  }'
# -> id 2

curl -s -X POST http://localhost:8000/api/applications/2/preview
```

```json
{"application_id": 2, "current_revision": 1, "has_conflicts": true,
 "conflicts": [{
   "application": {"id": 1, "circle": {"cx": 0.0, "cy": 0.0, "radius": 1000.0},
                   "frequency": {"low": 100.0, "high": 110.0}, ...},
   "trigger": {"time_intersect": true, "area_intersect": true,
               "frequency_intersect": false, "minimum_frequency_gap": 1.0,
               "within_guard_band": true},
   "reason": "within_guard_band"}]}
```

### 6.5 过期修订号 → 拒绝占用

```bash
curl -i -X POST http://localhost:8000/api/applications/2/approve \
  -H 'Content-Type: application/json' -d '{"expected_revision": 0}'
```

```json
HTTP/1.1 409 Conflict
{"error": {"code": "stale_revision",
  "message": "expected revision 0 but current revision is 1; re-preview and re-decide",
  "details": {"expected_revision": 0, "current_revision": 1}}}
```

### 6.6 修订号正确但锁内重新裁决相撞 → interference

```bash
curl -i -X POST http://localhost:8000/api/applications/2/approve \
  -H 'Content-Type: application/json' -d '{"expected_revision": 1}'
# HTTP/1.1 409 Conflict
# {"error": {"code": "interference", ...,
#   "details": {"current_revision": 1, "conflicts": [ ...同预览... ]}}}
```

### 6.7 并发批准相撞的复现（两个请求携带同一修订号）

```bash
# 两个完全重叠的待审批申请 id=1、id=2，当前修订号为 0
curl -s -X POST localhost:8000/api/applications/1/approve \
  -H 'Content-Type: application/json' -d '{"expected_revision":0}' > r1.json &
curl -s -X POST localhost:8000/api/applications/2/approve \
  -H 'Content-Type: application/json' -d '{"expected_revision":0}' > r2.json
wait
# 结果：一个 {"approved": true, "revision": 1}
#       另一个 409 {"error": {"code": "stale_revision",
#                 "details": {"expected_revision": 0, "current_revision": 1}}}
```

### 6.8 撤销 A：只影响后续裁决，产生修订号 2

```bash
curl -s -X POST http://localhost:8000/api/applications/1/revoke \
  -H 'Content-Type: application/json' -d '{"reason": "活动改期"}'
# {"revoked": true, "revision": 2, "previous_approval_revision": 1, ...}
```

### 6.9 B 重新预览（有效集合已空）并在修订号 3 获批

```bash
curl -s -X POST http://localhost:8000/api/applications/2/preview
# {"current_revision": 2, "has_conflicts": false, ...}

curl -s -X POST http://localhost:8000/api/applications/2/approve \
  -H 'Content-Type: application/json' -d '{"expected_revision": 2}'
# {"approved": true, "revision": 3, ...}
```

### 6.10 按修订号还原历史

```bash
curl -s http://localhost:8000/api/revision/1
# {"revision": 1, "effective_application_ids": [1],
#  "approvals": [{"revision": 1, "application_id": 1, ...}], "revocations": []}

curl -s http://localhost:8000/api/revision/2
# {"revision": 2, "effective_application_ids": [],
#  "approvals": [{"revision": 1, ..., "revoked_at_revision": 2}],
#  "revocations": [{"revision": 2, "application_id": 1, "reason": "活动改期"}]}

curl -s http://localhost:8000/api/revision/999
# HTTP 404 {"error": {"code": "revision_out_of_range", ...}}
```

### 6.11 临界值速查（保护带宽 2 MHz）

| 场景 | 结论 |
| --- | --- |
| 频段 `[100,110]` 对 `[110,120]`（闭区间端点相碰） | 冲突 `frequency_intersection` |
| 频段 `[100,110]` 对 `[111,112]`（间隔 1 < 2） | 冲突 `within_guard_band` |
| 频段 `[100,110]` 对 `[112,120]`（间隔 2 == 2） | **不冲突**（严格小于） |
| 时段 `[08,10)` 对 `[10,12)`（左闭右开端点相接） | 时段**不**相交 |
| 两圆相切（圆心距 == 半径和） | 区域相交 |

脚本版走查：[`examples/walkthrough.sh`](examples/walkthrough.sh)
（`./examples/walkthrough.sh http://localhost:8000`）。

### 6.12 整组批准（多台设备一次裁决）

一场活动的多台设备分别提交申请后，运营人员调用 `batch-approve`，携带
**一个** `expected_revision` 与按申请顺序排列的 ID 列表。下例的三个频段
彼此间隔 3 MHz（> 2 MHz 保护带宽），同地同时段也不构成干扰：

```bash
curl -s -X POST http://localhost:8000/api/applications/batch-approve \
  -H 'Content-Type: application/json' \
  -d '{"expected_revision": 0, "application_ids": [1, 2, 3]}'
```

成功时整组**一次提交**，按输入顺序占用连续修订号（上例假设当前为修订号 0，
即占用 1、2、3）：

```json
{
  "approved": true,
  "expected_revision": 0,
  "from_revision": 0,
  "revision": 3,
  "revisions": [1, 2, 3],
  "count": 3,
  "guard_band": 2.0,
  "results": [
    {"application_id": 1, "revision": 1, "application": {"id": 1, "status": "approved", "approval_revision": 1, ...}},
    {"application_id": 2, "revision": 2, "application": {"id": 2, "status": "approved", "approval_revision": 2, ...}},
    {"application_id": 3, "revision": 3, "application": {"id": 3, "status": "approved", "approval_revision": 3, ...}}
  ]
}
```

按其中**任一修订号**查询，还原的是**截至该项获批**的有效集合（前缀）：

```bash
curl -s http://localhost:8000/api/revision/2
# {"revision": 2, "effective_application_ids": [1, 2],
#  "approvals": [{"revision": 1, ...}, {"revision": 2, ...}], "revocations": []}
```

任一检查失败时，整组不批准、**修订号不变、没有任何一项获批**，HTTP 409
`batch_rejected`，`details` 按失败类型分组（仅出现实际发生的类型；每条都带
其在输入列表中的 `batch_index`，冲突对象额外标注 `scope`：
`effective` = 当前有效许可，`batch` = 组内更早的申请并附 `batch_index`）：

```bash
# id 4 与有效许可冲突、id 99999 不存在、且修订号已过期 -> 一次返回全部原因
curl -i -X POST http://localhost:8000/api/applications/batch-approve \
  -H 'Content-Type: application/json' \
  -d '{"expected_revision": 0, "application_ids": [4, 99999]}'
```

```json
HTTP/1.1 409 Conflict
{"error": {"code": "batch_rejected",
  "message": "the whole batch was not approved (stale_revision, not_found, interference); no revision was consumed",
  "details": {
    "expected_revision": 0, "current_revision": 3, "revised": false,
    "stale_revision": {"expected_revision": 0, "current_revision": 3},
    "applications_not_found": [{"application_id": 99999, "batch_index": 1}],
    "interference": [
      {"application_id": 4, "batch_index": 0,
       "conflicts": [
         {"scope": "effective", "application": {"id": 2, ...},
          "trigger": {...}, "reason": "frequency_intersection"}]}]
  }}}
```

`details` 可能出现的分组键：`stale_revision`、`applications_not_found`
（ID 无效）、`applications_not_pending`（已批准/已撤销，附当前 `status`）、
`interference`（每项含其全部冲突对象，`scope` 区分有效许可与组内申请）。

请求本身畸形（空列表 / 重复 ID）属于整笔 422 `validation_failed`：

```json
{"error": {"code": "validation_failed",
  "details": [{"field": "application_ids", "code": "application_ids_not_unique",
               "message": "application id 1 appears more than once",
               "application_id": 1}]}}
```

整组批准与单笔批准、撤销在 PostgreSQL 上共享同一行锁：并发的三类事务
**串行裁决**，绝不会出现部分获批或相互干扰的有效许可——抢占失败的一方收到
`stale_revision`（极端情况下由唯一约束兜底），重新预览/整组决定后重试即可。

脚本版走查：[`examples/batch_walkthrough.sh`](examples/batch_walkthrough.sh)
（`./examples/batch_walkthrough.sh http://localhost:8000`）。

---

## 7. 项目结构

```
.
├── app/
│   ├── main.py                 # FastAPI 路由、序列化、并发/快照逻辑
│   ├── adjudication.py         # 纯函数：圆/时段/频段/保护带宽裁决
│   ├── semantic_validation.py  # 整笔拒收的跨字段语义校验
│   ├── schemas.py              # Pydantic 请求模型
│   ├── models.py               # SQLAlchemy 表（申请 + 批准/撤销事件 + 修订号）
│   ├── db.py                   # 引擎、等待数据库、建表与修订号初始化
│   ├── config.py               # 环境变量配置
│   └── errors.py               # 统一错误信封
├── tests/                      # 裁决单元测试 + API 端到端测试（SQLite）
│   ├── test_adjudication.py
│   ├── test_api.py             # 单笔提交/预览/批准/撤销/历史
│   └── test_batch_approve.py   # 整组批准、组内冲突、并发裁决
├── examples/walkthrough.sh     # curl 走查脚本（单笔）
├── examples/batch_walkthrough.sh # curl 走查脚本（整组批准）
├── Dockerfile
├── docker-compose.yml          # db + api 一键启动
├── requirements.txt / requirements-dev.txt
└── README.md
```
