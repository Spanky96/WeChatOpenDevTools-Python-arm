# 微信小程序 DevTools 使用手册（4.1.13 · Mac ARM）

## 一句话
```bash
cd ~/devtools/WeChatOpenDevTools-Python-arm
python3 start.py
```
然后浏览器开 **http://127.0.0.1:8777/** —— 这是你唯一的入口，其余全自动。

---

## 完整流程（从微信启动起）

### ① 启动微信（冷启动场景）
```bash
open -a WeChat
```
- 微信正常启动即可，**不要**先开小程序
- 如果之前出过事故（小程序打不开/闪退），先彻底清一次：
  ```bash
  pkill -9 WeChatAppEx; sleep 1; pkill WeChat; sleep 2; open -a WeChat
  ```

### ② 启动工具（一次）
```bash
python3 start.py
```
自动拉起四个守护（日志在 `/tmp/wechat_rea/managed_*.log`）：
| 守护 | 作用 |
|---|---|
| sniper | 强制 versionType=1（vConsole）+ 调试型出生 |
| cdp_gateway | :9333 DevTools 接入 + **Console/Network 合成注入** |
| service_session | 发现小程序 → 注入 agent → :8779 直连枢纽 |
| (dashboard) | :8777 主界面 |

### ③ 发起真机调试（每次调试会话的开始）
微信开发者工具 → 打开**你有权限的任意项目** → 点「真机调试」
> 这一步创建调试桥（一切能力的源头）。PC 微信会作为"设备"连上。

### ④ 打开你要调试的小程序（任何人的）
在微信里正常打开。**等 ~15 秒**（后台自动：发现 → 注入 → 直连）。
Dashboard 会出现它（真机调试的排第一带"真机调试"徽章，其余按打开顺序）。

### ⑤ 开始用
| 想要 | 在哪 |
|---|---|
| Console / Network / Storage | Dashboard 对应 Tab（按小程序过滤，可清空可搜索） |
| 源码 + 断点 | Dashboard 源码 Tab：点行号设断点；报错栈 `xxx.js:行:列` 可点击跳转 |
| Chrome DevTools | Dashboard 里点该小程序的「DevTools」按钮（Sources 看 JS、断点、Console、**Network 也有**） |
| vConsole 悬浮球 | 小程序里自带（versionType 强制，无需操作） |
| REPL | Dashboard 控制台底部输入框（断点暂停时=栈帧求值） |

---

## 重要心法（桥的脾气）

桥 = 开发者工具里的临时中继，**怕重负载**。记住三条：

1. **Console/Network 捕获不怕桥死**（agent 直连枢纽，注入完成后独立运行）
2. 桥只为「注入 + DevTools + 断点」服务；**DevTools 开着会加速桥衰亡**
3. 桥死了（Dashboard 顶部红字提示 / DevTools 断流）→ **重启真机调试**（10 秒），
   已注入小程序的 console/network 不断流；小程序若重载了，daemon 会自动重注入

## 故障排查

| 症状 | 处理 |
|---|---|
| Dashboard 打不开 | `python3 start.py` 跑了吗？端口 8777 被占？ |
| 小程序列表空 | 真机调试发起了吗？小程序打开了吗？等 15 秒 |
| 某小程序一直无数据 | 关掉该小程序重开（微信会复用后台旧进程，旧进程上下文没插桩） |
| DevTools 打不开 JS | 桥可能半死：重启真机调试后重开 DevTools |
| 小程序全体闪退 | sniper 事故率极低但万一：`pkill -9 WeChatAppEx; pkill WeChat; open -a WeChat` |
| 看日志 | `/tmp/wechat_rea/managed_*.log`（四个守护各自的输出） |

## 日志/数据文件

| 文件 | 内容 |
|---|---|
| /tmp/wechat_rea/service_events.jsonl | 所有捕获事件（console/network/storage/route） |
| /tmp/wechat_rea/src_<appid>/ | 运行时源码（与断点行号一致） |
| /tmp/wechat_rea/managed_*.log | 守护日志 |
| cclogs/ | 全部逆向研究记录 |

## 能力边界（诚实版）
- 断点：桥健康时可用（DevTools 原生 + Dashboard 源码 Tab）
- 注入前的小程序启动期请求会漏（发现的 ~15 秒窗口）
- 小程序后台挂起时 socket 断（切回前台自动重连）
- 未来微信升级：地址图谱在 `configs/address_f2641d62_arm.json`，需重新适配
