# 微信小程序 DevTools 使用手册（4.1.13 · Mac ARM）

## 一句话
```bash
cd ~/devtools/WeChatOpenDevTools-Python-arm
python3 start.py
```
然后浏览器开 **http://127.0.0.1:8777/** —— 这是你唯一的入口，其余全自动。

## 无开发者工具模式（实验）

微信 **4.1.13.98 / WeChatAppEx 2.5.6.25561，Mac ARM64** 可试用独立通道：

```bash
python3 -m pip install -r requirements-direct.txt
python3 start.py --mode direct
```

完全退出微信开发者工具，保持微信打开，等 sniper 日志显示 `armed` 后关闭并重新打开目标小程序。
入口仍是 **http://127.0.0.1:8777/**，左下角显示“独立调试”；DevTools 仍通过 9333 接入，
Console / Network / wx Storage 复用现有 agent。不要同时运行独立的 probe 或第二个 9421 listener。
如果日志一直在等待 WeChatAppEx，先打开一次小程序让该进程出现，再关闭并重新打开目标。
重启本工具后也需重新打开目标小程序，以触发新的原生连接。

独立连接在线时，普通页面跳转会复用连接，避免每次加载都重新建立调试通道。
连接检测由 sniper 每秒续期，后端停止后最多 5 秒失效，下次加载可重新触发连接。
关闭 DevTools 会先停用该会话的 Debugger，再卸载会话，清理其断点；原生连接更换时也会清理已记录的旧会话。
首次更新此修复后建议关闭旧 DevTools、重开目标小程序，清除旧版本遗留的会话。

本机已在开发者工具进程为零时验证独立连接、原生目标发现、service 求值、Console、源码读取、
断点设置和临时测试代码的暂停 / 恢复，也观察到 agent 上线。
指定目标的重开恢复、Network 和 Storage 完整工作流仍需用户验收，暂不作为默认模式。
版本指纹不匹配会拒绝启用独立 hook；切回原流程运行 `python3 start.py --mode bridge`。
详见 [独立传输研究及验收记录](docs/WMPF_DIRECT_TRANSPORT_RESEARCH.md)。

---

## 默认桥模式流程（从微信启动起）

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
在微信里正常打开。后台约每 **2 秒**发现新目标，再完成 agent 注入和直连；桥正常时通常几秒内出现，桥重连或上下文尚未就绪时会更久。
Dashboard 会出现它（真机调试的排第一带"真机调试"徽章，其余按打开顺序）。

### ⑤ 开始用
| 想要 | 在哪 |
|---|---|
| Console / Network / Storage | Dashboard 对应 Tab（按小程序过滤，可清空可搜索） |
| 源码 + 断点 | Dashboard 源码 Tab：点行号设断点；报错栈 `xxx.js:行:列` 可点击跳转 |
| Chrome DevTools | Dashboard 里点该小程序的「DevTools」按钮（Sources 看 JS、断点、Console、**Network 也有**） |
| DevTools 查看小程序 Storage | Application → Local storage → `https://<appid>.wx-storage.invalid`（只读；点击刷新重新读取） |
| vConsole 悬浮球 | 小程序里自带（versionType 强制，无需操作） |
| REPL | Dashboard 控制台底部输入框（断点暂停时=栈帧求值） |

DevTools 首次打开会在页面初始化完成后回放近期已捕获的 Console / Network；打开前已被 agent 捕获的请求不必重新触发。Dashboard 刷新时读取最近 5 分钟、最多约 2 MiB 的事件，之后通过游标分页续读，不再因为单页 200 条的限制漏显示密集事件。

v10 agent 为新请求记录真实调用栈，Network 的 Initiator 可按实际文件和行列位置跳转 Sources。
小程序发布代码可能本身就合并在 `app-service.js`，拆分回原始工程文件需要小程序提供 source map。
历史请求没有调用栈时显示 `Other`，升级后重新触发请求即可。
agent 注入前已经结束的请求仍无法补回。更新 agent 后，重新打开目标小程序可建立一套干净的插桩。

DevTools 的 `wx-storage.invalid` 是本工具提供的存储视图，不会发起真实域名请求，
数据来自当前小程序 service 上下文的 `wx.getStorageInfoSync/getStorageSync`，不是网页的 `localStorage`。
值使用 JSON 文本展示以保留字符串、数字、对象的区别；该视图不支持修改、删除或清空。
读取不沿用 Dashboard 的 120 键 / 单值 2000 字符限制；整个快照超过 4 Mi 字符时明确报错，不显示不完整值。
更新后关闭旧的 DevTools 窗口再打开即可看到该节点，无需重新打开小程序。

独立模式不需要本流程的第 ③ 步。默认 `python3 start.py` 仍使用桥模式。

### 后台工作区

- **会话状态**：显示发现、附加、等待上下文、注入、直连、在线及重试倒计时；连接诊断中可查看 agent 版本、最近心跳、最近事件和失败原因。
- **失败恢复**：按 2 / 4 / 8 / 16 / 30 秒退避，上下文到达后可提前重试；离线会话可手动重试连接。
- **Network**：每个请求合并为一行，支持 URL / 方法 / 状态 / 截断筛选，详情包含请求头、参数、响应与总耗时；支持复制 URL / cURL / 响应体和导出 JSON。
- **Console**：级别与关键词筛选、暂停显示、自动滚动开关、导出日志。暂停显示期间后台继续接收事件。
- **Storage / 源码**：存储可筛选、复制、导出；源码可复制、下载、格式化及设置断点。格式化视图不支持断点，避免使用错误行号。
- **捕获设置**：右上角设置按钮可修改请求体、响应体、头部的字符上限（256 到 1048576）。默认分别为 32768 / 131072 / 16384；保存后推送至在线 v10 agent，新注入的 agent 也会读取保存值。旧钩子热升级的会话会提示重新打开小程序。
- **截断标记**：列表与详情会标明已截断、保留字符数和原始字符数；导出的 JSON 保留截断元数据。原生 DevTools 的截断请求体 / 响应体附带 REA 标记。
- **历史与内存**：事件日志按 16 MiB 轮换，保留 3 份历史，daemon 重启后保留；Dashboard 每个小程序保留最多 500 个请求、约 8 MiB 网络记录和 1000 条日志。超出保留范围会移出较早记录。
- **离线资源**：图标与源码格式化组件随项目提供，后台加载不依赖 CDN。深浅色跟随系统设置，窄屏下请求详情单独展示。

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
| 小程序列表空 | 真机调试发起了吗？小程序打开了吗？检查顶部桥状态；目标约每 2 秒扫描一次 |
| Network 初始为空，后来才有 | 检查 agent 是否已上线；启动期请求可能早于注入。先保持 DevTools 打开，再触发一个确实会发请求的页面操作 |
| 某小程序一直无数据 | 关掉该小程序重开（微信会复用后台旧进程，旧进程上下文没插桩） |
| 捕获设置未生效 | 查看会话顶部的旧钩子提示；重新打开该小程序，建立全新 v10 插桩 |
| DevTools 打不开 JS | 桥可能半死：重启真机调试后重开 DevTools |
| 独立模式页面跳转时反复断线 | 更新后重启 `python3 start.py --mode direct`，重开目标小程序和 DevTools；保留 sniper / direct 日志供定位 |
| 删除断点后仍停在当前行 | 删除不会自动恢复已暂停的执行，按 F8 继续；Dashboard 移除失败会保留断点标记并提示重试 |
| 小程序全体闪退 | sniper 事故率极低但万一：`pkill -9 WeChatAppEx; pkill WeChat; open -a WeChat` |
| 看日志 | `/tmp/wechat_rea/managed_*.log`（四个守护各自的输出） |

## 日志/数据文件

| 文件 | 内容 |
|---|---|
| /tmp/wechat_rea/service_events.jsonl | 所有捕获事件（console/network/storage/route） |
| /tmp/wechat_rea/service_events.jsonl.1 到 .3 | 轮换历史（增量游标可跨文件续读） |
| /tmp/wechat_rea/capture_settings.json | 持久化的抓包字符上限 |
| /tmp/wechat_rea/src_<appid>/ | 运行时源码（与断点行号一致） |
| /tmp/wechat_rea/managed_*.log | 守护日志 |
| cclogs/ | 全部逆向研究记录 |

## 能力边界（诚实版）
- 断点：桥健康时可用（DevTools 原生 + Dashboard 源码 Tab）
- 注入前的小程序启动期请求会漏（目标发现、上下文就绪及注入所需的窗口）
- 小程序后台挂起时 socket 断（切回前台自动重连）
- 未来微信升级：地址图谱在 `configs/address_f2641d62_arm.json`，需重新适配
