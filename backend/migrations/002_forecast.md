# 002 雨量情景预报

应用启动时以 `CREATE TABLE` 补建新表，并对既有 `runs` 表执行幂等 `ALTER TABLE … IF NOT EXISTS`（见 `src/main.py` 的 `MIGRATIONS`），新旧数据卷均可直接升级。

- `forecast_versions` 上游情景预报版次，`version_no` 全库唯一，记录来源与累计接收批数。
- `scenario_entries` 情景逐日净涨幅条目，以 `(version_id, scenario, forecast_date)` 唯一；同版乱序补报按情景和日期取最新修订（保留最大 `revision`）。
- `published_forecasts` 已发布的触线概率快照，`version_id` 唯一保证同批重跑不重复发布；只有更高 `version_no` 的计算结果才能接替当前发布。
- `runs.kind` 区分趋势预测（`trend`）与情景触线概率（`scenario`）；`runs.version_id` 指向所算版次（普通索引列，不建外键，保证迁移与新建库结构一致）。
- 运行状态新增 `stale`：上游更新版次后，在算或排队的旧版情景计算即刻失效。
