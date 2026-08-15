"""MCP tool definitions and handlers for mcp-ppsspp.

Tool descriptions are written to the TDQS rubric (Glama's Tool Definition
Quality Score). Each description covers, in order:

  - PURPOSE  - one clear action sentence.
  - USAGE    - when to use this vs sibling tools.
  - BEHAVIOR - side effects, error conditions, destructive notes.
  - RETURNS  - exact shape of the success output.

Each parameter has a `description` that adds context beyond the schema
(address-space conventions, alignment, button names, examples).
"""

from __future__ import annotations

import base64
import json
import re
from typing import Annotated, Any, Dict, List, Literal, Union

from pydantic import Field

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

from mcp_ppsspp.ppsspp import PpssppClient, PpssppError

# Canonical PSP button names PPSSPP's input.buttons.send understands.
PSP_BUTTONS = [
    "cross", "circle", "triangle", "square",  # Face buttons
    "up", "down", "left", "right",            # D-pad
    "start", "select",                        # System
    "ltrigger", "rtrigger",                   # Shoulder buttons
    "home",                                   # Home
]

BUTTONS_JOINED = ", ".join(PSP_BUTTONS)

ADDRESS_PARAM_DESC = (
    "PSP physical address. PSP memory layout: user RAM starts at 0x08800000 "
    "(or 0x08000000 - varies by firmware allocation), kernel RAM at 0x08000000-0x087FFFFF, "
    "VRAM at 0x04000000-0x041FFFFF, scratchpad at 0x00010000-0x00013FFF, hardware regs "
    "at 0xBC000000+. Most game state lives in user RAM. Note PPSSPP may also accept "
    "0x88xxxxxx kernel-mode mirrors of the same physical memory."
)


def _addr_hex(n: int) -> str:
    return f"0x{n:08X}"


def _fmt_hex(n: Any) -> str:
    if isinstance(n, int):
        return f"{n} (0x{n:X})"
    return str(n)


def register_tools(mcp: FastMCP, pp: PpssppClient) -> None:
    """Register every PPSSPP MCP tool on the given FastMCP server."""

    # ---- Connectivity & introspection --------------------------------------

    @mcp.tool()
    async def ppsspp_ping():
        """PURPOSE: Verify that the PPSSPP WebSocket debugger is reachable and responding. USAGE: Call once at start-of-session before any other tool calls; if it succeeds, the WebSocket handshake worked and PPSSPP's debugger is available. BEHAVIOR: No side effects - calls the 'version' event to learn PPSSPP's release version. Times out after ~10 seconds if PPSSPP isn't running, doesn't have 'Allow remote debugger' enabled (Settings -> Tools -> Developer Tools), or the host:port isn't reachable. RETURNS: Single line 'pong (PPSSPP VERSION)'."""
        r = await pp.call("version")
        return f"pong ({r.get('name', 'PPSSPP')} {r.get('version', '(unknown version)')})"

    @mcp.tool()
    async def ppsspp_get_info():
        """PURPOSE: Get the loaded game's title, disc ID, and version, plus PPSSPP's run state. USAGE: Call after ppsspp_ping to learn what game is loaded and whether emulation is currently running or stepping. BEHAVIOR: No side effects - pure read. Returns 'no game loaded' fields if PPSSPP is at the home menu / not currently emulating. RETURNS: Multi-line text with Title, Disc ID, Version, and run state (running / paused / stepping)."""
        status = await pp.call("game.status")
        lines: List[str] = []
        game = status.get("game")
        if game:
            lines.append(f"Title:   {game.get('title', '(unavailable)')}")
            lines.append(f"Disc ID: {game.get('id', '(unavailable)')}")
            lines.append(f"Version: {game.get('version', '(unavailable)')}")
        else:
            lines.append("No game loaded.")
        state = "stepping (paused)" if status.get("stepping") else ("paused" if status.get("paused") else "running")
        lines.append(f"State:   {state}")
        return "\n".join(lines)

    # ---- Memory reads ------------------------------------------------------

    @mcp.tool()
    async def ppsspp_read8(
        address: Annotated[int, Field(ge=0, description=ADDRESS_PARAM_DESC)],
    ):
        """PURPOSE: Read an unsigned 8-bit byte from PSP memory at the given physical address. USAGE: Use for single-byte status flags, counters, and 8-bit fields. For 16/32-bit values use ppsspp_read16/read32 (one call instead of multi-byte assembly); for spans use ppsspp_read_range. BEHAVIOR: No side effects - pure read. Returns an error if the address isn't a valid PSP memory address (PPSSPP validates against the PSP's mapped regions). RETURNS: Single line 'ADDR_HEX: VAL_DEC (0xVAL_HEX)'."""
        r = await pp.call("memory.read_u8", {"address": address})
        return f"{_addr_hex(address)}: {_fmt_hex(r.get('value'))}"

    @mcp.tool()
    async def ppsspp_read16(
        address: Annotated[int, Field(ge=0, description=ADDRESS_PARAM_DESC)],
    ):
        """PURPOSE: Read an unsigned 16-bit little-endian value from PSP memory at the given physical address. USAGE: Use for 16-bit game-state fields (most counters, IDs, small numerics). For single bytes use ppsspp_read8; for 32-bit use ppsspp_read32; for arbitrary byte spans use ppsspp_read_range. BEHAVIOR: No side effects - pure read. PSP is little-endian (MIPS Allegrex). Returns an error if address+2 exceeds the valid memory region. RETURNS: Single line 'ADDR_HEX: VAL_DEC (0xVAL_HEX)'."""
        r = await pp.call("memory.read_u16", {"address": address})
        return f"{_addr_hex(address)}: {_fmt_hex(r.get('value'))}"

    @mcp.tool()
    async def ppsspp_read32(
        address: Annotated[int, Field(ge=0, description=ADDRESS_PARAM_DESC)],
    ):
        """PURPOSE: Read an unsigned 32-bit little-endian value from PSP memory at the given physical address. USAGE: Use for 32-bit fields - timestamps, large counters, pointers, RGBA colors. For 8/16-bit use ppsspp_read8/read16; for spans use ppsspp_read_range. BEHAVIOR: No side effects - pure read. PSP is little-endian. Returns an error if address+4 exceeds the valid memory region. RETURNS: Single line 'ADDR_HEX: VAL_DEC (0xVAL_HEX)'."""
        r = await pp.call("memory.read_u32", {"address": address})
        return f"{_addr_hex(address)}: {_fmt_hex(r.get('value'))}"

    @mcp.tool()
    async def ppsspp_read_range(
        address: Annotated[int, Field(ge=0, description=ADDRESS_PARAM_DESC)],
        size: Annotated[int, Field(ge=1, le=65536, description="Number of bytes to read (1-65536). Larger reads work but produce big responses.")],
    ):
        """PURPOSE: Read a contiguous range of bytes from PSP memory and return as a hex dump. USAGE: Use whenever you need more than ~4 bytes - one round-trip vs N typed reads. PPSSPP returns the data base64-encoded over the wire; this tool decodes and formats as space-separated hex bytes. No hard size limit from the WebSocket but stay reasonable (<=16 KiB per call) for response sizes. BEHAVIOR: No side effects - pure read. Reads `size` consecutive bytes starting at `address`. Returns an error if any byte in the range is outside the valid PSP memory map. RETURNS: 'ADDR_HEX [N bytes]:' header + space-separated 2-digit uppercase hex bytes."""
        r = await pp.call("memory.read", {"address": address, "size": size})
        data = base64.b64decode(r.get("base64") or "")
        hexdump = " ".join(f"{b:02X}" for b in data)
        return f"{_addr_hex(address)} [{len(data)} bytes]:\n{hexdump}"

    @mcp.tool()
    async def ppsspp_read_string(
        address: Annotated[int, Field(ge=0, description=ADDRESS_PARAM_DESC)],
    ):
        """PURPOSE: Read a null-terminated UTF-8 string from PSP memory at the given address. USAGE: Use for in-game text, character names, dialogue, file names - anywhere the PSP stores a C-style null-terminated string. Stops at the first 0x00 byte. BEHAVIOR: No side effects - pure read. Reads bytes until null terminator, decodes as UTF-8. Returns an error if the address is outside valid memory, or if the string runs past valid memory before hitting a null. RETURNS: Single line 'ADDR_HEX: \"STRING\"'."""
        r = await pp.call("memory.readString", {"address": address, "type": "utf-8"})
        return f"{_addr_hex(address)}: {json.dumps(r.get('value') or '')}"

    # ---- Memory writes -----------------------------------------------------

    @mcp.tool()
    async def ppsspp_write8(
        address: Annotated[int, Field(ge=0, description=ADDRESS_PARAM_DESC)],
        value: Annotated[int, Field(ge=0, le=255, description="Byte value (0-255).")],
    ):
        """PURPOSE: Write an unsigned byte (0-255) to PSP memory at the given physical address. USAGE: Use for single-byte cheats, debug pokes, game-state mutations. For 16/32-bit use ppsspp_write16/write32; for spans use ppsspp_write_range. BEHAVIOR: DESTRUCTIVE: overwrites whatever was at `address` with no undo. Direct memory write - no hardware mediation. Returns an error if the address is outside valid memory or value > 255. RETURNS: Single line 'Wrote VAL -> ADDR_HEX'."""
        await pp.call("memory.write_u8", {"address": address, "value": value})
        return f"Wrote {_fmt_hex(value)} -> {_addr_hex(address)}"

    @mcp.tool()
    async def ppsspp_write16(
        address: Annotated[int, Field(ge=0, description=ADDRESS_PARAM_DESC)],
        value: Annotated[int, Field(ge=0, le=65535, description="16-bit value (0-65535).")],
    ):
        """PURPOSE: Write an unsigned 16-bit little-endian value to PSP memory. USAGE: Use for 16-bit cheats and pokes (HP, score, coordinates). For single bytes use ppsspp_write8; for 32/larger use ppsspp_write32/write_range. BEHAVIOR: DESTRUCTIVE: overwrites two bytes with no undo. PSP is little-endian (low byte at `address`, high at address+1). Returns an error if address+2 exceeds valid memory or value > 65535. RETURNS: Single line 'Wrote VAL -> ADDR_HEX'."""
        await pp.call("memory.write_u16", {"address": address, "value": value})
        return f"Wrote {_fmt_hex(value)} -> {_addr_hex(address)}"

    @mcp.tool()
    async def ppsspp_write32(
        address: Annotated[int, Field(ge=0, description=ADDRESS_PARAM_DESC)],
        value: Annotated[int, Field(ge=0, le=4294967295, description="32-bit value (0-4294967295).")],
    ):
        """PURPOSE: Write an unsigned 32-bit little-endian value to PSP memory. USAGE: Use for 32-bit cheats and pokes - timestamps, large counters, pointers. For 8/16-bit use ppsspp_write8/write16; for spans use ppsspp_write_range. BEHAVIOR: DESTRUCTIVE: overwrites four bytes with no undo. PSP is little-endian. Returns an error if address+4 exceeds valid memory or value > 4294967295. RETURNS: Single line 'Wrote VAL -> ADDR_HEX'."""
        await pp.call("memory.write_u32", {"address": address, "value": value})
        return f"Wrote {_fmt_hex(value)} -> {_addr_hex(address)}"

    @mcp.tool()
    async def ppsspp_write_range(
        address: Annotated[int, Field(ge=0, description=ADDRESS_PARAM_DESC)],
        bytes: Annotated[List[int], Field(min_length=1, max_length=65536, description="Byte values (each 0-255), written sequentially from `address`.")],
    ):
        """PURPOSE: Write a contiguous byte sequence to PSP memory starting at the given address. USAGE: Use for installing cheat tables, patching code blocks, or seeding regions. Bytes are sent base64-encoded over the wire. BEHAVIOR: DESTRUCTIVE: overwrites N bytes with no undo. Direct memory write. Returns an error if address+N exceeds valid memory or any byte value is outside 0-255. RETURNS: Single line 'Wrote N bytes -> ADDR_HEX'."""
        payload = bytearray(bytes)
        await pp.call("memory.write", {"address": address, "base64": base64.b64encode(payload).decode("ascii")})
        return f"Wrote {len(payload)} bytes -> {_addr_hex(address)}"

    # ---- Input -------------------------------------------------------------

    @mcp.tool()
    async def ppsspp_press_buttons(
        buttons: Annotated[Dict[str, bool], Field(description=f"Map of PSP button name -> pressed (boolean). Valid names: {BUTTONS_JOINED}. Example: {{\"cross\": true, \"right\": true}} holds X and Right.")],
    ):
        """PURPOSE: Set the PSP joypad button state - the buttons in the map are 'held' until you send another buttons command. USAGE: Drive games with input. Unlike one-frame-only schemes on other emulators, PPSSPP's input.buttons.send updates the persistent button state - the buttons stay held until you call ppsspp_press_buttons again with them set false (or use ppsspp_press_button for a timed one-shot). To release all buttons, call with all keys set to false. BEHAVIOR: Modifies emulator input state until changed. PSP buttons (case-sensitive): cross, circle, triangle, square, up, down, left, right, start, select, ltrigger, rtrigger, home. Unrecognized button names return an error. RETURNS: Single line 'Set buttons: BUTTON+BUTTON+...' or '... (all released)' if nothing was pressed."""
        await pp.call("input.buttons.send", {"buttons": buttons})
        pressed = [k for k, v in buttons.items() if v]
        return f"Set buttons: {'+'.join(pressed) if pressed else '(all released)'}"

    @mcp.tool()
    async def ppsspp_press_button(
        button: Annotated[str, Field(description=f"PSP button name. Valid: {BUTTONS_JOINED}.")],
        duration: Annotated[int, Field(ge=1, description="Number of frames to hold the button before releasing (default 1).")] = 1,
    ):
        """PURPOSE: Press a PSP button for a fixed number of frames, then auto-release. USAGE: Use for discrete actions like pressing Start to skip a cutscene, or Cross to confirm a menu. For longer holds across many frames use ppsspp_press_buttons (persistent state) instead. BEHAVIOR: Modifies emulator input state. PPSSPP queues the press internally and releases the button after `duration` frames; the tool call returns immediately. Returns an error if the button name isn't recognized. RETURNS: Single line 'Pressed BUTTON for N frames (auto-released)'."""
        await pp.call("input.buttons.press", {"button": button, "duration": duration})
        return f"Pressed {button} for {duration} frames (auto-released)"

    @mcp.tool()
    async def ppsspp_send_analog(
        stick: Literal["left", "right"],
        x: Annotated[float, Field(ge=-1, le=1, description="Horizontal axis. -1 = full left, 0 = center, 1 = full right.")],
        y: Annotated[float, Field(ge=-1, le=1, description="Vertical axis. -1 = full down, 0 = center, 1 = full up.")],
    ):
        """PURPOSE: Set the PSP analog stick state (one of left/right; the PSP only has one stick natively but PPSSPP exposes both for forward-compat). USAGE: Drive games that need analog input - character movement, camera control. X and Y are signed in [-1.0, 1.0]; (0, 0) = neutral, (1, 0) = full right, (0, -1) = full up. BEHAVIOR: Modifies emulator analog input. State persists until updated. RETURNS: Single line 'Set analog stick STICK to (X, Y)'."""
        await pp.call("input.analog.send", {"stick": stick, "x": x, "y": y})
        return f"Set analog stick {stick} to ({x}, {y})"

    # ---- Emulator control --------------------------------------------------

    @mcp.tool()
    async def ppsspp_pause():
        """PURPOSE: Pause PSP emulation (the debugger calls this 'stepping mode'). USAGE: Use before a sequence of memory inspects when you need a stable game state across calls. Memory r/w tool calls still work while paused. Use ppsspp_resume to continue. BEHAVIOR: Modifies emulator run state. Pauses the MIPS CPU; rendering may continue at last frame. Idempotent - pausing already-paused is a no-op. RETURNS: Single line 'Emulation paused'."""
        # cpu.stepping is fire-and-forget per PPSSPP source ("No immediate
        # response. Once CPU is stepping, a 'cpu.stepping' event will be
        # sent."). Send it, then poll cpu.status until stepping=true.
        await pp.fire_and_forget("cpu.stepping")
        await pp.wait_for_state(lambda s: s.get("stepping") is True)
        return "Emulation paused"

    @mcp.tool()
    async def ppsspp_resume():
        """PURPOSE: Resume PSP emulation from a paused/stepping state. USAGE: Counterpart to ppsspp_pause. Use after a paused inspection sequence. To step a single frame instead, use ppsspp_step. BEHAVIOR: Modifies emulator run state. Idempotent - resuming already-running is a no-op. RETURNS: Single line 'Emulation resumed'."""
        await pp.fire_and_forget("cpu.resume")
        await pp.wait_for_state(lambda s: s.get("stepping") is False)
        return "Emulation resumed"

    @mcp.tool()
    async def ppsspp_step():
        """PURPOSE: Step the MIPS CPU forward by ONE instruction (cpu.stepInto). USAGE: For instruction-level debugging - set a breakpoint, hit it, then step. NOT a frame-advance - one MIPS instruction is much smaller than one frame. To advance a frame's worth of execution, set a breakpoint at the start of the next frame's render and use ppsspp_resume. BEHAVIOR: Modifies emulator run state. Executes one MIPS instruction, then returns to stepping mode. Returns an error if emulation isn't currently in stepping mode (call ppsspp_pause first). RETURNS: Single line 'Stepped one instruction. PC: 0xADDR'."""
        r = await pp.call("cpu.stepInto")
        pc = r.get("pc")
        return f"Stepped one instruction. PC: {_addr_hex(pc) if pc is not None else '(unknown)'}"

    @mcp.tool()
    async def ppsspp_reset():
        """PURPOSE: Reset the loaded PSP game - equivalent to soft-resetting the console. USAGE: Use to start fresh from the game's intro. To return to a specific point, set up a savestate via PPSSPP's UI and load it (savestate API is not in the WebSocket interface, so this must be done via PPSSPP's keybinds - typically F1-F8 for slots). BEHAVIOR: DESTRUCTIVE: RAM contents cleared, CPU returns to game entry point, framecount/game-state lost. The ISO/EBOOT stays loaded. RETURNS: Single line 'Game reset'."""
        await pp.call("game.reset")
        return "Game reset"

    @mcp.tool()
    async def ppsspp_screenshot(
        source: Literal["render", "output"] = "render",
    ):
        """PURPOSE: Capture the current PSP framebuffer as a PNG-encoded screenshot. USAGE: For visual inspection or sequence documentation. Default 'render' source reads the active GPU render target - safer, native 480x272, what the PSP CPU asked the GPU to draw. Opt-in 'output' source reads PPSSPP's final composited output (post scaling/shaders) but can crash PPSSPP on games whose output framebuffer state confuses GPU_GetOutputFramebuffer (a real upstream bug - an _assert_ that should be a graceful failure). Prefer 'render' unless you specifically need the post-processed image. BEHAVIOR: Transparently pauses the CPU (cpu.stepping), captures, then resumes - both PPSSPP buffer events require stepping. If the emulator was already paused, leaves it paused. Returns an error if no game is loaded. The 'output' source CAN crash PPSSPP on certain games; if it does, MCP auto-reconnects to the relaunched PPSSPP cleanly. RETURNS: Text confirmation + inline PNG image block."""
        # PPSSPP's gpu.buffer.* events all require CORE_STEPPING_CPU (or GPU
        # stepping) state - they fail with "Neither CPU or GPU is stepping"
        # otherwise. We transparently pause->capture->resume so callers can
        # screenshot any time without managing pause state. If the emulator
        # was already paused, we leave it paused.
        #
        # source='render' (default) uses gpu.buffer.renderColor -> reads the
        # active GPU render target. Safer: GPU_GetCurrentFramebuffer hits a
        # different code path than the crash-prone GPU_GetOutputFramebuffer.
        #
        # source='output' uses gpu.buffer.screenshot -> reads the final
        # composited output (what's on screen, post scaling/shaders). Can
        # CRASH PPSSPP on some games: upstream has an `_assert_(buf != nullptr)`
        # after GPU_GetOutputFramebuffer that fires when the function returns
        # true with a null buffer (observed on some homebrew). We can't catch
        # a process abort from outside, but v0.1.2's auto-reconnect means MCP
        # recovers when PPSSPP is relaunched.
        event = "gpu.buffer.screenshot" if source == "output" else "gpu.buffer.renderColor"
        status_before = await pp.call("cpu.status")
        was_stepping = bool(status_before.get("stepping"))
        if not was_stepping:
            await pp.fire_and_forget("cpu.stepping")
            await pp.wait_for_state(lambda s: s.get("stepping") is True)
        try:
            # type: "base64" returns the raw base64 payload; the default "uri"
            # returns a "data:image/png;base64,..." prefix which we'd have to strip.
            r = await pp.call(event, {"type": "base64"})
            b64 = r.get("base64")
            if not b64 and r.get("uri"):
                # Belt-and-suspenders: if PPSSPP returned a URI anyway, strip the prefix.
                m = re.match(r"^data:image/png;base64,(.*)$", r["uri"])
                if m:
                    b64 = m.group(1)
            if not b64:
                raise PpssppError(
                    f"PPSSPP did not return screenshot data from {event} "
                    "(no game loaded, or framebuffer not readable?)"
                )
            return [
                f"Screenshot captured (source: {source}, event: {event}).",
                Image(data=base64.b64decode(b64), format="png"),
            ]
        finally:
            if not was_stepping:
                try:
                    await pp.fire_and_forget("cpu.resume")
                    await pp.wait_for_state(lambda s: s.get("stepping") is False, timeout_ms=2000)
                except Exception:
                    pass  # best-effort

    # ---- CPU / debugger ----------------------------------------------------

    @mcp.tool()
    async def ppsspp_get_registers():
        """PURPOSE: Read all MIPS Allegrex CPU registers (general-purpose + FPU + special). USAGE: For reverse engineering and debugging - inspect function arguments, return values, PC, stack pointer. PSP's calling convention puts args in $a0-$a3, return in $v0, stack in $sp, return address in $ra. BEHAVIOR: No side effects - pure read. Most informative when called while emulation is paused (ppsspp_pause first); on a running CPU the snapshot is from whenever PPSSPP samples it. RETURNS: Multi-line text with all register names + hex values, grouped by class (GPR, FPU, special)."""
        # PPSSPP's cpu.getAllRegs returns categories with PARALLEL arrays:
        #   { categories: [{ name, registerNames: [...], uintValues: [...], floatValues: [...] }] }
        # Not an array of {name, value} objects as I first assumed.
        r = await pp.call("cpu.getAllRegs")
        lines: List[str] = []
        for cat in r.get("categories") or []:
            lines.append(f"-- {cat.get('name', '')} --")
            names = cat.get("registerNames") or []
            vals = cat.get("uintValues") or []
            for i in range(max(len(names), len(vals))):
                nm = names[i] if i < len(names) else f"r{i}"
                v = vals[i] if i < len(vals) else None
                lines.append(f"  {nm:<8} = {_addr_hex(v) if v is not None else '(unavailable)'}")
        return "\n".join(lines) or "(no registers returned)"

    @mcp.tool()
    async def ppsspp_set_register(
        register: Annotated[str, Field(description="Register name as shown by ppsspp_get_registers - e.g. 'pc', 'v0', 'a0', 'sp', 'ra', 'f0'. Case-insensitive per PPSSPP.")],
        value: Annotated[Union[int, str], Field(description="New value. A JSON integer for the common case (32-bit; encode signed values as two's-complement uint), or a string for hex ('0x1F'), float ('1.5'), or special ('nan','inf','-inf') forms - PPSSPP parses all of these.")],
    ):
        """PURPOSE: Write a single MIPS Allegrex CPU register - set a GPR, PC, HI/LO, or FPU register to a new value. USAGE: The write counterpart to ppsspp_get_registers. Use during debugging to redirect execution (set `pc`), fix a return value (`v0`), patch an argument before a call (`a0`-`a3`), adjust the stack pointer (`sp`), or poke an FPU register. BEST DONE WHILE PAUSED (ppsspp_pause, or stopped at a breakpoint) - writing a register on a running CPU races the next instruction that overwrites it. Register names match ppsspp_get_registers output (GPRs zero/at/v0/v1/a0-a3/t0-t9/s0-s7/k0/k1/gp/sp/fp/ra, special pc/hi/lo, FPU f0-f31). BEHAVIOR: DESTRUCTIVE to CPU state - no undo (snapshot via ppsspp_get_registers first if you need the old value). Maps to PPSSPP's cpu.setReg. Returns an error if the register name is unknown or no game is loaded. RETURNS: Single line confirming the register and the value written."""
        r = await pp.call("cpu.setReg", {"name": register, "value": value})
        confirmed = (
            _addr_hex(r["uintValue"])
            if "uintValue" in r
            else (r["floatValue"] if "floatValue" in r else str(value))
        )
        return f"Set {register} = {confirmed}"

    # ---- Breakpoints -------------------------------------------------------

    @mcp.tool()
    async def ppsspp_breakpoint_add(
        address: Annotated[int, Field(ge=0, description="PSP execution address. Usually in user RAM (0x08800000+) or kernel RAM.")],
    ):
        """PURPOSE: Add a CPU execution breakpoint at the given PSP physical address. Emulation halts when PC reaches that address. USAGE: For RE work and HLE intercepts. Combine with ppsspp_resume + (later) ppsspp_get_registers to inspect state at the breakpoint. BEHAVIOR: Modifies PPSSPP's breakpoint table. The breakpoint persists until removed via ppsspp_breakpoint_remove or PPSSPP restarts. Returns an error if the address isn't executable memory. RETURNS: Single line 'Breakpoint added at ADDR_HEX'."""
        await pp.call("cpu.breakpoint.add", {"address": address})
        return f"Breakpoint added at {_addr_hex(address)}"

    @mcp.tool()
    async def ppsspp_breakpoint_remove(
        address: Annotated[int, Field(ge=0, description="PSP execution address of the breakpoint to remove.")],
    ):
        """PURPOSE: Remove a previously-added CPU execution breakpoint. USAGE: Clean up breakpoints when done debugging. To remove all, query ppsspp_breakpoint_list first. BEHAVIOR: Modifies PPSSPP's breakpoint table. Idempotent for non-existent breakpoints (no error). RETURNS: Single line 'Breakpoint removed at ADDR_HEX'."""
        await pp.call("cpu.breakpoint.remove", {"address": address})
        return f"Breakpoint removed at {_addr_hex(address)}"

    @mcp.tool()
    async def ppsspp_breakpoint_list():
        """PURPOSE: List all currently-set CPU execution breakpoints. USAGE: Inventory before bulk-removing, or sanity-check what's set. BEHAVIOR: No side effects - pure read. RETURNS: Multi-line text, one line per breakpoint with its address and any conditions."""
        r = await pp.call("cpu.breakpoint.list")
        bps = r.get("breakpoints") or []
        if not bps:
            return "No breakpoints set."
        lines = []
        for b in bps:
            disabled = " (disabled)" if b.get("enabled") is False else ""
            cond = f" if {b['condition']}" if b.get("condition") else ""
            lines.append(f"  {_addr_hex(b.get('address', 0))}{disabled}{cond}")
        plural = "" if len(bps) == 1 else "s"
        return f"{len(bps)} breakpoint{plural}:\n" + "\n".join(lines)
