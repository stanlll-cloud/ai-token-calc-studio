#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI Token 成本估算工作台后端服务（零第三方依赖，纯 Python 标准库）。"""

import argparse
import json
import math
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
USD_TO_CNY = 7.25

# 主流大模型参考单价（单位：人民币元 / 百万 Token，海外模型按参考汇率折算）
MODELS: List[Dict[str, Any]] = [
    {
        "id": "glm-4-flash",
        "name": "GLM-4-Flash",
        "provider": "智谱 AI",
        "input_cny_1m": 0.10,
        "cached_input_cny_1m": 0.10,
        "output_cny_1m": 0.10,
        "context_k": 128,
        "tag": "极致免费/极低成本",
    },
    {
        "id": "deepseek-v3",
        "name": "DeepSeek-V3",
        "provider": "DeepSeek",
        "input_cny_1m": 2.00,
        "cached_input_cny_1m": 0.50,
        "output_cny_1m": 8.00,
        "context_k": 128,
        "tag": "高性价比主力",
    },
    {
        "id": "deepseek-r1",
        "name": "DeepSeek-R1",
        "provider": "DeepSeek",
        "input_cny_1m": 4.00,
        "cached_input_cny_1m": 1.00,
        "output_cny_1m": 16.00,
        "context_k": 128,
        "tag": "深度推理",
    },
    {
        "id": "qwen-max",
        "name": "Qwen-Max",
        "provider": "阿里云百炼",
        "input_cny_1m": 2.40,
        "cached_input_cny_1m": 0.96,
        "output_cny_1m": 9.60,
        "context_k": 32,
        "tag": "中文旗舰",
    },
    {
        "id": "glm-4-plus",
        "name": "GLM-4-Plus",
        "provider": "智谱 AI",
        "input_cny_1m": 5.00,
        "cached_input_cny_1m": 2.50,
        "output_cny_1m": 5.00,
        "context_k": 128,
        "tag": "均衡通用",
    },
    {
        "id": "gemini-2.5-flash",
        "name": "Gemini 2.5 Flash",
        "provider": "Google",
        "input_cny_1m": round(0.15 * USD_TO_CNY, 2),
        "cached_input_cny_1m": round(0.0375 * USD_TO_CNY, 2),
        "output_cny_1m": round(0.60 * USD_TO_CNY, 2),
        "context_k": 1000,
        "tag": "百万长上下文",
    },
    {
        "id": "gpt-4o",
        "name": "GPT-4o",
        "provider": "OpenAI",
        "input_cny_1m": round(2.50 * USD_TO_CNY, 2),
        "cached_input_cny_1m": round(1.25 * USD_TO_CNY, 2),
        "output_cny_1m": round(10.00 * USD_TO_CNY, 2),
        "context_k": 128,
        "tag": "多模态旗舰",
    },
    {
        "id": "claude-3.7-sonnet",
        "name": "Claude 3.7 Sonnet",
        "provider": "Anthropic",
        "input_cny_1m": round(3.00 * USD_TO_CNY, 2),
        "cached_input_cny_1m": round(0.30 * USD_TO_CNY, 2),
        "output_cny_1m": round(15.00 * USD_TO_CNY, 2),
        "context_k": 200,
        "tag": "工程编码标杆",
    },
]

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_SYMBOL_RE = re.compile(r"[^\sA-Za-z0-9_\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")


def estimate_tokens(text: str) -> Dict[str, int]:
    """按中文字符、英文/数字词元与代码符号分别估算 Token 数量。"""
    if not text:
        return {
            "total_tokens": 0,
            "char_count": 0,
            "cjk_chars": 0,
            "latin_words": 0,
            "symbols": 0,
        }

    cjk_chars = len(_CJK_RE.findall(text))
    latin_words = _WORD_RE.findall(text)
    symbols = len(_SYMBOL_RE.findall(text))

    # 经验换算：中文约 1.35 token/字，英文词按每 4 字符 1 token（含长标识符拆词），符号约 0.65 token
    latin_tokens = sum(max(1, math.ceil(len(w) / 4)) for w in latin_words)
    estimated = max(1, round(cjk_chars * 1.35 + latin_tokens + symbols * 0.65))

    return {
        "total_tokens": estimated,
        "char_count": len(text),
        "cjk_chars": cjk_chars,
        "latin_words": len(latin_words),
        "symbols": symbols,
    }


def calculate_costs(
    input_tokens: int,
    output_tokens: int,
    requests_per_day: int,
    cache_hit_rate: float = 0.0,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """计算全模型单次、每日与指定周期费用并按周期总成本升序排序。"""
    input_tokens = max(0, int(input_tokens))
    output_tokens = max(0, int(output_tokens))
    requests_per_day = max(1, int(requests_per_day))
    days = max(1, int(days))
    hit = min(1.0, max(0.0, float(cache_hit_rate)))

    rows: List[Dict[str, Any]] = []
    for m in MODELS:
        uncached_in = input_tokens * (1.0 - hit)
        cached_in = input_tokens * hit
        per_call_cny = (
            (uncached_in * m["input_cny_1m"])
            + (cached_in * m["cached_input_cny_1m"])
            + (output_tokens * m["output_cny_1m"])
        ) / 1_000_000.0
        daily_cny = per_call_cny * requests_per_day
        period_cny = daily_cny * days

        rows.append(
            {
                "id": m["id"],
                "name": m["name"],
                "provider": m["provider"],
                "tag": m["tag"],
                "context_k": m["context_k"],
                "input_cny_1m": m["input_cny_1m"],
                "cached_input_cny_1m": m["cached_input_cny_1m"],
                "output_cny_1m": m["output_cny_1m"],
                "per_call_cny": round(per_call_cny, 6),
                "daily_cny": round(daily_cny, 4),
                "period_cny": round(period_cny, 2),
                "period_usd": round(period_cny / USD_TO_CNY, 2),
            }
        )

    rows.sort(key=lambda r: (r["period_cny"], r["per_call_cny"]))
    return rows


class TokenStudioHandler(BaseHTTPRequestHandler):
    """处理前端页面与估算 API 请求。"""

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_path: Path) -> None:
        if not html_path.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "index.html not found"})
            return
        body = html_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_html(WEB_DIR / "index.html")
        elif path == "/api/health":
            self._send_json(
                HTTPStatus.OK,
                {"status": "ok", "service": "ai-token-calc-studio", "models": len(MODELS)},
            )
        elif path == "/api/models":
            self._send_json(HTTPStatus.OK, {"usd_to_cny": USD_TO_CNY, "models": MODELS})
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/api/estimate":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"invalid json: {exc}"})
            return

        prompt = str(data.get("prompt") or "")
        breakdown = estimate_tokens(prompt)
        manual_in = data.get("input_tokens")
        input_tokens = int(manual_in) if manual_in is not None else breakdown["total_tokens"]
        output_tokens = int(data.get("output_tokens") or 600)
        requests_per_day = int(data.get("requests_per_day") or 500)
        cache_hit_rate = float(data.get("cache_hit_rate") or 0.3)
        days = int(data.get("days") or 30)

        results = calculate_costs(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            requests_per_day=requests_per_day,
            cache_hit_rate=cache_hit_rate,
            days=days,
        )
        self._send_json(
            HTTPStatus.OK,
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "requests_per_day": requests_per_day,
                "cache_hit_rate": cache_hit_rate,
                "days": days,
                "token_breakdown": breakdown,
                "results": results,
            },
        )


def create_server(host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), TokenStudioHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Token 成本估算工作台")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8080")),
        help="HTTP 监听端口（默认 8080）",
    )
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    args = parser.parse_args()

    server = create_server(args.host, args.port)
    print(f"AI Token Cost Studio running at http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
