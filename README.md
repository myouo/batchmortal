# Batch Mortal Analysis

`batchmortal` 是一个批量牌谱分析脚本：从 `amae-koromo` 获取雀魂对局，或从 `nodocchi.moe` 获取天凤四人半庄对局，再通过 SeleniumBase 将牌谱提交到 `mjai.ekyu.moe`，最后导出 CSV/XLSX 结果与可选图表。

## 环境要求

- Python 3.10+
- Google Chrome
- 能够访问所选数据源及 `mjai.ekyu.moe` 的网络环境

安装：

```bash
git clone https://github.com/myouo/batchmortal.git
cd batchmortal
pip install -r requirements.txt
```

## 推荐：使用配置文件

项目提供完整示例配置 [`config.example.yaml`](config.example.yaml)。先编辑其中的 `mode` 和对应玩家 ID，再运行：

```bash
python main.py --config config.example.yaml
```

建议先开启 `dry_run: true`，确认玩家、模式和提取出的牌谱链接正确；确认后改回 `false` 进行 Mortal 分析。

### 数据源配置

```yaml
# 同时只能选择一个数据源：mj/0 为雀魂，th/1 为天凤
mode: "th"

# mode: mj 时只读取这一段
mj:
  nickname: ""
  # account_id: 12345678
  limit: 10
  modes: "12"

# mode: th 时只读取这一段
th:
  nickname: ""
  limit: 10
  modes: "4p-south"
```

`mj:` 与 `th:` 可以同时保存两套参数，但每次运行只读取顶层 `mode` 选中的一段：

- `mode`：数据源开关，推荐填写 `mj` 或 `th`；也兼容 `0` 或 `1`。
- `modes`：当前数据源内部的对局类型筛选，与顶层 `mode` 含义不同；天凤固定使用 `4p-south`。
- 雀魂可使用 `nickname`，也可使用数字 `account_id`；天凤必须使用 `nickname`。
- `limit` 按实际对局模式限制获取数量。

### 对局模式

| 数据源 | `modes` 示例 | 说明 |
| :--- | :--- | :--- |
| 雀魂 `mj` | `9,12,16` | amae-koromo 的数字模式 ID，例如 9 四人金南、12 四人玉南、16 四人王座南 |
| 天凤 `th` | `4p-south` | 仅接受四人半庄（四麻南场）牌谱 |

天凤的 `4p-south` 是本项目根据 Nodocchi 返回的 `playernum` 和 `playlength` 生成的统一筛选名，并非 Nodocchi API 直接返回的字符串。三麻和东风牌谱不在本项目的天凤分析范围内。

### 常用公共配置

完整字段和注释请直接查看 [`config.example.yaml`](config.example.yaml)，常用字段包括：

| 配置项 | 作用 |
| :--- | :--- |
| `review_language` | 分析页面语言：`zh-CN`, `en`, `ja`, `ko` |
| `review_ui` | 结果页样式：`classic` 或 `killerducky`；默认保持 `classic` 兼容性 |
| `model_tag` | Mortal 模型版本 |
| `headless` | 是否无界面运行浏览器 |
| `dry_run` | 只提取并打印牌谱链接，不启动浏览器 |
| `retry` | 单条牌谱失败后的重试次数 |
| `analyze_bad_move_rate` | 是否统计 5%/10% 两档恶手率 |
| `save_screenshot` | 是否保存分析结果截图 |
| `save_local_paipu` | 是否保存 Mortal 结果页 HTML |
| `output` | `csv` 或 `xlsx` |
| `plot` | `none`, `html`, `png`, `both` |
| `plot_limit` | 图表只使用最近 N 条结果；不填表示全部 |

## 命令行覆盖

配置文件是推荐入口；临时参数可以在命令行中覆盖：

```bash
# 使用配置文件，但临时切换到天凤并只提取链接
python main.py --config config.example.yaml --mode th -p ププリン --modes 4p-south --limit 10 --dry-run

# 使用配置文件，但临时分析指定雀魂玩家
python main.py --config config.example.yaml --mode mj -p 言乾 --modes 12 --limit 10

# 使用配置文件，直接从指定文件中批量读取牌谱链接
python main.py --config config.example.yaml --file paipu.txt
```

主要参数：

| 参数                   | 说明                                                 |
|:---------------------|:---------------------------------------------------|
| `--config`           | 指定 YAML/TOML 配置文件                                  |
| `--mode`             | 唯一数据源：`mj`/`0` 或 `th`/`1`                          |
| `--file`             | 文件数据源：从指定的文件名中读取牌谱链接，每行一个链接。在此模式下，对局模式，对局时间等信息不可用。 |
| `-p`, `--player`     | 当前数据源的玩家昵称                                         |
| `-a`, `--account-id` | 雀魂数字账号 ID；天凤不支持                                    |
| `--modes`            | 逗号分隔的对局模式                                          |
| `--limit`            | 每个实际模式最多获取的记录数                                     |
| `--review-ui`        | 结果页样式：`classic` 或 `killerducky`                    |
| `--dry-run`          | 只打印牌谱 URL                                          |
| `--headless`         | 切换无头浏览器设置                                          |
| `--badmove`          | 开启恶手率统计                                            |
| `--save-local`       | 保存 Mortal 结果页 HTML                                 |
| `--save-screenshot`  | 保存结果截图                                             |
| `--plot`             | 生成 HTML/PNG 图表                                     |

KillerDucky 页面将 Rating 和 AI 一致率显示在 About 中。项目实际从该页面引用的
`/report/*.json` 结构化数据读取这些字段；开启 `analyze_bad_move_rate` 后，也会根据每个
决策的 `actual_index` 与实际选择概率计算 5%/10% 恶手率。两种 UI 的恶手率口径一致。

旧参数 `--source majsoul|tenhou` 仍可兼容使用，但不能和 `--mode` 同时出现；新配置统一推荐 `mode: mj|th`。

## 浏览器提交模式

- 默认：单个持久浏览器串行处理，使用提交间隔和失败冷却，稳定性最好。
- `prewarm_standby: true`：使用两个持久窗口轮流处理任务；仍是受控提交，不保证更快。
- `unsafe_parallel_review: true`：绕过受控提交协调，不代表真正的多线程并发，可能更容易触发 Turnstile 或限流。
- `submit_interval`：受控模式下两次提交的最小间隔秒数。
- `submit_cooldown`：连续提交失败后的冷却秒数。

## 输出目录

雀魂与天凤结果按来源并列保存：

```text
results/
├── majsoul/<nickname>/
└── tenhou/<nickname>/
```

常见文件：

- `results.xlsx` 或 `results.csv`
- `mode_<id>/<uuid>.png`
- `mode_<id>/<uuid>_error.png`
- `mode_<id>/<uuid>.html`
- `report_<nickname>.html` / `report_<nickname>.png`

导出结果包含 `source` 字段，用于标识 `majsoul` 或 `tenhou`。天凤模式目录示例为 `mode_4p-south`。

可视化报告包含关键指标卡、Rating 单半庄值与半庄移动平均、按决策数加权的 AI 一致率、Rating 分布和低 Rating 牌谱检讨入口。缺失指标显示为 `—`，不会按 0 计入图表或汇总。

从旧版本升级时，请将原有的 `results/<nickname>/` 雀魂目录移动到 `results/majsoul/<nickname>/`；否则程序无法从新目录识别以前已经处理的牌谱。

## 注意事项

- Nodocchi 返回的 `tw` 是压缩座位排列；脚本会解码目标玩家视角，并且只接受 `tenhou.net` 正式牌谱 URL。
- Nodocchi 中没有 `url` 或 `tw` 的历史统计记录不会进入 Mortal 分析队列。
- 已成功写入结果文件的牌谱会跳过；失败记录仍可在后续运行中重试。
- `--badmove`、本地 HTML 和截图只会应用于新执行的分析，不会自动重跑已经成功的牌谱。
- 总耗时通常取决于浏览器提交、Cloudflare Turnstile 和远端分析生成速度。

## License

MIT
