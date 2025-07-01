### **注意本库只能作为学习用途, 造成的任何问题与本库开发者无关, 如侵犯到你的权益，请联系删除。**

### **注意本库只能作为学习用途, 造成的任何问题与本库开发者无关, 如侵犯到你的权益，请联系删除。**

### **注意本库只能作为学习用途, 造成的任何问题与本库开发者无关, 如侵犯到你的权益，请联系删除。**

---


## 支持版本列表

> 感谢志远大佬的WeChatOpenDevTool开源、感谢JaveleyQAQ大佬、感谢Sndav大佬，小弟只是新增支持Mac arm架构，重要代码都是上述3位大佬的

| MAC 微信版本 | 小程序版本 | 是否为最新版 | 是否支持内置浏览器f12 |
| ---------------- | ---------- | ------------ | ------------ |
| 3.8.10        | 13080a11   | ✅           | ✅           |
| 3.8.9（28564） | 13080911   | ✅           | ✅           |
| 3.8.8          | 13080813   |  ❌           | ✅           |
| 3.8.8 (28486)  | 13080812   | ❌              | ✅           |
| 3.8.7 (28245)   | 13080712   | ❌            | ❌            |




## 使用方法



1. 安装python3版本

安装依赖

```
pip3  install -r requirements.txt
```

运行✅

```
python main.py 
```
---

### 常见问题
未适配版本，按下述方法ida自行添加，记得做了新地址帮作者补充下😊
> 1、ida打开/Applications/WeChat.app/Contents/MacOS/WeChatAppEx.app/Contents/Frameworks/WeChatAppEx Framework.framework/Versions/C/WeChatAppEx Framework；注意选择arm架构打开
> 
> 2、JsonGetBoolFunc：搜索字符串“enable_vconsole”，找到交叉引用，找到“if ( sub_25F4C88((__int64)v53, "enable_vconsole", 0LL) & 1 )”中的sub_25F4C88地址
> 
> 3、DevToolStringAddr：搜索字符串“closeNetLog”，下面的devTools就只要找的地址
> 
> 4、WechatWebStringAddr：搜索字符串“https://applet-debug.com/devtools/wechat_app.html”
> 
> 5、xwebadress地址：搜索字符串“xweb-enable-inspect”


