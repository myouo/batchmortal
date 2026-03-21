# Batch Mortal Analysis

`batchmortal` 是一个基于 Python 和 SeleniumBase 的批量牌谱分析脚本。它会从 `amae-koromo` 拉取雀魂对局记录，再自动打开 `mjai.ekyu.moe` 提交牌谱、等待分析完成，并把结果导出为 `xlsx` 或 `csv`。

## 环境要求

- Python 3.8+
- Google Chrome
- 可访问目标站点的网络环境

安装依赖：

```bash
git clone https://github.com/myouo/batchmortal.git
cd batchmortal
pip install -r requirements.txt
```

## 基本用法

```bash
python main.py <玩家昵称> [选项]
```

### 使用配置文件（推荐）

你可以通过配置文件来简化命令行输入。在项目根目录下创建一个 `config.yaml` 或 `config.toml` 文件（或参考 [`config.default.yaml`](config.default.yaml)）。

示例 `config.yaml`：

```yaml
nickname: "言乾"
limit: 10
modes: "12"
headless: true
save_screenshot: true
output: "xlsx"
```

带配置文件的运行方式：
```bash
python main.py
```
*(你也可以通过 `python main.py --config my_config.yaml` 手动指定配置文件位置。命令行传入的参数会覆盖配置文件中的同名设置。)*

常见示例（不使用配置文件）：


```bash
python main.py 言乾 --modes 12 --limit 1 --headless --save-screenshot --output xlsx
```

## 参数说明

| 参数 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `nickname` | 无 | 目标雀魂昵称，位置参数 |
| `--limit` | `10` | 每个 mode 最多拉取多少条记录 |
| `--modes` | `9` | 逗号分隔的 mode 列表，例如 `9（四人金南）,12（四人玉南）,16（四人王座南）` |
| `--model-tag` | `4.1b` | Mortal 分析模型版本（未测试） |
| `--headless` | `False` | 后台无界面运行浏览器（推荐） |
| `--dry-run` | `False` | 只拉取并打印牌谱 URL，不启动浏览器 |
| `--save-screenshot` | `False` | 保存分析结果页面截图（举报时可用） |
| `--output` | `xlsx` | 导出格式，可选 `xlsx` 或 `csv`（默认xlsx） |
| `--retry` | `3` | 失败条目的重试次数。每次重试都会重新打开分析页并重新提交 |
| `--prewarm-standby` | `False` | 实验功能。使用两个持久窗口轮流接力，每次只让当前焦点窗口完整处理一条任务，完成后刷新回分析页，再切到另一个窗口继续（推荐尝试） |
| `--proxy` | 系统代理 | 指定浏览器代理；不传时尝试自动读取系统代理 |
| `--unsafe-parallel-review` | `False` | 允许并发提交 review。理论上更快，但在单代理环境下通常更慢，也更容易触发 Turnstile 重试（已弃用，不推荐） |
| `--submit-interval` | `6` | 受控提交模式下，两次提交之间的最小间隔秒数（已弃用，不推荐） |
| `--submit-cooldown` | `30` | 受控提交模式下，连续失败后的冷却秒数（已弃用，不推荐） |
| `--no-manual-verification` | `False` | 兼容旧脚本保留参数，当前无实际作用（已弃用） |
| `--flare-url` | 无 | 兼容旧脚本保留参数，当前无实际作用（已弃用） |

## 运行模式建议

- 默认模式：单窗口串行，当前最稳。
- `--prewarm-standby`：实验功能。现在表示“双窗口轮转”，不是后台抢跑预热；在单代理环境下不保证比默认串行更快，但值得一试。
- `--unsafe-parallel-review`：不推荐在单系统代理环境下使用，通常会增加 Cloudflare/Turnstile 等待和失败率。

推荐先从默认模式开始：

```bash
python main.py 言乾 --limit 10 --modes 16 --headless
```

如果你要测试实验性的双窗口轮转：

注意：这是实验路径，建议只在你已经拿到默认串行基线后再做对比测试。

```bash
python main.py 言乾 --limit 10 --modes 16 --headless --prewarm-standby
```

## 输出目录

结果默认写入：

```text
results/<nickname>/
```

常见产物包括：

- `results/<nickname>/results.xlsx` 或 `results/<nickname>/results.csv`
- `results/<nickname>/mode_<id>/<uuid>.png`
- `results/<nickname>/mode_<id>/<uuid>_error.png`

## 日志

运行日志会在每条输出前附带当前系统时间，便于定位慢点和错误发生时刻。

## 说明

- `xlsx` 写入逻辑已做批量化优化，但整体耗时通常主要由浏览器提交、Cloudflare Turnstile 和远端分析生成决定。
- 在只有一个系统代理的情况下，多窗口或多线程通常不会线性提速。

## License

MIT
