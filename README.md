# bilisum

把哔哩哔哩视频整理成**学习笔记**的本地工具。贴一个链接，产出一份带时间戳、保留代码和操作步骤的完整笔记。

全程本地运行：字幕/音频 → 本地语音识别 → 本地大模型整理，**不把任何内容上传到第三方**。Windows 桌面应用，双击即用。

> 非官方项目，与哔哩哔哩无任何关联。

## 它解决什么问题

看技术教程时，真正有价值的东西往往在屏幕和口播里同时出现：口播讲思路，屏幕上是完整的配置文件和代码。普通字幕工具只能拿到前者。

bilisum 的两条内容通道：

- **字幕优先**：有官方/AI 字幕就直接用，几秒出结果
- **画面补充**：下载 1080p 视频流抽帧，用 Windows 自带 OCR 读出屏幕上的代码、终端、PPT 文字
- **语音兜底**：都没有时，本地 faster-whisper 转写

## 免责声明

- 本项目**仅供个人学习与研究使用**，不得用于任何商业用途或内容再分发。
- 工具**不存储、不缓存、不传播**任何视频、音频或字幕内容；下载的音视频流仅用于本地转写，转写完成即删除。
- 工具**不绕过**任何付费、会员或权限限制。需要权益的内容无法获取时，会如实报错。
- 使用时需遵守哔哩哔哩的用户协议与相关法律法规。因使用本工具产生的一切后果由使用者自行承担。
- 项目与哔哩哔哩官方无关，未获其授权或认可。

## 从源码运行

需要 Windows 10/11、Python 3.10+、[uv](https://docs.astral.sh/uv/)、[Ollama](https://ollama.com/download)。

```powershell
git clone <repo-url> bilisum
cd bilisum

# 依赖（国内网络建议加镜像）
uv venv --python 3.12
uv pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -e .

# 拉模型
ollama pull qwen2.5:7b-instruct

# 配置：复制模板后按需修改
copy config.example.toml config.toml

# 运行（独立窗口）
.\.venv\Scripts\pythonw.exe app.py
```

首次建议先在界面「设置」里粘贴 SESSDATA（见[怎么拿](#拿-sessdata)），否则所有视频都会走本地语音识别，慢很多。

## 它是怎么工作的

```
视频链接（BV / av / b23.tv 短链 / 番剧 ep / 分P）
   ↓ 解析
   ↓ 取元数据（标题、UP、时长、简介）
   ├─[优先] 官方字幕 / AI 字幕   ← 需要 SESSDATA
   └─[兜底] dash 音轨 → faster-whisper 本地转写
   ├─[可选] 1080p 视频流 → 抽帧 → 本机 Windows OCR（抓屏幕上的代码/PPT/终端）
   ↓ 断句合并、清洗
   ↓ Ollama 本地模型：分段整理笔记（map）→ 拼接 + 生成知识框架
```

画面提取默认关闭，因为它会多花几十秒。教程、代码、PPT 类视频建议打开，收益很大。

默认输出是**学习笔记**：一句话说明 / 知识框架 / 前置知识 / 完整笔记（按时间分段，保留代码与操作步骤）。也可以切成精简摘要模式。

## 前置准备

1. **Ollama**（必须）：https://ollama.com/download 安装后拉模型
   ```powershell
   ollama pull qwen2.5:7b-instruct
   ```
   > 模型默认存 C 盘。盘紧张的话先把模型目录挪走再 pull：
   > ```powershell
   > setx OLLAMA_MODELS "D:\ollama-models"
   > ```
   > 设完重启 Ollama 服务。

2. **Python 环境**（已在本机装好）：`uv venv --python 3.12` + `uv pip install -e .`

   > 装依赖时如果卡在 pypi：
   > ```powershell
   > uv pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -e .
   > ```
   > 注意 `av` 必须留在 14.x（`av>=14,<15`）。av 19 删掉了 faster-whisper 在用的
   > `metadata_errors` 参数，装成 19 会在转写时报 `TypeError: open() got an unexpected
   > keyword argument 'metadata_errors'`。

3. **SESSDATA**（可选，强烈建议）：不配也能用，但只能走本地语音识别，慢很多且专有名词容易错。
   配置方法：网页里「设置」→ 粘贴 SESSDATA → 保存。或者写 `config.toml`。

## 桌面 App（推荐用法）

双击桌面 **「B站学习笔记」** —— 直接弹出独立窗口，不经过浏览器，关窗即退出并停止后台服务，不留残余进程。

- 安装位置：`%USERPROFILE%\bilisum-app\`（程序 265 MB + 数据 1.4 GB）
- 配置/笔记/模型都在 `%USERPROFILE%\bilisum-app\data\` 下，与开发目录**完全独立**
- 日志：`%USERPROFILE%\bilisum-app\data\app.log`（`--windowed` 没有控制台，出问题先看这里）

### 重新打包 / 更新

```powershell
# 1. 改完代码后重新打包（约 1 分钟，产物 dist\bilisum）
powershell -ExecutionPolicy Bypass -File build.ps1

# 2. 同步到 %USERPROFILE%\bilisum-app 并更新快捷方式（data 目录会保留）
powershell -ExecutionPolicy Bypass -File install.ps1
```

> ⚠️ **这两个 .ps1 必须带 UTF-8 BOM**。PowerShell 5.1 读无 BOM 的脚本会按 GBK 解析，中文字符串会把引号吃掉导致 `TerminatorExpectedAtEndOfString`。

### 打包踩过的坑

| 问题 | 现象 | 处理 |
|---|---|---|
| pywebview 版本 | 装了镜像上的 3.4（2022 年版），配 pythonnet 3.2.1 直接崩，且**异常信息打不出来** | 升到 `pywebview>=5.3`（实际装到 6.2.1） |
| 原生库 | ctranslate2 / av / onnxruntime 都带 DLL | PyInstaller 6.x 自带 hooks 能处理，无需手工收集 |
| uvicorn 协议 | 动态导入，静态分析抓不到 | `--collect-submodules uvicorn` |
| OCR 脚本 | 是外部 .ps1，不会自动打包 | `--add-data "tools/win_ocr_batch.ps1;tools"` |
| 无控制台 | `--windowed` 下 `print` 是 None，报错全丢 | 入口里 `_log()` 同时写 `data\app.log` |

**必须用 onedir（文件夹），不要 onefile**：启动快，且 `_internal` 里的 DLL 结构清晰，出问题好排查。

## 分发给别人用

### 绿色便携包（推荐，对方解压即用）

```powershell
powershell -ExecutionPolicy Bypass -File make-portable.ps1
# 产物：release\bilisum-portable（8.8 GB）
```

包里自带**全部依赖**，对方不需要装任何东西：

| 组成 | 体积 |
|---|---|
| `bilisum.exe` + `_internal` | 276 MB |
| `data\models`（whisper 语音识别） | 1.4 GB |
| `runtime\ollama`（Ollama 运行时） | 2.8 GB |
| `runtime\models`（qwen2.5:7b） | 4.5 GB |

启动时 `app.py` 会检测 `runtime\ollama\ollama.exe`：
- **有** → 用包内的，模型目录指向 `runtime\models`，关窗时一并停掉
- **没有** → 沿用系统已装的 Ollama（如果对方机器上已有，不会冲突）
- 如果 11434 端口已被占用 → 复用现有实例，不重复启动

> 注意：`build.ps1` 只清 `build` 和 `dist\bilisum`，**不会**动 `release` 目录。便携包特意放在 `release\` 就是为了避开重新打包时的清理（早期放在 `dist\` 下被 `Remove-Item dist` 连根删过一次）。

### 分发前必须知道的两件事

**1. 包里绝不能带你的 SESSDATA**
`make-portable.ps1` 有第 6 步专门清理 `data\settings.json`、`app.log`、`notes`、`tmp`、`hf` 缓存，并在结束时校验。如果手动改过包内容，务必再确认一遍。

**2. 体积太大，普通压缩压不动**
模型和 DLL 本身压缩率极低。建议分卷：

```powershell
7z a -v1900m bilisum-portable.7z .\release\bilisum-portable\*
```

或者出**精简版**（1.7 GB，不含 Ollama，对方自己装 1.5GB 安装包 + 拉 4.4GB 模型）：

```powershell
powershell -ExecutionPolicy Bypass -File make-portable.ps1 -SkipOllama
```

### 对方需要做什么

1. 解压到任意目录（路径别带中文和空格更稳）
2. 双击 `bilisum.exe`
3. 按 `使用说明.txt` 配一次 SESSDATA

要求：Windows 10/11（依赖系统自带 WebView2，一般都有）。

## 开发运行（改代码时用）

改代码走开发目录，不用每次重新打包：

```powershell
# 独立窗口模式（推荐）
.\.venv\Scripts\pythonw.exe app.py

# 浏览器模式
.\.venv\Scripts\python.exe -m bilisum.web
```

开发环境的配置/模型在项目目录下的 `data\`，和 app 版各存一份。**改完代码后必须重启**（Python 不热重载），页面顶部有 `stale` 检测会提醒。

命令行也能用（适合批量或调试）：

```powershell
.\.venv\Scripts\python.exe tools\cli.py "https://www.bilibili.com/video/BVxxxx" --save
```

## 分 P / 合集视频

多 P 视频（教程合集、番剧分集等）在输入里指定第几 P，四种写法都认：

```
BV1rpWjevEip p3                            ← 最省事
https://www.bilibili.com/video/BV1rpWjevEip?p=3
BV1rpWjevEip 第3p                          （也认「第p3」）
BV1rpWjevEip?p=3
```

**什么都不写就是第 1 P**。总结一个 100 P 的合集时默认只处理 P1，结果里会明确提示：

> 这是 100 P 的合集，本次只总结第 1 P「【课前篇】Python从0到1学习指南」。要换集就在链接末尾加 ?p=N

一次只处理一个 P，**整个合集批量总结还没做**。

> 注意：`b23.tv` 短链跳到的是 P1，带不了 P 参数。要指定分 P 只能用 BV 号或完整链接。

## 输出形态：学习笔记 / 精简摘要

`output_mode` 控制最终产出，默认 **`note`（完整学习笔记）**：

| 模式 | 产出 | 适合 |
|---|---|---|
| `note`（默认） | 一句话说明 + 知识框架 + 前置知识 + **完整笔记**（按时间分段，保留代码/命令/参数/步骤，不压缩） | 学习、复习、当资料留存 |
| `summary` | 一句话总结 + 5-9 条核心要点 + 内容脉络 + 关键名词 | 只想快速知道这视频讲什么 |

笔记模式的 map 阶段**不做压缩**：每段单独整理成知识点，代码和配置原样保留在 ``` 代码块里；最后用 Python 拼接（不再过一次模型，避免二次压缩丢信息），再由小标题生成知识框架。

同一个 23:24 教程视频、同样开画面 OCR：

| | 精简摘要 `summary` | 学习笔记 `note` |
|---|---|---|
| 输出体量 | 约 2.5 KB | 约 40 KB |
| 内容 | 6 条要点 + 3-6 段脉络 | 19 个知识点小节，含完整 nginx 配置与 Java 代码 |
| 耗时 | 35s | 77s |

笔记模式下**同一主题横跨多个分块会被重复记录**，所以拼装后按内容相似度（Jaccard > 0.62）去掉重复小节；这是必要的，否则 `location/rewrite/proxy_pass` 会被重复写八遍。

## 画面内容提取（OCR）

**解决什么问题**：教程、PPT、代码演示类的视频，关键信息几乎全在屏幕上。字幕只会说"配置 location 和 rewrite"，而完整的 nginx.conf 代码、终端命令、DevTools 请求详情全在画面里，纯文本路径完全拿不到。

**怎么做**：下载 1080p 视频流 → 每 30 秒抽一帧 → 调用**本机 Windows OCR**（`Windows.Media.Ocr`，零额外依赖）→ 去 UI 噪声、去重复帧 → 作为「〔画面〕」段落并入文本，和时间轴对齐后一起总结。

**怎么开**：主界面输入框下方的复选框，勾上即生效（默认关）。命令行加 `--frames`。

实测同一个 23 分钟教程视频：

| | 只用字幕 | 字幕 + 画面 OCR |
|---|---|---|
| 文本量 | 7640 字 | 16764 字 |
| 内容脉络 | 3 段 | **6 段** |
| 耗时 | 11.7s | 35.1s |

画面 OCR 能抓到字幕里根本没有的东西，例如 `rewrite ^/api/(.*)$ /$1 break;`、`proxy_pass http://localhost:8080;`、`location /api/ 表示精确匹配以 /api/ 开头的路径`。

### 局限（都是实测出来的）

- **分辨率是生死线**：360p 下 OCR 完全不可用（"黑马程序员"→"黑马程瘁奚"、代码全是乱码），必须 720p 以上，默认优先 1080p。
- **中文有错字**：WinRT OCR 对中文识别一般（"联调测试"→"朕调测试"），但**代码和英文基本准确**。
- **只认文字，不懂画面**：能读到代码和文字，看不出"他在 IDEA 里点了哪个按钮"这类操作语义。
- **视频流体积**：1080p 对这个静态画面为主的教程只有 24MB，但画面活跃的视频可能几百 MB。
- **耗时**：抽帧 + OCR 约多花 20-40 秒；文本量翻倍后模型总结也变慢。

### 配置

```toml
extract_frames = false      # 默认关
frame_interval = 30         # 每多少秒抽一帧，画面切换快的视频可以降到 15
max_frames = 120            # 抽帧上限，超长视频自动放大间隔
```

抽帧中间产物在 `data/tmp/frames/`，总结完成后自动清理帧图；视频文件按 `bvid-cid` 缓存复用，想省磁盘可以手动删。

## 实测开销（本机 i7-14650HX / 24G 内存，纯 CPU）

| 场景 | 耗时 |
|---|---|
| 6 分钟视频走 ASR（small / int8） | 约 95s（其中转写 ~70s） |
| 同视频第二次（命中转写缓存） | 约 25s（只剩模型总结） |
| 模型总结本身（1158 字 → 结构化摘要） | 约 10s |

转写结果缓存在 `data/tmp/*.cues.json`，同一个视频重复总结不会重新识别；换 `whisper_model` 会重新转写（缓存按模型名区分）。

## 网络相关的坑

- **Python（httpx / uv）不读 Windows 系统代理**，只会读 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量。
  PowerShell 的 `Invoke-WebRequest` 走系统代理能通、而 Python 报超时，就是这个原因。
  本项目对 B站和 Ollama 的请求都设了 `trust_env=False`，避免被代理劫持到本地服务上。
- **whisper 权重默认从 `hf-mirror.com` 拉**（huggingface.co 国内常不可达）。想换源：
  ```powershell
  $env:BILISUM_HF_ENDPOINT = "https://huggingface.co"
  ```
- **`ollama pull` 卡在 99%** 是常见现象（多连接僵死），Ctrl+C 后重跑即可，会断点续传。

## 拿 SESSDATA

1. 浏览器登录 B站
2. F12 → Application（应用）→ Cookies → `https://www.bilibili.com`
3. 找到 `SESSDATA`，复制它的值

也可以整段粘贴浏览器 Cookie 字符串（`SESSDATA=xxx; bili_jct=yyy; ...`），工具会自动提取。

> Cookie 有效期通常几个月，过期后摘要会退化成 ASR，重贴即可。凭证只存在本机 `data/settings.json`。

## 配置项

`config.toml`（长期配置，优先级最低）：

```toml
sessdata = ""                          # 或环境变量 BILISUM_SESSDATA
ollama_base = "http://127.0.0.1:11434"
ollama_model = "qwen2.5:7b-instruct"
whisper_model = "small"                # base / small / medium / large-v3
whisper_device = "cpu"                 # 有 CUDA 环境可改 cuda
whisper_compute = "int8"
chunk_chars = 3200                     # 每段喂给模型的字数
num_ctx = 8192                         # 模型上下文窗口
temperature = 0.25
```

优先级：环境变量 > 网页保存的 `data/settings.json` > `config.toml` > 默认值。

## 目录约定

| 路径 | 内容 |
|---|---|
| `data/settings.json` | 网页里保存的设置（含 SESSDATA） |
| `data/models/` | faster-whisper 权重（不占 C 盘） |
| `data/tmp/` | 临时音频，转写完自动删（`keep_audio = true` 可保留） |
| `data/notes/` | 勾选「保存笔记」时生成的 Markdown |

## 已知限制

- **纯音乐 / 舞蹈 / 无声视频**没有口播，转写为空会直接报错提示。
- **需要权益的视频**（充电专属、大会员）会拿到空音轨，需要带对应账号的 Cookie。
- **B站 AI 字幕质量一般**：断句碎、专有名词错字多，工具做了合并，但原文对比仍有误差。
- **ASR 对口音和嘈杂现场很吃力**：`small` 模型在方言/多人对话场景下错字明显
  （实测把「挑山工」识别成「挑战工」）。这类内容建议 `whisper_model = "medium"` 或配 SESSDATA。
- **超长视频**（2 小时以上）建议配 SESSDATA 走字幕，走 ASR 在 CPU 上会很久。
- 仅供个人学习使用，不要拿转写文本做二次分发。

## 排错

| 现象 | 原因 / 处理 |
|---|---|
| **画面提取报 `[WinError 5] 拒绝访问`** | 已修。原因是抽帧目录名固定（`{bvid}-{cid}`），连点两次或两个任务并行时，一个在写帧、另一个在删目录 → Windows 拒绝访问。现在每次运行用独立目录（带 run_id）并自行清理。若再遇到，完整堆栈在 `data/last_frames_error.log` |
| **改了代码但行为没变** | **服务没重启，跑的还是旧模块**。页面顶部会出现橙色提示「代码已更新，服务还是 xx:xx 启动的旧版本」。杀掉 `pythonw.exe` 重新双击快捷方式即可 |
| `Ollama 不可用` | 没启动服务，运行 `ollama serve`，或确认 `ollama_base` 地址 |
| `model not found` | 没拉模型，`ollama pull qwen2.5:7b-instruct` |
| 摘要质量差、答非所问 | 换 `qwen2.5:7b-instruct` 以上；或把 `chunk_chars` 调小 |
| 字幕一直拿不到 | SESSDATA 过期；或该视频确实没有字幕（未登录时 B站不下发字幕列表） |
| 音频下载失败 | 视频需权益 / CDN 临时失效，重试一次；必要时更新 Cookie |

---

## License

[MIT](LICENSE)

## 致谢

- [Ollama](https://ollama.com/) —— 本地模型运行
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) —— 本地语音识别
- [PyAV](https://github.com/PyAV-Org/PyAV) —— 视频解码与抽帧
- [pywebview](https://pywebview.flowrl.com/) —— 桌面窗口
- Windows Windows.Media.Ocr —— 系统自带 OCR，无需额外依赖