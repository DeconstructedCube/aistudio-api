# Google AI Studio 反向代理服务 —— 网页版 Gemini 转标准 API

> 源码：https://github.com/DeconstructedCube/aistudio-api

---

## 📌 常见问题（Q&A）

**Q1：这工具是干什么用的？**  
**A1：** Google AI Studio 网页版提供免费的 Gemini 调用，但直接用网页不方便接入各种第三方应用。本项目通过轻量级的无头浏览器（CDP 协议）和页面环境注入技术，将网页调用通道转换为与官方 Gemini REST API 完全兼容的代理服务。配置好后，你可以用 SillyTavern、NextChat 等任何支持 Gemini 的客户端直连，支持 Function Calling、思维链输出和原生生图功能。

**Q2：和其他无头浏览器方案比，优势在哪？**  
**A2：**  
1. **极度轻量**：使用纯 Python 异步 WebSocket 实现原生 Chrome DevTools Protocol（CDP），不依赖臃肿的 Node.js / Playwright 环境。
2. **强力抗指纹拦截**：项目配套了定制版的 Chromium (CloakBrowser)，配合请求级的上下文隔离，能有效绕过 Google BotGuard 的反爬检测。
3. **全平台支持（含手机）**：不仅支持 Linux/Windows/macOS 和 Docker，更是**完美支持安卓 Termux**，自带幂等脚本一键配置 proot 容器，闲置手机也能当服务器跑。

**Q3：目前支持哪些模型？**  
**A3：** 只要 Google AI Studio 目前开放的 Gemini 模型（如 Gemini 1.5 Pro, Flash, Gemini 2.0 等）都会在 `/v1beta/models` 路由中动态发现并支持。

**Q4：多账号怎么处理？**  
**A4：** 内置强大的账号轮询机制。自带轻量化 Web UI（无需额外命令），支持导入多个 Google 账号的 Cookie，支持 Round-Robin、LRU、最少受限等多种轮询策略，突破单账号配额限制。

**Q5：这东西稳吗？**  
**A5：** 因为采用的是在页面内直接执行携带 `withCredentials = true` 的 XHR 请求，天然携带完整的 Cookie 和环境快照，比单纯扒接口发包（RPC）方案稳健得多，大幅度降低了封号与 429 概率。

---

## 🔧 安装与运行

环境要求：Python 3.10+，统一使用 `uv` 管理依赖。

**Linux / macOS / Windows：**
```bash
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api
uv sync
uv run python3 main.py server --port 8080
```

**Android Termux（手机端环境）：**
```bash
pkg update && pkg install -y python git uv proot-distro
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api
uv sync
bash scripts/install_termux_prereqs.sh --project-root "$PWD"
uv run python3 main.py server --port 8080
```

*(也支持 Docker 与 Docker Compose 部署，详见仓库 README)*

---

## 🧾 账号配置与使用

服务启动后，直接在浏览器访问 `http://localhost:8080`，即可进入 Web 控制面板。
在面板中一键粘贴从浏览器开发者工具复制的 Cookie 字符串即可。

**客户端调用示例（与官方 API 完全一致）：**
```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: 你的密钥（可随意填写，或在配置中锁定）" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}'
```

---

## ⚠️ 注意

- 本项目仅供个人学习、安全研究与协议逆向，严禁商业用途。使用者需自行承担账号风控等风险，作者不对此负责。
- **问题反馈**：帖子里发即可，注意不要at，看见谁现场问，维护者不想吃跳蛋😭

---

## 📜 开源协议

**MIT License**
可自由使用，详情见仓库 LICENSE。