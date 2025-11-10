# 微信 4.1.4 适配状态报告

## 项目信息

- **项目名称**: WeChatOpenDevTools-Python-arm
- **目标版本**: 微信 4.1.4 (WeChatAppEx 3.8.7 / 0x13080712)
- **工作分支**: dev
- **状态**: ⚠️ 适配未完成，暂时搁置
- **日期**: 2025-11-10

## 问题描述

微信升级到 4.1.4 后，原有的小程序调试工具失效：
- 旧配置文件 `address_13080712_arm.json` 中的地址全部失效
- WeChatAppEx Framework 内存布局已改变
- 小程序中无法显示调试按钮

## 已完成的工作

### ✅ 分析工具（全部可用）

| 工具 | 文件路径 | 功能 |
|------|----------|------|
| 地址查找 | `find_addresses.py` | 搜索内存中的关键字符串 |
| 配置验证 | `verify_config.py` | 验证配置文件有效性 |
| 智能分析 | `smart_analysis.py` | 动态分析函数调用 |
| 强制启用 v1 | `force_enable.py` | 尝试强制启用调试 |
| 强制启用 v2 | `force_enable_v2.py` | 改进版强制启用 |
| 实验性Hook | `experimental_hook.py` | 深度扫描和Hook |

### ✅ Frida 脚本（8个）

- `scripts/find_addresses.js` - 基础地址搜索
- `scripts/find_addresses_v2.js` - 增强版搜索
- `scripts/verify_addresses.js` - 地址验证
- `scripts/smart_hook.js` - 智能Hook监控
- `scripts/force_enable.js` - 强制启用v1
- `scripts/force_enable_v2.js` - 强制启用v2
- `scripts/experimental_hook.js` - 实验性Hook
- `scripts/test_hook_v2.js` - Hook策略测试

### ✅ 文档（4份）

- `CLAUDE.md` - 项目架构说明（main 分支）
- `docs/IDA_ANALYSIS_GUIDE.md` - IDA Pro 详细分析指南
- `docs/WECHAT_414_GUIDE.md` - 完整适配指南
- `docs/DEV_BRANCH_README.md` - Dev 分支说明
- `WECHAT_414_STATUS.md` - 本文档

### ✅ 对话日志（3份）

- `cclogs/2025-11-10-01-创建CLAUDE文档.md`
- `cclogs/2025-11-10-02-适配微信4.1.4版本.md`
- `cclogs/2025-11-10-03-动态分析尝试.md`

## 技术发现

### 找到的地址

| 资源 | 偏移地址 | 状态 |
|------|----------|------|
| enable_vconsole 字符串 | 0x9293A11 | ✅ 已定位 |
| dev_tools 字符串 | 0x8AFCBD1 | ✅ 已定位 |
| xweb-enable-inspect 字符串 | 0x8AFC6D5 | ✅ 已定位 |

### 缺失的资源

| 资源 | 旧版本中的作用 | 状态 |
|------|---------------|------|
| devTools 字符串 | 修改为 DevTools 启用调试菜单 | ❌ 未找到 |
| wechat_app.html URL | 修改为 wechat_web.html 启用浏览器调试 | ❌ 未找到 |
| JsonGetBoolFunc 函数 | 读取布尔配置，强制返回 true | ❌ 未定位 |
| xwebadress 函数 | 启用 xweb inspect 模式 | ❌ 未定位 |

### 测试结果

| 方法 | 结果 | 说明 |
|------|------|------|
| 强制启用 v1 | ❌ 失败 | ObjC 不可用，系统函数 Hook 失败 |
| 强制启用 v2 | ❌ 失败 | Hook 了 2 个函数但未被调用 |
| 实验性 Hook | ❌ 失败 | 脚本卡住，可能扫描超时 |

## 结论

**微信 4.1.4 可能完全改变了调试功能的实现方式**，或者：

1. 调试功能使用了新的触发机制
2. 调试功能被深度隐藏或完全移除
3. 需要额外的前置条件才能激活

动态分析方法在当前版本上**无效**。

## 后续方案

### 方案 A: 静态分析（IDA Pro）✨ 推荐

**适用场景**: 你有 IDA Pro 且有时间深入研究

**步骤**:
1. 参考 `docs/IDA_ANALYSIS_GUIDE.md`
2. 用 IDA Pro 分析 WeChatAppEx Framework
3. 手动查找 4 个关键地址
4. 创建新配置文件

**优点**: 最可靠，能彻底理解新版本实现
**缺点**: 需要 IDA Pro 和逆向工程知识

### 方案 B: 降级微信⚡ 快速

**适用场景**: 急需调试功能，可以接受使用旧版本

**步骤**:
1. 卸载微信 4.1.4
2. 下载安装微信 3.8.x
3. 使用现有配置文件

**优点**: 立即可用
**缺点**: 无法使用新版本功能

### 方案 C: 等待社区方案⏳ 当前选择

**适用场景**: 不急于使用，希望有稳定方案

**行动**:
1. 保留当前 dev 分支的所有成果
2. 关注项目 Issue 和社区动态
3. 等待其他开发者的适配方案
4. 或等待微信更新可能恢复的调试接口

**优点**: 无需投入更多时间
**缺点**: 不确定何时有可用方案

## 给未来的自己/其他开发者

### 如果你想继续研究

1. **从 dev 分支开始**:
   ```bash
   git checkout dev
   ```

2. **运行地址搜索**:
   ```bash
   python3 find_addresses.py
   ```

3. **验证配置**:
   ```bash
   python3 verify_config.py
   ```

4. **参考文档**:
   - IDA 分析: `docs/IDA_ANALYSIS_GUIDE.md`
   - 适配指南: `docs/WECHAT_414_GUIDE.md`

### 可能的突破点

1. **分析其他字符串**:
   - 搜索 "debug"、"developer"、"inspect" 等
   - 可能有新的配置键名

2. **查看 URL Scheme**:
   - 微信可能通过特定 URL 激活调试
   - 尝试抓包分析网络请求

3. **分析 JavaScript 注入**:
   - 检查是否有 JS 层面的调试开关
   - 分析小程序的 JS SDK

4. **寻找开发者选项**:
   - 查找是否有隐藏的开发者菜单
   - 尝试特定的操作序列

### 已知的限制

- WeChatAppEx 版本号不能作为唯一判断依据
- 主应用版本更重要（4.1.4 vs 3.8.x）
- 内存布局可能在每次编译时改变
- 调试功能可能与服务器端配置有关

## 技术栈

- **语言**: Python 3
- **动态分析**: Frida 16.1.11
- **进程管理**: psutil 5.9.7
- **架构**: macOS ARM64
- **目标**: WeChatAppEx Framework (Objective-C/C++)

## 贡献者

- 原始框架: 志远、JaveleyQAQ、Sndav
- 微信 4.1.4 适配尝试: Claude Code (2025-11-10)

## 许可证

本项目仅供学习研究使用。

---

**最后更新**: 2025-11-10
**状态**: 暂时搁置，等待社区方案或后续研究
