#!/usr/bin/env python3
"""
Local OpenAI-compatible relay for Devin CLI / Windsurf ConnectRPC backend.
Listens on 127.0.0.1:18805 and translates /v1/chat/completions (streaming & non-streaming,
including tools and multi-turn tool_calls with thinking signatures) to
https://server.codeium.com/exa.api_server_pb.ApiServerService/GetChatMessage.
"""

import http.server
import json
import os
import socketserver
import struct
import sys
import time
import tomllib
import urllib.error
import urllib.request
import uuid

LISTEN_HOST = os.environ.get("DEVIN_PROXY_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("DEVIN_PROXY_PORT", "18805"))
CREDENTIALS_PATH = os.path.expanduser("~/.local/share/devin/credentials.toml")
DEFAULT_MODEL = "gemini-4-argon-high"

# Cache of tool_call_id -> {"thinking": str, "signature": str, "sig_type": str}
# so multi-turn tool calls on thinking models (Gemini 4 Argon, Claude Opus, SWE-2)
# preserve their cryptographic thought signatures across stateless OpenAI requests.
TOOL_SIGNATURE_CACHE = {}


def load_credentials():
    with open(CREDENTIALS_PATH, "rb") as f:
        data = tomllib.load(f)
    api_key = data.get("windsurf_api_key", "")
    api_url = data.get("api_server_url", "https://server.codeium.com").rstrip("/")
    if not api_key:
        raise RuntimeError(f"Missing windsurf_api_key in {CREDENTIALS_PATH}")
    return api_key, api_url


# ---------------- Protobuf wire helpers ----------------

def pb_varint(val):
    if val < 0:
        val += 1 << 64
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def pb_tag(field_num, wire_type):
    return pb_varint((field_num << 3) | wire_type)


def pb_uint(field_num, val):
    return pb_tag(field_num, 0) + pb_varint(int(val))


def pb_double(field_num, val):
    return pb_tag(field_num, 1) + struct.pack("<d", float(val))


def pb_bytes(field_num, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return pb_tag(field_num, 2) + pb_varint(len(data)) + data


def read_varint(buf, pos):
    res = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80):
            return res, pos
        shift += 7


def parse_pb_fields(buf):
    pos = 0
    fields = []
    n = len(buf)
    while pos < n:
        tag, pos = read_varint(buf, pos)
        fnum = tag >> 3
        wtype = tag & 7
        if wtype == 0:
            val, pos = read_varint(buf, pos)
            fields.append((fnum, wtype, val))
        elif wtype == 1:
            val = struct.unpack("<d", buf[pos:pos + 8])[0]
            pos += 8
            fields.append((fnum, wtype, val))
        elif wtype == 5:
            val = struct.unpack("<f", buf[pos:pos + 4])[0]
            pos += 4
            fields.append((fnum, wtype, val))
        elif wtype == 2:
            ln, pos = read_varint(buf, pos)
            val = buf[pos:pos + ln]
            pos += ln
            fields.append((fnum, wtype, val))
        else:
            break
    return fields


# ---------------- Request builder ----------------

def extract_text_content(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") in ("text", "input_text"):
                    parts.append(str(item.get("text", "")))
        return "\n".join(parts)
    return str(content)


def build_metadata(api_key):
    return (
        pb_bytes(1, "devin-cli")
        + pb_bytes(2, "3000.11.3")
        + pb_bytes(3, api_key)
        + pb_bytes(4, "en")
        + pb_bytes(5, "linux")
        + pb_bytes(7, "3000.11.3")
        + pb_bytes(12, "chisel")
        + pb_bytes(28, "chisel")
    )


def build_chat_request(openai_req, api_key):
    model_uid = openai_req.get("model") or DEFAULT_MODEL
    if model_uid.startswith("devin/"):
        model_uid = model_uid.split("/", 1)[1]

    messages = openai_req.get("messages") or []
    system_parts = []
    chat_msgs_pb = []

    for msg in messages:
        role = msg.get("role", "user")
        text = extract_text_content(msg.get("content"))

        if role in ("system", "developer"):
            if text:
                system_parts.append(text)
            continue

        msg_id = str(uuid.uuid4())

        if role == "user":
            cm = (
                pb_bytes(1, msg_id)
                + pb_uint(2, 1)
                + pb_bytes(3, text or " ")
            )
            chat_msgs_pb.append(pb_bytes(3, cm))

        elif role == "assistant":
            cm = bytearray()
            cm += pb_bytes(1, msg_id)
            cm += pb_uint(2, 2)
            if text:
                cm += pb_bytes(3, text)

            tool_calls = msg.get("tool_calls") or []
            cached_sig = None
            for tc in tool_calls:
                tc_id = tc.get("id") or f"call_{uuid.uuid4().hex[:24]}"
                fn = tc.get("function") or {}
                fn_name = fn.get("name") or ""
                fn_args = fn.get("arguments")
                if not isinstance(fn_args, str):
                    fn_args = json.dumps(fn_args or {})
                tc_pb = (
                    pb_bytes(1, tc_id)
                    + pb_bytes(2, fn_name)
                    + pb_bytes(3, fn_args)
                )
                cm += pb_bytes(6, tc_pb)
                if not cached_sig and tc_id in TOOL_SIGNATURE_CACHE:
                    cached_sig = TOOL_SIGNATURE_CACHE[tc_id]

            reasoning = msg.get("reasoning_content") or (cached_sig and cached_sig.get("thinking")) or ""
            if reasoning:
                cm += pb_bytes(11, reasoning)
            if cached_sig and cached_sig.get("signature"):
                cm += pb_bytes(12, cached_sig["signature"])
                cm += pb_bytes(18, cached_sig.get("sig_type") or "sealed")

            chat_msgs_pb.append(pb_bytes(3, bytes(cm)))

        elif role == "tool":
            tc_id = msg.get("tool_call_id") or ""
            cm = (
                pb_bytes(1, msg_id)
                + pb_uint(2, 4)
                + pb_bytes(3, text if text else "(empty)")
                + pb_bytes(7, tc_id)
            )
            chat_msgs_pb.append(pb_bytes(3, cm))

    system_prompt = "\n\n".join(system_parts) if system_parts else "You are a helpful assistant."

    max_tokens = int(
        openai_req.get("max_completion_tokens")
        or openai_req.get("max_tokens")
        or 16384
    )
    temperature = float(openai_req.get("temperature", 1.0))

    gen_cfg = (
        pb_uint(1, 1)
        + pb_uint(2, 128000)
        + pb_uint(3, max_tokens)
        + pb_double(5, temperature)
        + pb_uint(7, 40)
        + pb_double(8, 0.95)
    )

    tools_pb = []
    for tool in (openai_req.get("tools") or []):
        if tool.get("type") == "function" and "function" in tool:
            fn = tool["function"]
        else:
            fn = tool
        name = fn.get("name", "")
        if not name:
            continue
        desc = fn.get("description") or ""
        params = fn.get("parameters") or {"type": "object", "properties": {}}
        if not isinstance(params, str):
            params = json.dumps(params)
        t_pb = pb_bytes(1, name) + pb_bytes(2, desc) + pb_bytes(3, params)
        tools_pb.append(pb_bytes(10, t_pb))

    sess_meta = pb_bytes(1, str(uuid.uuid4())) + pb_uint(3, 4) + pb_uint(4, 14)

    req_pb = bytearray()
    req_pb += pb_bytes(1, build_metadata(api_key))
    req_pb += pb_bytes(2, system_prompt)
    for m in chat_msgs_pb:
        req_pb += m
    req_pb += pb_uint(7, 5)
    req_pb += pb_bytes(8, gen_cfg)
    for t in tools_pb:
        req_pb += t
    req_pb += pb_bytes(15, sess_meta)
    req_pb += pb_bytes(16, str(uuid.uuid4()))
    req_pb += pb_uint(20, 1)
    req_pb += pb_bytes(21, model_uid)
    req_pb += pb_bytes(28, "opalescent-winter")

    payload = bytes(req_pb)
    connect_frame = b"\x00" + struct.pack(">I", len(payload)) + payload
    return connect_frame, model_uid


# ---------------- Connect stream reader ----------------

def iter_connect_frames(resp):
    while True:
        hdr = resp.read(5)
        if not hdr or len(hdr) < 5:
            break
        flags = hdr[0]
        length = struct.unpack(">I", hdr[1:5])[0]
        payload = bytearray()
        while len(payload) < length:
            chunk = resp.read(length - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
        yield flags, bytes(payload)


def parse_response_frame(payload):
    out = {
        "delta_text": "",
        "delta_thinking": "",
        "signature": "",
        "sig_type": "",
        "tool_deltas": [],
        "stop_reason": None,
        "usage": None,
    }
    for fnum, wtype, val in parse_pb_fields(payload):
        if fnum == 3 and wtype == 2:
            out["delta_text"] += val.decode("utf-8", errors="replace")
        elif fnum == 9 and wtype == 2:
            out["delta_thinking"] += val.decode("utf-8", errors="replace")
        elif fnum == 10 and wtype == 2:
            out["signature"] = val.decode("utf-8", errors="replace")
        elif fnum == 21 and wtype == 2:
            out["sig_type"] = val.decode("utf-8", errors="replace")
        elif fnum == 5 and wtype == 0:
            out["stop_reason"] = val
        elif fnum == 6 and wtype == 2:
            tc = {"id": "", "name": "", "arguments": ""}
            for sf, sw, sv in parse_pb_fields(val):
                if sf == 1 and sw == 2:
                    tc["id"] = sv.decode("utf-8", errors="replace")
                elif sf == 2 and sw == 2:
                    tc["name"] = sv.decode("utf-8", errors="replace")
                elif sf == 3 and sw == 2:
                    tc["arguments"] += sv.decode("utf-8", errors="replace")
            out["tool_deltas"].append(tc)
        elif fnum == 7 and wtype == 2:
            u = {}
            for sf, sw, sv in parse_pb_fields(val):
                if sf == 2 and sw == 0:
                    u["prompt_tokens"] = sv
                elif sf == 3 and sw == 0:
                    u["completion_tokens"] = sv
                elif sf == 5 and sw == 0:
                    u["cached_tokens"] = sv
            if "prompt_tokens" in u or "completion_tokens" in u:
                out["usage"] = u
    return out


# ---------------- HTTP Server ----------------

CATALOG_MODELS = [
    {"id": "gemini-4-argon-high", "context_window": 1048576},
    {"id": "gemini-4-argon-medium", "context_window": 1048576},
    {"id": "gemini-4-argon-low", "context_window": 1048576},
    {"id": "swe-2-high", "context_window": 262000},
    {"id": "swe-2-medium", "context_window": 262000},
    {"id": "swe-2-max", "context_window": 262000},
    {"id": "claude-opus-5-5-high", "context_window": 1000000},
    {"id": "claude-opus-5-5-max", "context_window": 1000000},
    {"id": "claude-fable-5-1-high", "context_window": 1000000},
    {"id": "gpt-6-astra-high", "context_window": 1000000},
    {"id": "gpt-6-sol-high", "context_window": 1000000},
]


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class RelayHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[devin-proxy] {self.address_string()} - {fmt % args}\n")

    def send_json(self, status, obj):
        self.close_connection = True
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            try:
                api_key, api_url = load_credentials()
                self.send_json(200, {"status": "ok", "upstream": api_url, "key_len": len(api_key)})
            except Exception as e:
                self.send_json(503, {"status": "error", "error": str(e)})
            return

        if self.path.startswith("/v1/models"):
            now = int(time.time())
            data = [
                {
                    "id": m["id"],
                    "object": "model",
                    "created": now,
                    "owned_by": "cognition-devin",
                    "context_window": m["context_window"],
                }
                for m in CATALOG_MODELS
            ]
            self.send_json(200, {"object": "list", "data": data})
            return

        self.send_json(404, {"error": {"message": f"Not found: {self.path}"}})

    def do_POST(self):
        if not self.path.startswith("/v1/chat/completions"):
            self.send_json(404, {"error": {"message": f"Unsupported path: {self.path}"}})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            openai_req = json.loads(raw_body.decode("utf-8"))
        except Exception as e:
            self.send_json(400, {"error": {"message": f"Invalid JSON: {e}"}})
            return

        try:
            api_key, api_url = load_credentials()
            connect_body, model_uid = build_chat_request(openai_req, api_key)
        except Exception as e:
            self.send_json(500, {"error": {"message": f"Failed to build request: {e}"}})
            return

        upstream_url = f"{api_url}/exa.api_server_pb.ApiServerService/GetChatMessage"
        req = urllib.request.Request(
            upstream_url,
            data=connect_body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/connect+proto",
                "Connect-Protocol-Version": "1",
                "Accept": "*/*",
            },
            method="POST",
        )

        is_stream = bool(openai_req.get("stream", False))
        cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())

        try:
            resp = urllib.request.urlopen(req, timeout=180)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            self.send_json(e.code, {"error": {"message": f"Upstream HTTP {e.code}: {err_body}"}})
            return
        except Exception as e:
            self.send_json(502, {"error": {"message": f"Upstream connection error: {e}"}})
            return

        if is_stream:
            self.handle_streaming(resp, cmpl_id, created, model_uid)
        else:
            self.handle_non_streaming(resp, cmpl_id, created, model_uid)

    def write_sse(self, obj):
        line = f"data: {json.dumps(obj)}\n\n".encode("utf-8")
        self.wfile.write(line)
        self.wfile.flush()

    def handle_streaming(self, resp, cmpl_id, created, model_uid):
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        sent_role = False
        tool_index = -1
        tool_ids = []
        full_thinking = []
        last_sig = ""
        last_sig_type = "sealed"
        finish_reason = "stop"
        usage_obj = None

        try:
            with resp:
                for flags, payload in iter_connect_frames(resp):
                    if flags & 2:
                        end_json = json.loads(payload.decode("utf-8", errors="replace") or "{}")
                        if "error" in end_json:
                            err_msg = end_json["error"].get("message", "Connect stream error")
                            self.write_sse({
                                "id": cmpl_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model_uid,
                                "choices": [{"index": 0, "delta": {"content": f"\n[Error: {err_msg}]"}, "finish_reason": "stop"}],
                            })
                        break

                    parsed = parse_response_frame(payload)
                    if parsed["signature"]:
                        last_sig = parsed["signature"]
                    if parsed["sig_type"]:
                        last_sig_type = parsed["sig_type"]
                    if parsed["usage"]:
                        u = parsed["usage"]
                        prompt_t = u.get("prompt_tokens", 0) + u.get("cached_tokens", 0)
                        comp_t = u.get("completion_tokens", 0)
                        usage_obj = {
                            "prompt_tokens": prompt_t,
                            "completion_tokens": comp_t,
                            "total_tokens": prompt_t + comp_t,
                        }

                    if parsed["delta_thinking"]:
                        full_thinking.append(parsed["delta_thinking"])
                        delta = {"reasoning_content": parsed["delta_thinking"]}
                        if not sent_role:
                            delta["role"] = "assistant"
                            sent_role = True
                        self.write_sse({
                            "id": cmpl_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model_uid,
                            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                        })

                    if parsed["delta_text"]:
                        delta = {"content": parsed["delta_text"]}
                        if not sent_role:
                            delta["role"] = "assistant"
                            sent_role = True
                        self.write_sse({
                            "id": cmpl_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model_uid,
                            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                        })

                    for td in parsed["tool_deltas"]:
                        if td["id"] or td["name"]:
                            tool_index += 1
                            tc_id = td["id"] or f"call_{uuid.uuid4().hex[:24]}"
                            tool_ids.append(tc_id)
                            tc_chunk = {
                                "index": tool_index,
                                "id": tc_id,
                                "type": "function",
                                "function": {"name": td["name"], "arguments": td["arguments"] or ""},
                            }
                        else:
                            if tool_index < 0:
                                tool_index = 0
                            tc_chunk = {
                                "index": tool_index,
                                "function": {"arguments": td["arguments"]},
                            }
                        delta = {"tool_calls": [tc_chunk]}
                        if not sent_role:
                            delta["role"] = "assistant"
                            sent_role = True
                        self.write_sse({
                            "id": cmpl_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model_uid,
                            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                        })

                    if parsed["stop_reason"] == 10:
                        finish_reason = "tool_calls"
                    elif parsed["stop_reason"] == 3:
                        finish_reason = "length"

            if tool_ids:
                finish_reason = "tool_calls"
                thinking_str = "".join(full_thinking)
                for tid in tool_ids:
                    if len(TOOL_SIGNATURE_CACHE) >= 2000:
                        TOOL_SIGNATURE_CACHE.pop(next(iter(TOOL_SIGNATURE_CACHE)), None)
                    TOOL_SIGNATURE_CACHE[tid] = {
                        "thinking": thinking_str,
                        "signature": last_sig,
                        "sig_type": last_sig_type,
                    }

            final_chunk = {
                "id": cmpl_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_uid,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            }
            if usage_obj:
                final_chunk["usage"] = usage_obj
            self.write_sse(final_chunk)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except BrokenPipeError:
            pass

    def handle_non_streaming(self, resp, cmpl_id, created, model_uid):
        content_parts = []
        thinking_parts = []
        tool_calls = []
        last_sig = ""
        last_sig_type = "sealed"
        finish_reason = "stop"
        usage_obj = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        with resp:
            for flags, payload in iter_connect_frames(resp):
                if flags & 2:
                    end_json = json.loads(payload.decode("utf-8", errors="replace") or "{}")
                    if "error" in end_json:
                        self.send_json(502, {"error": end_json["error"]})
                        return
                    break

                parsed = parse_response_frame(payload)
                if parsed["delta_text"]:
                    content_parts.append(parsed["delta_text"])
                if parsed["delta_thinking"]:
                    thinking_parts.append(parsed["delta_thinking"])
                if parsed["signature"]:
                    last_sig = parsed["signature"]
                if parsed["sig_type"]:
                    last_sig_type = parsed["sig_type"]
                if parsed["usage"]:
                    u = parsed["usage"]
                    prompt_t = u.get("prompt_tokens", 0) + u.get("cached_tokens", 0)
                    comp_t = u.get("completion_tokens", 0)
                    usage_obj = {
                        "prompt_tokens": prompt_t,
                        "completion_tokens": comp_t,
                        "total_tokens": prompt_t + comp_t,
                    }

                for td in parsed["tool_deltas"]:
                    if td["id"] or td["name"]:
                        tc_id = td["id"] or f"call_{uuid.uuid4().hex[:24]}"
                        tool_calls.append({
                            "id": tc_id,
                            "type": "function",
                            "function": {
                                "name": td["name"],
                                "arguments": td["arguments"] or "",
                            },
                        })
                    elif tool_calls:
                        tool_calls[-1]["function"]["arguments"] += td["arguments"]

                if parsed["stop_reason"] == 10:
                    finish_reason = "tool_calls"
                elif parsed["stop_reason"] == 3:
                    finish_reason = "length"

        thinking_str = "".join(thinking_parts)
        if tool_calls:
            finish_reason = "tool_calls"
            for tc in tool_calls:
                TOOL_SIGNATURE_CACHE[tc["id"]] = {
                    "thinking": thinking_str,
                    "signature": last_sig,
                    "sig_type": last_sig_type,
                }

        message = {
            "role": "assistant",
            "content": "".join(content_parts) if content_parts else None,
        }
        if thinking_str:
            message["reasoning_content"] = thinking_str
        if tool_calls:
            message["tool_calls"] = tool_calls

        self.send_json(200, {
            "id": cmpl_id,
            "object": "chat.completion",
            "created": created,
            "model": model_uid,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }],
            "usage": usage_obj,
        })


if __name__ == "__main__":
    srv = ThreadedHTTPServer((LISTEN_HOST, LISTEN_PORT), RelayHandler)
    sys.stderr.write(f"[devin-proxy] listening on http://{LISTEN_HOST}:{LISTEN_PORT}\n")
    srv.serve_forever()
