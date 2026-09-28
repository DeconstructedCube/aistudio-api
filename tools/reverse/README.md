# Google AI Studio 前端逆向与内省工具

本目录提供针对 Google AI Studio 前端脚本、JSPB Wire 协议及 BotGuard Wasm 签名的分析脚本。

---

## 目录结构

```
tools/reverse/
├── README.md               # 逆向分析指南与切入点
├── reverse_headless.py     # 自动化无头抓取：捕获 JS Bundle、Wasm 模块并执行运行时内省
├── inspect_runtime.py      # CDP Attach 模式：直接内省运行中的 Chrome 页面对象
├── wasm_hook.js            # 浏览器端 WebAssembly 拦截与转储脚本
└── output/                 # [自动生成] 解包并格式化后的静态资源与分析报告
```

---

## 逆向切入点

### 1. 服务单例获取 (`BotGuardService`)
- **当前方式**：在页面初始化时触发轻量提问请求，由 Hook 拦截 snapshot 入参获取 service 单例引用。
- **目标方式**：探查 Angular 根节点（`app-root`）与依赖注入容器（`ng.getInjector()`），直接读取 `BotGuardService` 实例，省去页面 DOM 交互。

### 2. BotGuard Wasm 虚拟机分析 (`dms[snapKey]`)
- **当前方式**：调用前端导出的 `dms[snapKey](service, contentHash)` 计算以 `!` 开头的快照票据。
- **目标方式**：
  - 通过 `wasm_hook.js` 截获 `WebAssembly.instantiate` 获取二进制字节流。
  - 分析导出的核心签名逻辑，评估在独立环境脱离浏览器执行的可行性。

### 3. JSPB Wire 协议结构
- **当前方式**：依据已验证字段映射（`wire_spec.py`）构造请求数组。
- **目标方式**：在解包代码中解析 `MakerSuiteService` 对应的完整 Protobuf 字段定义，补充边缘参数类型。

---

## 使用方法

### 1. 自动化无头分析 (推荐)
启动独立的无头浏览器，注入 Hook，访问 AI Studio 并下载全部静态资源与内存报告（执行完毕后自动关闭进程）：
```bash
uv run python tools/reverse/reverse_headless.py
```
- 输出产物位于 `tools/reverse/output/`。
- 可选参数 `--no-headless` 显示界面，`--timeout 30` 调整加载等待时间。

### 2. 实时内省运行中的实例 (Attach 模式)
在不重启现有浏览器的情况下，连接指定端口进行反射内省：
```bash
uv run python tools/reverse/inspect_runtime.py --port 9222
```
