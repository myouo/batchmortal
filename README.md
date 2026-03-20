# Batch Mortal Analysis (Python/SeleniumBase Edition)

`batchmortal` 是一个基于 Python 编写的自动化离线牌谱批量分析工具。它可以通过查询 [amae-koromo](https://amae-koromo.sapk.ch/) API 自动获取雀魂（Mahjong Soul）玩家历史对局，并自动操控浏览器将牌谱提交至 [Mortal (mjai.ekyu.moe)](https://mjai.ekyu.moe/zh-cn.html) 引擎进行后台 AI 分析评价。

现使用最先进的 `SeleniumBase` (UC 模式) 接管浏览器控制，能够全自动绕过 Cloudflare Turnstile 实体验证，极大地提升了稳定性并降低了配置门槛。

## 主要特性

- **无痛过签**：依靠 SeleniumBase 的 Undetected-Chromedriver 原生模式，丝滑模拟人类操作，全自动隐藏并绕过 Cloudflare 检测。
- **批量爬取与解析**：内置 `acc2match` 算法逆向牌谱链接，支持从多种游戏模式（如 9: 四麻半庄, 12: 四麻东风）里批量拉取记录。
- **全自动模拟提交**：支持填写牌谱、点击高级选项展开、自动勾选“显示 rating”选项并强制等待结果回传提取元数据。
- **智能代理切换**：原生支持检测您当前系统中的局域网/系统代理，以彻底解决大陆网络环境下网页卡加载和过不去验证的问题；支持手动传入 `--proxy`。
- **直观数据导出**：结果自动汇总，可自由导出为 `.xlsx` 格式（带表头）或 `.csv` 格式，此外亦可有选地生成并截取审查结果（Screenshot）。

## 安装依赖

确保你的电脑已经安装了 **Python 3.8+** 以及 **Google Chrome** 浏览器。

```bash
# 推荐使用虚拟环境进行安装
git clone https://github.com/myouo/batchmortal.git
cd batchmortal
pip install -r requirements.txt
```

## 使用说明

**核心命令**：
```bash
python main.py <你的玩家昵称> [选项参数]
```

**示例**：
```bash
# 最常用的情境：查询玩家 "言乾"，拉取最新的1局四麻玉南场（mode=12），生成 xlsx 表格，并且保存分析结果截图（后台静默执行）
python main.py 言乾 --modes 12 --limit 1 --headless --save-screenshot --output xlsx
```

### 完整参数列表

| 标志/参数 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `nickname` | ✅ | `无` | 要查询与提取牌谱的目标雀魂昵称（首个位置参数） |
| `--limit` | ❌ | `10` | 限制每个 mode 拉取对局记录的总数 |
| `--modes` | ❌ | `9` | 9 为四人金南，12 为四人玉南， 16 为四人王座南 |
| `--model-tag`| ❌ | `4.1b` | 指定在 Mortal 引擎中执行检查的网络版本号，例如 '4.1b', '4.1c', '4.0' 等（未测试） |
| `--headless` | ❌ | `False` | 附加此项让 Chrome 在后台隐藏运行（不再弹出前台大窗，推荐打开） |
| `--save-screenshot` | ❌ | `False` | 附加此项以将 Mortal 最终生成的评分页面和详细数据截图保存为 `.png`（会连带展开元数据） |
| `--output` | ❌ | `xlsx` | 可选值为 `xlsx` 或 `csv`。将包含各种一致率和 rating 指标的统计信息导出。 |
| `--proxy` | ❌ | 自动系统级 | 指定强制的代理链接（例：`http://127.0.0.1:7890`）。不传入此参数则自动回退利用系统代理池。 |
| `--dry-run` | ❌ | `False` | 演习模式。不会启动浏览器，只调取 API 并输出所有的 paipuUrl。 |

## 目录与产物

导出的产物将存在同级的目录 `results/<nickname>/` 中。

- `results/<nickname>/results.xlsx`：汇集了本次跑批测试所有的 Rating 评价、AI一致率进度（如 `109/143`），对局长度等。
- `results/<nickname>/mode_<id>/<uuid>.png`：如果您携带了 `--save-screenshot`，这里会生成每一局牌谱审查结束时的定格截图。
- `results/<nickname>/mode_<id>/<uuid>_error.png`：如果有发生超时或执行错误等边缘用例，会自动保存错误状态的屏幕快照辅助 debug。

## 协议

采用 [MIT License](./LICENSE) 授权。
