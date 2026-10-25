# 微信群机器人（wechat-group-bot）

一个跑在 **Windows + 微信 PC 4.x** 上的个人微信群助手：AI 问答、查询指令、定时推送。
免费、不注入微信进程、不碰协议，全部行为是"模拟真人操作微信窗口"，是当前封号风险最低的个人号方案。

> 仓库：https://github.com/fengwuyun/wechat-group-bot （`dist/` 内含打包好的单文件 exe，可整仓克隆或直接下载该文件）

## 架构

```
微信群 ⇄ 微信PC客户端4.x(小号) ⇄ WxuiChannel(UIAutomation收发)
                                    ⇄ Router(白名单/触发/限流/免打扰/记忆)
                                        ⇄ LLM(Ollama兼容接口, 可mock)  ⇄ Skills(天气/日报/…)
                                        ⇄ APScheduler(定时推送)
```

通道层是接口抽象（`bot/channel.py`）：未来官方 API 开放群聊，或需切换视觉 RPA 方案（omni-bot-sdk）时，只替换这一层。

## 环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10/11 |
| Python | 3.10+（本机 3.13 已验证） |
| 微信 PC | **4.1.x**，语言必须为**简体中文**（UI 元素按中文名定位） |
| 账号 | 一个小号登录 PC 微信（建议有实名/使用记录的老号） |
| 显示 | 电脑不要锁屏睡眠；微信窗口不能最小化到托盘（可被其他窗口遮挡，但不能最小化） |

## 部署步骤

```bat
cd wechat-group-bot
pip install -r requirements.txt
```

### 方式 A：桌面软件（推荐）

二选一：

1. **现成 exe**：双击 `dist\微信群机器人控制台.exe`（独立单文件，无需装 Python 也行）
2. **Python 运行**：双击 `配置面板.bat`

打开后是一个原生窗口（无网页、无服务、无控制台黑窗），所有操作都在窗口里：

- **① 基本设置**：运行通道、机器人昵称、监听群名（每行一个）
- **② AI 大模型**：服务商一键预填（智谱/DeepSeek/Kimi）、API Key、人设提示词，带**测试连接**按钮
- **③ 触发与安全**：@触发、前缀、限流、夜间免打扰、管理员
- **④ 定时推送**：表格化管理任务（添加/编辑/删除）
- **⑤ 运行日志**：实时滚动

顶部 **▶ 启动 / ■ 停止** 一键控制——机器人作为软件的后台线程运行（不弹黑窗口），
关闭窗口时若机器人还在跑会询问是否停止。每次保存自动备份旧配置为 `config.yaml.bak`。

> 想自己重新打包：双击 `build_exe.bat`（需要联网装 PyInstaller）。

### 方式 B：控制台运行 / 手动编辑配置

```bat
copy config.example.yaml config.yaml   &  edit config.yaml
python -m bot.main --config config.yaml
```

编辑 `config.yaml`：

1. `channel.bot_nickname` → 填**小号的微信昵称**（识别 @机器人 用）
2. `channel.groups` → 填要监听的**群名**（必须和微信里显示的完全一致）
3. `llm.*` → 三选一填入（都有免费额度）：
   - 智谱：`base_url: https://open.bigmodel.cn/api/paas/v4`，`model: glm-4-flash`
   - DeepSeek：`base_url: https://api.deepseek.com/v1`，`model: deepseek-chat`
   - 没有_key 也能跑：保持 `mock: true`，全流程可用，只是回复是占位文本

### 首次验证（M1）

```bat
:: 1. 小号扫码登录 PC 微信后，用探针确认程序能看到微信窗口和控件
python -m bot.probe --window 你的群名

:: 2. 先用 mock 通道熟悉指令（不连微信）
python -m bot.main --config config.yaml --mock

:: 3. 真机启动
run.bat    （或 python -m bot.main --config config.yaml）
```

启动后程序会自动为每个监听群打开**独立聊天窗口**（平铺在桌面），然后开始监听。
用另一个号在群里发 `@小号 在吗` 或 `/ping` 测试。日志在 `bot.log`。

## 群内指令

| 指令 | 说明 |
|---|---|
| `@小号 <问题>` | AI 问答（多轮上下文，每群独立记忆） |
| `/问 <问题>` | 同上，不用 @ |
| `/天气 <城市>` | 查天气（vvhan → wttr.in 自动降级） |
| `/日报` | 今日新闻日报 + 农历 + 每日一句 |
| `/帮助` `/ping` | 菜单 / 存活检测 |

## 定时推送

在 `config.yaml` 的 `push.jobs` 里加（cron 字段同 APScheduler）：

```yaml
push:
  jobs:
    - name: 早报
      group: "测试群"
      cron: { hour: 8, minute: 30 }
      skill: daily_news
    - name: 北京天气
      group: "测试群"
      cron: { hour: 7, minute: 0 }
      skill: weather
      args: ["北京"]
```

## 开机自启（可选）

以任务计划程序方式注册（管理员 PowerShell/CMD）：

```bat
schtasks /Create /TN "WeChatGroupBot" /TR "C:\完整路径\wechat-group-bot\run.bat" /SC ONLOGON /DELAY 0001:00
```

并让电脑不休眠：`powercfg /change standby-timeout-ac 0`（或电源设置里手动关睡眠）。
更稳的做法：任务计划程序里勾选"错过计划后尽快启动"+"失败每 1 分钟重试"。

## 风险与注意事项（务必阅读）

- **封号风险不为零**。本方案是所有个人号方案里最低的（不注入、不碰协议、行为=真人操作），
  但微信条款不鼓励任何自动化。请务必：用小号、保持默认限流（8条/分钟）、不要用它加人或群发。
- 微信窗口**不能最小化**，可以遮挡；发送瞬间会抢占一次焦点。
- 微信自动升级可能改变控件结构导致收发失效：重跑 `python -m bot.probe` 采集新控件树即可定位。
- 图片/语音/文件消息 v1 只忽略不处理；只响应文本。
- `config.yaml` 含 API key，不要外传。

## 故障排查

| 现象 | 处理 |
|---|---|
| 启动报"找不到微信主窗口" | 确认微信已登录、语言为简体中文；`python -m bot.probe` 看能否枚举 |
| 报"找不到群" | 群名要和微信显示完全一致；先把群置顶或发条消息让它出现在会话列表 |
| 收不到消息 | 微信是否最小化；`bot.log` 里有无轮询异常；跑 probe 对照控件结构 |
| 回复不发出 | 查 `bot.log`"已发送到"；确认发送瞬间没有鼠标抢占冲突 |

## 文件结构

```
wechat-group-bot/
├─ bot/
│  ├─ main.py          机器人入口/主循环/定时推送（可嵌入 GUI 线程）
│  ├─ config.py        配置加载
│  ├─ router.py        路由：触发/限流/免打扰/记忆
│  ├─ channel.py       通道抽象 + Mock 通道
│  ├─ wxui_channel.py  微信4.x UIAutomation 通道（核心）
│  ├─ llm.py           LLM 客户端（OpenAI 兼容 + mock）
│  ├─ skills.py        技能：天气/日报/帮助
│  ├─ manage.py        管理逻辑：配置读写校验/进程内启停控制器
│  ├─ gui.py           桌面控制台（tkinter 窗口）
│  └─ probe.py         UI 探针（适配排障）
├─ config.example.yaml 配置模板
├─ requirements.txt
├─ selftest.py         离线自测（8 项断言）
├─ gui_entry.py        exe 打包入口
├─ build_exe.bat       一键打包 exe
├─ 配置面板.bat         双击打开桌面控制台（Python 方式）
├─ dist/微信群机器人控制台.exe   打包好的单文件软件
└─ run.bat             控制台方式启动机器人
```
