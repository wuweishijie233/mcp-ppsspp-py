# mcp-ppsspp

[English](README.md) | **中文**

[![PyPI version](https://img.shields.io/pypi/v/mcp-ppsspp.svg)](https://pypi.org/project/mcp-ppsspp/)
[![PyPI downloads](https://img.shields.io/pypi/dm/mcp-ppsspp.svg)](https://pypi.org/project/mcp-ppsspp/)
[![CI](https://github.com/dmang-dev/mcp-ppsspp/actions/workflows/ci.yml/badge.svg)](https://github.com/dmang-dev/mcp-ppsspp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/pypi/l/mcp-ppsspp.svg)](LICENSE)

一个 [MCP](https://modelcontextprotocol.io) 服务器，通过 PPSSPP（PlayStation Portable 模拟器）内置的 WebSocket 调试接口，把它暴露给任何兼容 MCP 的客户端（Claude Desktop、Claude Code 等）。

可以读写 PSP 内存、用按键输入驱动游戏、截图、设置 CPU 断点、查看 MIPS Allegrex 寄存器——全部通过一套干净的工具接口完成。**无需安装任何桥接插件**，PPSSPP 的调试器是模拟器自带的。

## 工作原理

```
+------------------+    stdio     +------------------+   WebSocket    +------------------+
|   MCP 客户端     |   JSON-RPC   |   mcp-ppsspp     |   JSON-RPC     |     PPSSPP       |
| (Claude / 等)    | ===========> |     (Python)     | =============> |    (debugger)    |
+------------------+              +------------------+                +------------------+
```

与 [mcp-bizhawk](https://github.com/dmang-dev/mcp-bizhawk) / [mcp-mgba](https://github.com/dmang-dev/mcp-mgba) 这类需要往模拟器里加载 Lua 插件的桥接不同，PPSSPP 自带调试器 WebSocket 接口——我们只需要对它说 JSON 就行。**不用装插件。**

连接使用 PPSSPP 调试端口的 `debugger.ppsspp.org` 子协议。

## 环境要求

- [PPSSPP](https://www.ppsspp.org/download)（带 WebSocket 调试器的新版本——1.7+）
- **Python 3.10+**
- 在 PPSSPP 中开启 "Allow remote debugger"（允许远程调试）

## 安装

```bash
pip install mcp-ppsspp
```

或者从源码安装：

```bash
pip install -e .
```

或者用 [uv](https://docs.astral.sh/uv/)：

```bash
uvx mcp-ppsspp
```

## 配置 PPSSPP 调试器

1. 启动 PPSSPP，加载任意 PSP ISO/EBOOT
2. **设置 -> 工具 -> 开发者工具 -> 允许远程调试（Allow remote debugger）**（勾选）
3. PPSSPP 会显示当前的主机:端口（例如 `ws://192.168.1.10:12345/debugger`）
4. 记下端口号——稍后要把它设置成 MCP 服务器的环境变量

## 注册到你的 MCP 客户端

### Claude Code（命令行）

```bash
claude mcp add ppsspp --scope user --env PPSSPP_PORT=12345 mcp-ppsspp
```

把 `12345` 换成你实际的端口。验证：

```bash
claude mcp list
# ppsspp: mcp-ppsspp - 已连接
```

### Claude Desktop

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "ppsspp": {
      "command": "mcp-ppsspp",
      "env": { "PPSSPP_PORT": "12345" }
    }
  }
}
```

修改后重启 Claude Desktop。

## 配置项

| 环境变量      | 默认值       | 作用                                        |
|---------------|--------------|---------------------------------------------|
| `PPSSPP_HOST` | `127.0.0.1`  | 要连接的 WebSocket 主机                     |
| `PPSSPP_PORT` | （必填）     | WebSocket 端口——见 PPSSPP 调试器设置       |

## 工具列表

| 工具 | 说明 |
|------|------|
| `ppsspp_ping` | 验证连通性（返回版本号） |
| `ppsspp_get_info` | 游戏标题、光盘 ID、版本、运行状态 |
| `ppsspp_read8` / `ppsspp_read16` / `ppsspp_read32` | 从 PSP 内存读取 u8 / u16-LE / u32-LE |
| `ppsspp_write8` / `ppsspp_write16` / `ppsspp_write32` | 写入 PSP 内存 |
| `ppsspp_read_range` | 以字节数组读取最多 64 KiB |
| `ppsspp_write_range` | 向内存写入字节数组 |
| `ppsspp_read_string` | 读取以 null 结尾的 UTF-8 字符串 |
| `ppsspp_press_buttons` | 设置持续按下的 PSP 按键状态 |
| `ppsspp_press_button` | 按下某键 N 帧后自动释放 |
| `ppsspp_send_analog` | 设置摇杆位置 |
| `ppsspp_pause` / `ppsspp_resume` | 暂停 / 恢复模拟 |
| `ppsspp_step` | 单步执行一条 MIPS 指令 |
| `ppsspp_reset` | 软重置当前游戏 |
| `ppsspp_screenshot` | 截取帧缓冲为内嵌 PNG |
| `ppsspp_get_registers` | 读取全部 MIPS Allegrex 寄存器 |
| `ppsspp_set_register` | 写入单个 MIPS Allegrex 寄存器 |
| `ppsspp_breakpoint_add` / `_remove` / `_list` | CPU 执行断点 |

### PSP 内存布局（速查表）

| 范围                    | 区域                          |
|-------------------------|-------------------------------|
| `0x00010000` - `0x00013FFF` | 暂存内存（快速 16 KiB SRAM） |
| `0x04000000` - `0x041FFFFF` | VRAM（2 MiB GE 显存）        |
| `0x08000000` - `0x087FFFFF` | 内核 RAM（8 MiB，低半区）    |
| `0x08800000` - `0x09FFFFFF` | 用户 RAM（24 MiB，大部分游戏状态都在这里） |
| `0xBC000000+`              | 硬件寄存器                   |

PSP 是**小端**（MIPS Allegrex）。`0x88xxxxxx` 的内核态镜像与 `0x08xxxxxx` 映射到同一块物理内存。

### PSP 按键

`cross`、`circle`、`triangle`、`square`、`up`、`down`、`left`、`right`、`start`、`select`、`ltrigger`、`rtrigger`、`home`。

## 故障排查

| 现象 | 原因 / 解决方法 |
|---|---|
| 启动时提示 `PPSSPP_PORT must be set` | 把环境变量设为 PPSSPP「开发者工具」对话框里显示的端口 |
| `WebSocket connection failed` | PPSSPP 没在运行、"Allow remote debugger" 没勾选，或端口不对 |
| 工具调用卡住 / 超时 | 确认 PPSSPP 界面有响应；WebSocket 请求需要 PPSSPP 主循环来分发 |
| 内存操作报 `Invalid address` | 地址超出 PSP 映射区域（用户 RAM 是 `0x08800000+`，不是 `0x00000000+`） |
| 截图没有数据 | 没有加载游戏——先启动一个 ISO/EBOOT |
| 按键似乎没反应 | PPSSPP 的输入可能有按键但远程输入在游戏快速轮询时"手感"不对；试试把 `ppsspp_press_button` 的 `duration` 调大 |

## 已知限制

- **没有存档 API**——PPSSPP 的 WebSocket 调试器不提供 `savestate.save` / `load`。目前请用 PPSSPP 的快捷键（槽位一般是 F1-F8）。也可以尝试用 `input.buttons.press` 触发按键绑定，但这不是原生方案。
- **逐帧推进只有指令级**（`cpu.stepInto`）。要推进一整帧，可以在 vblank 处理器处设断点再 `resume`。
- **摇杆是共享状态**——`ppsspp_send_analog` 更新的是持续摇杆位置，不会自动回中。

## 开发

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -e ".[dev]"
pytest                        # 运行单元测试 + stdio 端到端测试
```

测试套件使用进程内的假 PPSSPP WebSocket 服务器，所以**不装 PPSSPP 也能跑**。`scripts/` 里的实机验证脚本需要真实 PPSSPP 实例：

```bash
PPSSPP_PORT=12345 python scripts/smoke.py
PPSSPP_PORT=12345 python scripts/verify_reconnect.py
PPSSPP_PORT=12345 python scripts/verify_screenshot.py
```

## 用 MCP Inspector 调试

用 [MCP Inspector](https://github.com/modelcontextprotocol/inspector) 交互式浏览和调用本服务器的工具：

```bash
PPSSPP_PORT=<端口> npx @modelcontextprotocol/inspector mcp-ppsspp
```

`mcp-ppsspp` 没有默认端口——请从 PPSSPP 的**开发者工具 -> 允许远程调试**对话框读取当前端口，并作为 `PPSSPP_PORT` 传入。即使没连 PPSSPP，`tools/list` 也能用；但*调用*工具需要 PPSSPP 正在运行且开启了远程调试。

## 许可证

[MIT](LICENSE)
