# 美区基础资料补齐执行报告（2026-11-23）

- 范围：两店美区已收录 678 款；成功刷新 669 款，未完成 9 款。
- 来源：Apple 美国区 Lookup、Google Play 美国区官方详情（现有锁定采集库）。
- Apple 视频采集与自动重试已停用；保留历史视频地址，不新增请求。
- Google Play 只保存官方视频及封面地址，不下载视频文件。
- 不调用 AI，不修改现有 AI 深度补档任务，不提交、不推送。

| 平台 | 目标 | 本轮成功刷新 | 缓存中有评分 | 缓存中有视频 |
|---|---:|---:|---:|---:|
| App Store | 268 | 264 | 264 | 暂停（仅保留历史记录） |
| Google Play | 410 | 405 | 387 | 121 |

## 未完成记录

每项均尝试两轮。保留旧资料；接口未返回不等于已证实下架。非游戏分类不强行写成游戏。

| 游戏 | 标识 | 原因 |
|---|---|---|
| Amaze Romantic Goddess | android:com.arrow.romantic.puzzle.goddess | App not found (404) |
| Minion Rush: Running Game | android:com.gameloft.android.ANMP.GloftDMHM | App not found (404) |
| OOC: The Playable Anime | android:com.newai.ooc | Official result: not_game |
| Castlevania: 40th Anniversary | android:jp.konami.epjCastlevania1 | App not found (404) |
| Castlevania: 40th Anniversary | ios:us:1498019884 | Official result: unavailable |
| Minion Rush: Running game | ios:us:596402997 | Official result: unavailable |
| CrownCards - Trading Cards | ios:us:6762010774 | Official result: not_game |
| Cumulark | ios:us:6833957187 | Official result: unavailable |
| Cube Smash | android:com.combo.cubesmash | App not found (404) |

## 官方未提供评分的成功记录

以下 19 款详情获取成功，但缓存中没有有效评分；缺失不估算，也不写成零：

- Dumpling Trading: Squishy Fun（android:com.dumpling.trading.squishy.fun）
- Dino Marshal（android:com.fowreed.dinocomd）
- Slow Mo Hit（android:com.gdm.slowmo）
- Numhop: Math Puzzle（android:com.numhop.app）
- Punch Arena: Battle Clash（android:com.onepunch.battleground）
- KPOP Sandbox Battle Playground（android:com.somia.kpop.ragdoll.sandbox）
- Printa: 3D Printing Tycoon（android:com.buildbyhenri.printa）
- ChessGame（android:com.payonce.chess）
- iChess（android:com.kidzzone.chess）
- BrainyPet: Kids Learning Pet（android:com.brainypet.myapp）
- Branch Line（android:com.chillicactusgames.branchline）
- Keyboard Run: Master Escape（android:com.pl.bike.blitzz.racing.sports）
- Hit Wall: Block Smasher（android:com.hit.wall）
- Bullmora: 777 Slots（android:com.rosybarn.briskbyre）
- Find the Ducks（android:app.puzzleplay.ducks）
- Neuro Zip: Puzzle Game（android:com.buildapps.neuro_zip）
- Dumpling Squishy: Fidget Trade（android:com.mystery.dumplings.unboxing.livestreamasmr）
- Cocobi Little Monster Hospital（android:com.kigle.cocobi.littlemonsterhospital）
- Topplewood（android:com.game.dropcolorblocks）

## 文件与执行记录

- 数据：`data/base/store-apps.json`、`data/releases/catalog.json`、`data/releases/2026-11-22.json`。
- 脚本：新增 `scripts/backfill_store_basics.py`；调整 `scripts/store_apps.py`、`scripts/release_monitor.py` 停用 Apple 视频请求/重试。
- 新增暂停逻辑检查用例：`scripts/test_release_monitor.py`，本轮未运行自动测试。
- 执行：`git fetch origin`、只读 Git 状态/分支核对、`python -u scripts/backfill_store_basics.py`。
- 运行时逐条核对官方应用 ID、游戏分类；统计资料缺失项，发现存储缺失标记与实际字段不一致数为 0。
- 最新榜单/上架快照日期仍为 2026-11-22；本轮补充的是 2026-11-23 获取的基础资料，不生成新榜单。
- 未进行浏览器、图片外链逐个加载或云端验收；资料完整指字段存在，不保证所有外链始终可访问。
- 当前目录保留 `progress.json`、`summary.json` 及 `before-*.json` 原始备份；重跑同一日脚本可续跑失败项。
