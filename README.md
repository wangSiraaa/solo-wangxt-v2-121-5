# 蒸馏曲线切点选择与产率核对（化工培训系统）

根据**离线**累积蒸馏试验曲线（TBP / 恩氏）选择馏分温度切点、核对各段体积/质量
产率，并在调整切点时实时显示**重叠、缺口、残余量**与物料平衡闭合情况。

> 仅处理题目给定的离线试验数据，不连接、不控制任何真实装置。

## 技术栈

| 层 | 实现 |
|----|------|
| 前端 | React 18 + TypeScript + Vite（SVG 自绘曲线图，无重型图表库） |
| 后端 | FastAPI，插值/换算全部用 **NumPy + SciPy** 纯函数实现 |
| 存储 | PostgreSQL（JSONB 保存试验条件、密度、曲线点、方案快照）；本地演示可用 SQLite |
| 数据 | `data/seed.json` 内置 3 组教学算例 |

## 核心计算规则（`backend/app/`）

1. **曲线坐标不混用**：曲线横轴是温度，纵轴 `R(T)` 是**累积回收体积百分比**
   （进料体积基准）。质量百分比是另一套基准，绝不直接当成同一横轴。
2. **单调插值 PCHIP**（`curve.py`）：`scipy.interpolate.PchipInterpolator`
   保形分段三次 Hermite，单调输入下严格保持单调，平台段不会像普通三次样条那样过冲。
3. **严格不外推**：所有求值只在实测温度区间 `[T_min, T_max]` 内；切点越界部分
   不计产率，返回 `out_of_range` 提示。导出文件写明适用范围。
4. **异常先提示核实**：
   - 曲线下降（累积量必须单调非降）、同温度两个回收值、回收 >100% → 硬错误；
   - 缺 0%/100% 端点、总回收量异常偏低（<80%）→ 警告/提示；
   - 损失超过未回收余量 `100−R_max` → 警告（全回收时为硬错误）。
5. **密度有依据换算**（`density.py`）：以 100 体积单位进料为基准，逐曲线结点段
   `ΔVᵢ × ρᵢ / ρ_feed` 累加；`ρᵢ` 为馏出温度—密度表在**段中点温度**的分段线性
   插值（馏分越重密度越高，必须分段取值）。残渣使用独立的残渣密度。
   密度表覆盖不到的段不外推密度：已覆盖部分照常计入，缺失段的体积量单独报告。
6. **重叠/缺口/残余**（`planning.py`）：
   - 重叠 = 馏分区间交集（同一部分被重复计产率）；
   - 缺口 = 馏分之间及首尾未覆盖的温度段（分前/中/尾，越界段标记"无数据"）；
   - 相邻馏分端点相等（如 185→185 零宽馏分）识别为零宽，产率 0 且不产生重叠/缺口；
   - 体积平衡恒等式（恒成立，作为自检）：

     ```
     馏分并集 + 范围内未切出 + 起点前回收(R_min) + 未回收残渣(100−R_max) = 100%
     ```

     试验损失是"未回收残渣"内部的明细（净额 = 毛额 − 损失），不另外相加。
   - 质量基准同一套区间逻辑；若密度表/进料密度不自洽（反算馏出液平均密度与
     进料密度不符），给出残差与核实建议，体积平衡仍成立。

## 三组内置算例（`data/seed.json`）

- **示例 A**：首点 5%、末点 90%（**缺两端点**），末切点 370 ℃ 超过实测终点 340 ℃
  → 缺端点提示、越界截断、高温不外推。
- **示例 B**：0–100% 完整曲线，馏分密度 0.64→0.95 **随馏分显著变化**，含一个
  **相邻切点相等的零宽馏分**（航煤 185→185 ℃），演示体积%与质量%明显不同。
- **示例 C**：190 ℃ 处回收量被误抄低（**曲线下降硬错误**），切点带两处**重叠**、
  一处**中间缺口**和越界尾段；密度与进料不自洽时给出质量平衡核实提示。

## 导出

保存方案后可导出 Markdown / JSON，均包含：
PCHIP 插值方法、体积/质量换算依据、残渣密度假设、温度适用范围、
"范围外不外推、不存在的高温数据不做假设"声明、逐馏分产率、重叠/缺口明细与
100% 平衡核对、全部数据提示。

## 运行

### 后端

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate   # 可选
pip install -r requirements.txt
cp .env.example .env                                 # 默认 SQLite，零依赖起步
uvicorn app.main:app --reload --port 8000
# 启动后导入教学算例：
curl -X POST http://localhost:8000/api/seed
```

切换 PostgreSQL：`docker compose up -d db`，并把 `.env` 中 `DATABASE_URL`
改为 `postgresql+asyncpg://distill:distill@localhost:5432/distillation`，
重启即可（JSON 列在 PG 上自动使用 JSONB）。

### 前端

```bash
cd web
npm install
npm run dev        # http://localhost:5173 ，/api 已代理到 8000
```

### 测试

```bash
cd backend && python3 -m pytest -q   # 26 个用例：插值/密度/重叠/缺口/闭合/API
cd web && npm run build              # tsc 类型检查 + 构建
```

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/experiments` | 试验列表 |
| POST | `/api/experiments` | 录入试验（曲线下降等硬错误返回 422 及问题清单） |
| GET | `/api/experiments/{id}/curve/sample` | 实测范围内的 PCHIP 取样曲线（画图用） |
| POST | `/api/experiments/{id}/evaluate` | 按切点实时计算，不落库 |
| POST | `/api/experiments/{id}/plans` | 保存方案（同时存结果快照） |
| GET | `/api/plans/{id}/export?format=markdown\|json` | 导出 |

交互式文档：`http://localhost:8000/docs`。
