# WeChatOpenDevTools-Python (Mac ARM)

> ⚠️ 仅供学习研究使用，造成的任何问题与开发者无关；如侵犯到你的权益，请联系删除。

微信小程序 DevTools 解锁 + 本地监控台（Mac ARM）。**单一入口 `main.py`，自动检测微信版本分流**：

```bash
open -a WeChat        # ① 先启动微信（未启动时工具会提示并每 2 秒重试）
python3 main.py       # ② 唯一入口：4.x → direct 独立通道（默认）；3.8.x → 老版注入
```

Dashboard（仅 4.x 流程）：**http://127.0.0.1:8777/**　　老版流程不启动 8777

## 功能

- **vConsole 悬浮调试**：新老版本均支持（强制 versionType=1）
- **监控台（仅 4.x）**：Console / Network（含请求体响应体、Initiator 调用栈）/ wx Storage / 运行时源码 + 断点 / REPL，全部按小程序分应用展示
- **Chrome DevTools 接入（仅 4.x）**：Dashboard 一键打开，Sources / Network / Application 齐全
- **direct 独立通道（4.x 默认）**：完全不需要微信开发者工具

## 适配版本与来源

| 微信版本 | 流程 | 来源 |
|---|---|---|
| **4.1.13.98** / WeChatAppEx 2.5.6.25561（`f2641d62`） | 新版：sniper + direct/bridge + 监控台 | 本仓库实现；direct 传输参考 [netz888/zhong-wechat-wmpf-debugger](https://github.com/netz888/zhong-wechat-wmpf-debugger)，逆向记录见 [docs/WMPF_DIRECT_TRANSPORT_RESEARCH.md](docs/WMPF_DIRECT_TRANSPORT_RESEARCH.md) 与 `cclogs/` |
| **3.8.x**（`13080712` / `13080812` / `13080813` / `13080911` / `13080a11`） | 老版：地址补丁注入（configs/address_*.json + hook.js） | 基于志远的 WeChatOpenDevTool 开源项目，及 JaveleyQAQ、Sndav 的贡献，本仓库为其 Mac ARM 适配版 |

## 使用方法与区别

| | 老版流程（3.8.x） | 新版流程（4.x） |
|---|---|---|
| 启动 | `python3 main.py`（自动识别） | 同左 |
| 前置条件 | 微信已启动 | 微信已启动；**direct（默认）：无其他前置**；bridge（`--mode bridge`）：需微信开发者工具发起「真机调试」 |
| 运行形态 | 前台运行：原版彩色横幅 + 中文注入日志，不启动 8777/Dashboard，Ctrl+C 退出 | 后台托管守护 + Dashboard |
| 能力 | 仅 vConsole（老版 devtools 页） | vConsole + 监控台 + Chrome DevTools |
| 连接触发 | 注入即生效 | 工具或微信重启后需**重开一次小程序**，以触发 direct 原生连接 |
| 指定 pid | `python3 main.py -p <pid> [--version 13080712]` | 不需要（sniper 自动盯进程） |

### 4.x 日常流程

1. 保持微信打开（出过事故就 `pkill -9 WeChatAppEx; sleep 1; pkill WeChat; sleep 2; open -a WeChat`）
2. `python3 main.py`，等 sniper 日志 `armed`
3. 打开目标小程序 → Dashboard 几秒内出现
4. Console / Network / Storage / 源码断点 / REPL 都在 Dashboard 对应 Tab

### 常见问题

| 症状 | 处理 |
|---|---|
| Dashboard 打不开 | 老版流程本来就没有 Dashboard；4.x 下 `python3 main.py` 跑了吗？8777 被占？ |
| 小程序列表空 | direct：重开一次小程序；bridge：真机调试发起了吗（`--mode bridge` 才有桥） |
| 某小程序一直无数据 | 关掉重开（微信会复用没插桩的旧后台进程） |
| 独立模式反复断线 | 重启 `python3 main.py`，重开目标小程序和 DevTools |
| 小程序全体闪退 | 极罕见：`pkill -9 WeChatAppEx; pkill WeChat; open -a WeChat` |
| 看日志 | `/tmp/wechat_rea/managed_*.log`；事件在 `/tmp/wechat_rea/service_events.jsonl` |

### 能力边界

- 注入前的小程序启动期请求会漏（发现与注入存在窗口）
- 断点依赖通道健康；小程序后台挂起时 socket 断，切回前台自动重连
- 未来微信升级：新版地址图谱在 `configs/address_f2641d62_arm.json`，老版在 `configs/address_*.json`，需重新适配

## 依赖

```bash
python3 -m pip install -r requirements.txt -r requirements-direct.txt
```

direct 依赖缺失时自动回落 bridge 模式并提示。
