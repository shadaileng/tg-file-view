#!/usr/bin/env python3
"""Telegram 网络全链路诊断工具 — 分段定位预览加载慢的瓶颈。

网络路径: App → SOCKS5 (127.0.0.1:1080) → V2Ray → TG DCs

7 个诊断阶段:
  Stage 1: SOCKS5 TCP 连接 + 握手  → V2Ray 端口是否正常
  Stage 2: 代理出口 HTTP 往返       → 代理出口公网延迟
  Stage 3: Telegram DC DNS 解析     → DNS 速度
  Stage 4: 各 DC TCP 连接延迟       → 最优 DC
  Stage 5: MTProto Ping (×5)        → 协议层 RTT
  Stage 6: iter_download 块延迟     → 下载吞吐量
  Stage 7: get_entity 操作延迟      → API 调用 RTT

用法:
  uv run python scripts/diag_tg_network.py -s ./data/tg_file_viewer
  uv run python scripts/diag_tg_network.py -s ./data/tg_file_viewer -f 10
"""

import argparse
import asyncio
import os
import socket
import sqlite3
import struct
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 颜色输出
# ---------------------------------------------------------------------------
_USE_COLOR = sys.stdout.isatty()


def _color(code: int, text: str) -> str:
    if _USE_COLOR:
        return f"\033[{code}m{text}\033[0m"
    return text


def _c_lat(ms: float, good: float = 100, warn: float = 500) -> str:
    """着色延迟值: <=good 绿色 | <=warn 黄色 | >warn 红色."""
    v = f"{ms:7.1f}ms"
    if ms <= good:
        return _color(32, v)  # green
    if ms <= warn:
        return _color(33, v)  # yellow
    return _color(31, v)  # red


def _header(title: str) -> None:
    print(f"\n{_color(36, '=' * 60)}")
    print(f"  {_color(1, title)}")
    print(f"{_color(36, '=' * 60)}")


def _ok(msg: str) -> None:
    print(f"  {_color(32, '✓')} {msg}")


def _warn(msg: str) -> None:
    print(f"  {_color(33, '⚠')} {msg}")


def _fail(msg: str) -> None:
    print(f"  {_color(31, '✗')} {msg}")


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------
def _load_env():
    """从 .env 文件加载配置，返回 (proxy_url, api_id, api_hash, db_path)."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return None, None, None, "./data/db.sqlite"

    values = {}
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()

    data_dir = values.get("TG_DATA_DIR", "./data")
    db_path = str(Path(data_dir) / "db.sqlite")

    return (
        values.get("TG_PROXY_URL"),
        values.get("TG_API_ID"),
        values.get("TG_API_HASH"),
        db_path,
    )


# ---------------------------------------------------------------------------
# Stage 1: SOCKS5 代理连通性
# ---------------------------------------------------------------------------
async def stage1_socks5(proxy_host: str, proxy_port: int) -> bool:
    """测试 SOCKS5 TCP 连接 + 握手延迟。

    返回 True 表示代理可达，False 表示不可达。
    """
    _header("Stage 1: SOCKS5 TCP 连接 + 握手")

    t0 = time.monotonic()

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(proxy_host, proxy_port),
            timeout=5.0,
        )
    except ConnectionRefusedError:
        _fail(f"连接被拒绝 — {proxy_host}:{proxy_port} 无进程监听")
        print(f"  → 请先启动 V2Ray: sudo systemctl start v2ray")
        return False
    except asyncio.TimeoutError:
        _fail(f"连接超时 ({proxy_host}:{proxy_port})")
        print(f"  → 检查防火墙规则或 V2Ray 绑定的地址")
        return False
    except OSError as e:
        _fail(f"系统错误: {e}")
        return False

    tcp_elapsed = (time.monotonic() - t0) * 1000
    _ok(f"TCP 连接 {proxy_host}:{proxy_port} — {_c_lat(tcp_elapsed, good=5)}")

    # SOCKS5 握手
    t_handshake = time.monotonic()
    try:
        await asyncio.wait_for(_socks5_handshake(reader, writer), timeout=3.0)
    except asyncio.TimeoutError:
        _fail("SOCKS5 握手超时 — 代理协议异常")
        writer.close()
        return False
    except Exception as e:
        _fail(f"SOCKS5 握手失败: {e}")
        writer.close()
        return False

    handshake_ms = (time.monotonic() - t_handshake) * 1000
    _ok(f"SOCKS5 握手 — {_c_lat(handshake_ms, good=10)}")
    writer.close()
    return True


async def _socks5_handshake(reader, writer):
    """SOCKS5 无认证握手 (RFC 1928)."""
    # Client greeting: VER=5, NMETHODS=1, METHODS=[0x00=NOAUTH]
    writer.write(b"\x05\x01\x00")
    await writer.drain()
    # Server choice: VER=5, METHOD=0x00
    resp = await reader.readexactly(2)
    if resp != b"\x05\x00":
        raise RuntimeError(f"Unexpected SOCKS5 greeting response: {resp.hex()}")


# ---------------------------------------------------------------------------
# Stage 2: 代理出口 HTTP 往返
# ---------------------------------------------------------------------------
async def _socks5_connect_to_remote(
    reader, writer, dest_host: str, dest_port: int
) -> None:
    """通过已握手的 SOCKS5 连接建立到目标主机的隧道 (CONNECT 命令).

    RFC 1928: 握手完成后发送 CONNECT 请求建立 TCP 隧道.
    """
    # CONNECT request: VER=5, CMD=1(CONNECT), RSV=0, ATYP=3(DOMAIN),
    #                  domain_len, domain_bytes, port(2 bytes big-endian)
    host_bytes = dest_host.encode()
    request = (
        b"\x05\x01\x00\x03"  # VER, CMD, RSV, ATYP=DOMAIN
        + bytes([len(host_bytes)])
        + host_bytes
        + struct.pack("!H", dest_port)
    )
    writer.write(request)
    await writer.drain()

    # Server response: VER, REP, RSV, ATYP, BND.ADDR (variable), BND.PORT (2 bytes)
    resp = await asyncio.wait_for(reader.readexactly(4), timeout=5.0)
    if resp[0] != 5 or resp[1] != 0:
        rep_code = resp[1]
        rep_msgs = {
            1: "general failure",
            2: "connection not allowed by ruleset",
            3: "network unreachable",
            4: "host unreachable",
            5: "connection refused by destination host",
            6: "TTL expired",
            7: "command not supported",
            8: "address type not supported",
        }
        raise RuntimeError(f"SOCKS5 CONNECT failed: {rep_msgs.get(rep_code, f'code {rep_code}')}")

    # Read the bound address (variable length depending on ATYP)
    atyp = resp[3]
    if atyp == 1:  # IPv4
        await reader.readexactly(4)
    elif atyp == 3:  # DOMAIN
        domain_len = (await reader.readexactly(1))[0]
        await reader.readexactly(domain_len)
    elif atyp == 4:  # IPv6
        await reader.readexactly(16)
    # Read bound port (2 bytes)
    await reader.readexactly(2)


async def stage2_http_via_proxy(proxy_host: str, proxy_port: int) -> bool:
    """通过 SOCKS5 代理访问 httpbin.org/get 测量公网往返延迟。

    手写 SOCKS5 CONNECT 隧道 + 原始 HTTP 请求，零外部库依赖。
    """
    _header("Stage 2: 代理出口 HTTP 往返 (httpbin.org)")

    try:
        # Step 1: TCP 连接代理
        t0 = time.monotonic()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(proxy_host, proxy_port),
            timeout=5.0,
        )
        tcp_ms = (time.monotonic() - t0) * 1000

        # Step 2: SOCKS5 握手 (无认证)
        t_hs = time.monotonic()
        await asyncio.wait_for(_socks5_handshake(reader, writer), timeout=3.0)
        hs_ms = (time.monotonic() - t_hs) * 1000

        # Step 3: SOCKS5 CONNECT 到 httpbin.org:80
        t_conn = time.monotonic()
        await _socks5_connect_to_remote(reader, writer, "httpbin.org", 80)
        connect_ms = (time.monotonic() - t_conn) * 1000
        print(f"    TCP: {_c_lat(tcp_ms, good=5)}  握手: {_c_lat(hs_ms, good=5)}  "
              f"CONNECT: {_c_lat(connect_ms, good=100)}")

        # Step 4: 通过 SOCKS5 隧道发送 HTTP 请求
        request = (
            "GET /get HTTP/1.1\r\n"
            "Host: httpbin.org\r\n"
            "Connection: close\r\n"
            "User-Agent: diag-tg-network/1.0\r\n"
            "\r\n"
        )

        t_req = time.monotonic()
        writer.write(request.encode())
        await writer.drain()

        # Step 5: 读取 HTTP 响应
        body = b""
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=10.0)
                if not chunk:
                    break
                body += chunk
        except asyncio.TimeoutError:
            pass

        writer.close()

        total_ms = (time.monotonic() - t0) * 1000
        req_ms = (time.monotonic() - t_req) * 1000

        # 解析 HTTP 状态码
        if body:
            status_line = body.split(b"\r\n")[0].decode(errors="replace") if body else "no response"
            if b"200 OK" in body[:20]:
                _ok(f"HTTP 200 — 请求耗时 {_c_lat(req_ms, good=200, warn=500)}  "
                    f"总耗时 {_c_lat(total_ms, good=500, warn=1000)}")
            else:
                _warn(f"HTTP 响应异常: {status_line} (耗时 {req_ms:.1f}ms)")
        else:
            _fail("HTTP 无响应")
            return False

        return True

    except asyncio.TimeoutError:
        _fail("代理 HTTP 连接超时 (>10s)")
        return False
    except Exception as e:
        _fail(f"代理 HTTP 失败: {e}")
        return False


# ---------------------------------------------------------------------------
# Stage 3: Telegram DC DNS 解析
# ---------------------------------------------------------------------------
# Telegram Data Center 地址 (来源: telegram.org)
_TG_DCS = {
    "DC1 (Miami)":       "149.154.175.50",
    "DC2 (Amsterdam)":   "149.154.167.51",
    "DC3 (Miami)":       "149.154.175.100",
    "DC4 (Amsterdam)":   "149.154.167.91",
    "DC5 (Singapore)":   "91.108.56.130",
    "pluto (media)":     "149.154.175.53",
    "venus (media)":     "149.154.167.52",
    "aurora (media)":    "149.154.175.101",
    "vesta (media)":     "149.154.167.92",
    "flora (media)":     "91.108.56.131",
}


async def stage3_dns() -> bool:
    """测试 Telegram 域名的 DNS 解析速度。"""
    _header("Stage 3: Telegram DC DNS 解析")

    domains = [
        "149.154.175.50",        # direct IP, no DNS needed
        "web.telegram.org",      # web client
        "api.telegram.org",      # Bot API
    ]

    loop = asyncio.get_running_loop()
    for host in domains:
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(
                loop.getaddrinfo(host, 443, family=socket.AF_INET),
                timeout=5.0,
            )
            ms = (time.monotonic() - t0) * 1000
            _ok(f"DNS {host:30s} → {_c_lat(ms, good=50)}")
        except (socket.gaierror, asyncio.TimeoutError) as e:
            _fail(f"DNS {host:30s} → 解析失败 ({e})")
            return False
        except Exception as e:
            _warn(f"DNS {host:30s} → {e}")

    return True


# ---------------------------------------------------------------------------
# Stage 4: 各 DC TCP 连接延迟
# ---------------------------------------------------------------------------
async def _tcp_connect_latency(host: str, port: int = 443) -> float:
    """测量 TCP 连接延迟 (ms)."""
    try:
        t0 = time.monotonic()
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=5.0,
        )
        elapsed = (time.monotonic() - t0) * 1000
        writer.close()
        return elapsed
    except (asyncio.TimeoutError, OSError):
        return float("inf")


async def stage4_dc_latency() -> dict[str, float]:
    """测试各 Telegram DC 的 TCP 连接延迟（直连，不走代理）。"""
    _header("Stage 4: 各 DC TCP 连接延迟（直连）")

    results = {}
    for name, ip in _TG_DCS.items():
        lat = await _tcp_connect_latency(ip)
        results[name] = lat

    # 按延迟排序输出
    sorted_dcs = sorted(results.items(), key=lambda x: x[1])
    for name, lat in sorted_dcs:
        label = f"{name:25s} ({_TG_DCS[name]:16s})"
        if lat == float("inf"):
            _fail(f"{label} → 不可达")
        else:
            _ok(f"{label} → {_c_lat(lat, good=100)}")

    return results


# ---------------------------------------------------------------------------
# Stage 5: MTProto Ping (Telethon)
# ---------------------------------------------------------------------------
async def stage5_mtproto_ping(
    api_id: str, api_hash: str, proxy_host: str, proxy_port: int,
    session_path: str,
) -> bool:
    """通过 Telethon 连接 Telegram 并 Ping 5 次测协议层 RTT。
    
    session_path: Telethon .session 文件路径（不含 .session 后缀）。
    """
    _header("Stage 5: MTProto Ping (Telethon, ×5)")

    if not api_id or not api_hash:
        _warn("未配置 TG_API_ID/TG_API_HASH，跳过")
        return True

    try:
        from telethon import TelegramClient
        from python_socks import ProxyType
    except ImportError as e:
        _warn(f"缺少依赖: {e}")
        return True

    proxy = (ProxyType.SOCKS5, proxy_host, proxy_port)

    client = TelegramClient(session_path, int(api_id), api_hash, proxy=proxy)

    try:
        # Connect
        t_conn = time.monotonic()
        await asyncio.wait_for(client.connect(), timeout=15.0)
        conn_ms = (time.monotonic() - t_conn) * 1000
        _ok(f"connect() — {_c_lat(conn_ms, good=200)}")

        # 检查授权
        if not await client.is_user_authorized():
            _warn("未登录 Telegram，跳过 Ping / 下载 / API 测试")
            await client.disconnect()
            return True  # not a failure, just skip further stages

        # Ping ×5
        rtts = []
        for i in range(5):
            t0 = time.monotonic()
            await client.get_me()  # 最小的 RPC，用作 ping
            rtt = (time.monotonic() - t0) * 1000
            rtts.append(rtt)
            print(f"    Ping #{i + 1}: {_c_lat(rtt, good=200)}")

        avg_rtt = sum(rtts) / len(rtts)
        min_rtt = min(rtts)
        max_rtt = max(rtts)
        print(f"    RTT 统计: avg={_c_lat(avg_rtt, good=200)}  "
              f"min={_c_lat(min_rtt, good=200)}  max={_c_lat(max_rtt, good=200)}")

        # Store client reference for later stages
        stage5_mtproto_ping._client = client
        return True

    except asyncio.TimeoutError:
        _fail("Telethon connect 超时 (>15s)")
        return False
    except Exception as e:
        _fail(f"Telethon 连接失败: {e}")
        return False


# ---------------------------------------------------------------------------
# 数据库辅助：按本地文件 ID 查询
# ---------------------------------------------------------------------------
def _get_file_from_db(db_path: str, file_id: int) -> dict | None:
    """从本地 SQLite 数据库中查询指定 file_id 的文件和频道信息."""
    if not Path(db_path).exists():
        print(f"  ⚠ 数据库不存在: {db_path}")
        return None

    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        """SELECT f.id, f.file_name, f.file_size, f.mime_type,
                  f.message_id, f.tg_ref,
                  c.tg_id AS channel_tg_id, c.title AS channel_title
           FROM files f
           JOIN channels c ON f.channel_id = c.id
           WHERE f.id = ?""",
        (file_id,),
    )
    row = cursor.fetchone()
    conn.close()

    if row is None:
        return None

    return {
        "id": row[0],
        "file_name": row[1],
        "file_size": row[2],
        "mime_type": row[3],
        "message_id": row[4],
        "tg_ref": row[5],
        "channel_tg_id": row[6],
        "channel_title": row[7],
    }


# ---------------------------------------------------------------------------
# Stage 6: iter_download 块延迟分布
# ---------------------------------------------------------------------------
async def stage6_download_speed(
    db_path: str = "./data/db.sqlite",
    target_file_id: int | None = None,
) -> bool:
    """通过 iter_download 下载文件，测量块间隔延迟分布。

    若指定 target_file_id，从数据库查询该文件并用 Telethon 拉取；
    否则自动查找一个 <5MB 的媒体文件。
    """
    _header("Stage 6: iter_download 块延迟分布")

    client = getattr(stage5_mtproto_ping, "_client", None)
    if client is None:
        _warn("Stage 5 未建立连接或未授权，跳过")
        return True

    try:
        # ── 指定文件模式 ──
        if target_file_id is not None:
            info = _get_file_from_db(db_path, target_file_id)
            if info is None:
                _fail(f"数据库中未找到 file_id={target_file_id}")
                return False

            _ok(f"目标: {info['file_name']}")
            _ok(f"频道: {info['channel_title']}  "
                 f"大小: {info['file_size'] / 1024:.0f} KB  "
                 f"类型: {info['mime_type']}")

            try:
                entity = await client.get_entity(info["channel_tg_id"])
            except Exception as e:
                _fail(f"get_entity({info['channel_tg_id']}) 失败: {e}")
                return False

            try:
                msg = await client.get_messages(entity, ids=info["message_id"])
            except Exception as e:
                _fail(f"get_messages(ids={info['message_id']}) 失败: {e}")
                return False

            if msg is None or msg.media is None:
                _fail("消息无媒体附件")
                return False

            media = msg.media
            file_size = info["file_size"]
            file_size_kb = info["file_size"] / 1024

        else:
            # ── 自动查找模式 ──
            # 获取用户所在的频道列表
            dialogs = await client.get_dialogs(limit=50)
            channels = [d for d in dialogs if d.is_channel]
            if not channels:
                _warn("未加入任何频道，跳过下载测试")
                return True

            # 遍历频道，找第一个有合适大小文件的消息
            media_msg = None
            channel_name = ""
            target_max = 5 * 1024 * 1024  # 5MB limit for quick test

            for ch in channels:
                try:
                    msgs = await client.get_messages(ch.entity, limit=50)
                except Exception:
                    continue
                for m in msgs:
                    if m is None or m.media is None:
                        continue
                    doc = getattr(m, "document", None)
                    if doc:
                        size = getattr(doc, "size", None) or 0
                        if 0 < size <= target_max:
                            media_msg = m
                            channel_name = ch.name or str(ch.id)
                            target_max = size  # prefer smaller files
                    else:
                        # photo, etc.
                        media_msg = m
                        channel_name = ch.name or str(ch.id)
                        break
                if media_msg is not None:
                    break

            if media_msg is None:
                _warn("未找到合适的测试文件，跳过")
                return True

            file_size = getattr(media_msg.document, "size", 0) if hasattr(media_msg, "document") else 0
            file_size_kb = file_size / 1024
            _ok(f"测试源: {channel_name}, 大小 {file_size_kb:.0f} KB")

            media = media_msg.media

        # Download in chunks, measuring inter-chunk delays
        chunk_intervals = []
        total_bytes = 0
        last_chunk_time = time.monotonic()
        first_chunk_time = None
        chunk_count = 0

        t_total = time.monotonic()
        async for chunk in client.iter_download(media, request_size=64 * 1024):
            now = time.monotonic()
            total_bytes += len(chunk)
            chunk_count += 1

            if first_chunk_time is None:
                first_chunk_time = now
                ttfb = (now - t_total) * 1000
            else:
                interval = (now - last_chunk_time) * 1000
                chunk_intervals.append(interval)

            last_chunk_time = now

        elapsed_total = (time.monotonic() - t_total) * 1000
        speed_kbps = (total_bytes / 1024) / (elapsed_total / 1000) if elapsed_total > 0 else 0

        if chunk_intervals:
            sorted_intervals = sorted(chunk_intervals)
            n = len(sorted_intervals)
            p50 = sorted_intervals[n // 2]
            p90 = sorted_intervals[int(n * 0.9)]
            p99 = sorted_intervals[min(int(n * 0.99), n - 1)]

            ttfb_str = _c_lat(ttfb, good=500, warn=2000) if first_chunk_time else "N/A"
            print(f"    TTFB (首字节): {ttfb_str}")
            print(f"    P50 块间隔: {_c_lat(p50, good=200)}  "
                  f"P90: {_c_lat(p90, good=500)}  "
                  f"P99: {_c_lat(p99, good=1000)}")
            print(f"    总数据: {total_bytes / 1024:.0f} KB | "
                  f"块数: {chunk_count} | "
                  f"总耗时: {_c_lat(elapsed_total, good=2000)}")
            print(f"    吞吐量: {_color(34, f'{speed_kbps:.0f} KB/s')}")
        else:
            _ok(f"TFFB: {_c_lat(ttfb, good=500, warn=2000)}, "
                f"文件过小无块间隔数据, 总耗时 {_c_lat(elapsed_total, good=2000)}")

        return True

    except Exception as e:
        _fail(f"下载测试失败: {e}")
        return False


# ---------------------------------------------------------------------------
# Stage 7: API 调用延迟
# ---------------------------------------------------------------------------
async def stage7_api_latency() -> bool:
    """测试 get_entity 和 get_messages 操作的延迟。"""
    _header("Stage 7: 典型 API 调用延迟")

    client = getattr(stage5_mtproto_ping, "_client", None)
    if client is None:
        _warn("Stage 5 未建立连接或未授权，跳过")
        return True

    try:
        dialogs = await client.get_dialogs(limit=5)
        channels = [d for d in dialogs if d.is_channel]
        if not channels:
            _warn("无频道，跳过")
            return True

        target = channels[0]

        # get_entity
        t0 = time.monotonic()
        entity = await client.get_entity(target.entity)
        get_entity_ms = (time.monotonic() - t0) * 1000
        _ok(f"get_entity('{getattr(target, 'name', '?')}') — {_c_lat(get_entity_ms, good=200, warn=2000)}")

        # get_messages
        try:
            t0 = time.monotonic()
            msgs = await client.get_messages(entity, limit=20)
            get_msgs_ms = (time.monotonic() - t0) * 1000
            _ok(f"get_messages(limit=20) — {_c_lat(get_msgs_ms, good=500, warn=3000)} "
                f"({len(msgs)} 条)")
        except Exception as e:
            _warn(f"get_messages: {e}")

        return True

    except Exception as e:
        _fail(f"API 测试失败: {e}")
        return False


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
async def print_summary(results: dict[str, bool], dc_latencies: dict[str, float]) -> None:
    """打印诊断汇总和建议。"""
    _header("诊断汇总")

    passed = sum(1 for v in results.values() if v)
    total = len(results)
    status_color = 32 if passed == total else (33 if passed > 0 else 31)

    print(f"\n  结果: {_color(status_color, f'{passed}/{total} 阶段通过')}")
    print()
    for stage, ok in results.items():
        label = stage.split(":", 1)[1].strip() if ":" in stage else stage
        if ok:
            print(f"  {_color(32, '✓')} {label}")
        else:
            print(f"  {_color(31, '✗')} {label}")

    # 慢速 DC 汇总
    slow_dcs = [(n, lat) for n, lat in dc_latencies.items() if lat != float("inf") and lat > 300]
    if slow_dcs:
        print(f"\n  {_color(33, '⚠')} 以下 DC TCP 延迟 >300ms:")
        for name, lat in sorted(slow_dcs, key=lambda x: x[1], reverse=True):
            print(f"      {name:20s} — {lat:.0f}ms")

    # 建议
    print(f"\n  {_color(36, '建议:')}")
    if not results.get("stage1", True):
        print("    - 启动 V2Ray: sudo systemctl start v2ray")
    if dc_latencies.get("DC5 (Singapore)", float("inf")) < 150:
        print("    - 当前网络到新加坡 DC5 延迟较低（<150ms），"
              "可考虑使用 DC5 附近的代理")
    if any(lat > 400 and lat != float("inf") for lat in dc_latencies.values()):
        print("    - 多数 DC 延迟 >400ms，跨国代理延迟是预览慢的主要原因")
        print("    - 优化方向：换用亚洲节点代理 / 升级 VLESS+XTLS / 启用本地缓存")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
async def main():
    print(f"\n{_color(36, '╔' + '═' * 58 + '╗')}")
    print(f"{_color(36, '║')}  {_color(1, 'Telegram 网络全链路诊断')}                    {_color(36, '║')}")
    print(f"{_color(36, '╚' + '═' * 58 + '╝')}")

    # ─── CLI 参数解析 ───
    parser = argparse.ArgumentParser(
        description="Telegram 网络全链路诊断 — 分段定位预览加载慢的瓶颈",
    )
    parser.add_argument(
        "-s", "--session",
        required=True,
        help="Telethon session 文件路径（不含 .session 后缀），例: ./data/tg_file_viewer",
    )
    parser.add_argument(
        "-f", "--file-id",
        type=int,
        default=None,
        help="指定要下载测试的文件 ID（本地 DB 中的 files.id），不指定则自动查找",
    )
    args, _ = parser.parse_known_args()

    session_path = args.session
    target_file_id = args.file_id

    # 加载配置
    proxy_url, api_id, api_hash, db_path = _load_env()

    # 解析代理地址
    proxy_host = "127.0.0.1"
    proxy_port = 1080
    if proxy_url:
        from urllib.parse import urlparse
        parsed = urlparse(proxy_url)
        if parsed.hostname:
            proxy_host = parsed.hostname
        if parsed.port:
            proxy_port = parsed.port
        print(f"\n代理: socks5://{proxy_host}:{proxy_port}")
    else:
        print(f"\n{_color(33, '⚠')} 未配置 TG_PROXY_URL")

    print(f"Session: {session_path}")
    print(f"TG API ID: {'***' + api_id[-2:] if api_id and len(api_id) > 2 else '未配置'}")
    if target_file_id:
        print(f"目标文件: ID={target_file_id}")
    print()

    results = {}
    dc_latencies = {}

    # Stage 1: SOCKS5
    ok = await stage1_socks5(proxy_host, proxy_port)
    results["stage1: SOCKS5"] = ok
    if not ok:
        await print_summary(results, dc_latencies)
        return 1

    # Stage 2: HTTP via proxy
    ok = await stage2_http_via_proxy(proxy_host, proxy_port)
    results["stage2: HTTP via proxy"] = ok

    # Stage 3: DNS
    ok = await stage3_dns()
    results["stage3: DNS"] = ok

    # Stage 4: DC TCP latency
    dc_latencies = await stage4_dc_latency()
    ok = any(lat != float("inf") for lat in dc_latencies.values())
    results["stage4: DC TCP latency"] = ok

    # Stage 5: MTProto Ping — 使用 CLI 指定的 session
    ok = await stage5_mtproto_ping(api_id, api_hash, proxy_host, proxy_port, session_path)
    results["stage5: MTProto Ping"] = ok

    # Stage 6: Download speed — 支持指定 file-id
    ok = await stage6_download_speed(db_path, target_file_id)
    results["stage6: Download speed"] = ok

    # Stage 7: API latency
    ok = await stage7_api_latency()
    results["stage7: API latency"] = ok

    # Cleanup（不再删除项目自身的 session 文件）
    client = getattr(stage5_mtproto_ping, "_client", None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass

    await print_summary(results, dc_latencies)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
