import re
import random
import base64
import time
import requests
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from astrbot.api.all import *

SENTRY_BASE = "https://sentry.platorelay.com/.gs/pow/captcha"

BASE_TELEMETRY = {
    "dwellMs": 446629,
    "moves": 592,
    "velocityVar": 17.2058786473109,
    "velocityMedian": 1.455788671386738,
    "velocityAvg": 3.2309785421350123,
    "velocityMin": 0.0005871534893303571,
    "velocityMax": 18.108148421848494,
    "velocityP25": 0.42923229467905805,
    "velocityP75": 3.793246599138705,
    "directionChanges": 31,
    "keypresses": 0,
    "speedSamples": 592,
    "moveDensity": 754.4408783783783
}

def generate_telemetry(variation=0.1):
    telemetry = {}
    for key, value in BASE_TELEMETRY.items():
        factor = 1 + random.uniform(-variation, variation)
        telemetry[key] = value * factor
    telemetry["dwellMs"] = int(telemetry["dwellMs"])
    telemetry["moves"] = int(telemetry["moves"])
    telemetry["directionChanges"] = int(telemetry["directionChanges"])
    telemetry["keypresses"] = 0
    telemetry["speedSamples"] = telemetry["moves"]
    return telemetry

def generate_fingerprint():
    return "-" + ''.join(random.choices("0123456789abcdef", k=8))

def decode_base64_url(raw_url):
    parsed = urlparse(raw_url)
    if parsed.path.endswith('/a') or 'a?' in raw_url:
        query = parse_qs(parsed.query)
        if 'd' in query:
            d_param = query['d'][0]
            try:
                decoded = base64.b64decode(d_param).decode('utf-8')
                if decoded.startswith('http'):
                    return decoded
                else:
                    return f"{parsed.scheme}://{parsed.netloc}{decoded}"
            except Exception:
                pass
    return raw_url

def extract_card_key(html):
    soup = BeautifulSoup(html, 'html.parser')
    selectors = [
        '#card-key', '.voucher-code', 'pre', 'code',
        'div[class*="card"]', 'p[class*="key"]', 'span[class*="code"]'
    ]
    for selector in selectors:
        elem = soup.select_one(selector)
        if elem:
            return elem.get_text(strip=True)
    match = re.search(r'[A-Z0-9]{16}', html)
    if match:
        return match.group()
    return None

class CaptchaSolver:
    def solve(self, puzzle_data):
        instruction = puzzle_data["puzzle"]["instruction"].lower()
        shapes = puzzle_data["puzzle"]["shapes"]

        if "largest" in instruction or "smallest" in instruction:
            return self._solve_size_comparison(instruction, shapes)
        elif "find" in instruction:
            return self._solve_find_object(instruction, shapes)
        elif "rotate" in instruction or "align" in instruction:
            return self._solve_rotate(instruction, shapes)
        else:
            raise ValueError(f"未知指令: {instruction}")

    def _solve_size_comparison(self, instruction, shapes):
        match = re.search(r"(largest|smallest) (\w+)", instruction)
        if not match:
            raise ValueError(f"无法解析大小比较指令: {instruction}")
        comparator = match.group(1)
        shape_type = match.group(2)

        candidates = [(i, s) for i, s in enumerate(shapes) if s["type"].lower() == shape_type.lower()]
        if not candidates:
            raise ValueError(f"未找到类型 {shape_type}")

        if comparator == "largest":
            target = max(candidates, key=lambda x: x[1]["size"])
        else:
            target = min(candidates, key=lambda x: x[1]["size"])
        return target[0]

    def _solve_find_object(self, instruction, shapes):
        words = instruction.split()
        target_type = words[-1] if words else ""
        for i, s in enumerate(shapes):
            if s["type"].lower() == target_type.lower():
                return i
        raise ValueError(f"未找到类型 {target_type}")

    def _solve_rotate(self, instruction, shapes):
        if not shapes:
            raise ValueError("没有图形")
        current_orientation = shapes[0].get("orientation", 0)
        required_rotation = (360 - current_orientation) % 360
        return required_rotation

def bypass_captcha(session):
    telemetry = generate_telemetry()
    fingerprint = generate_fingerprint()
    req_payload = {
        "telemetry": telemetry,
        "deviceFingerprint": fingerprint,
        "forcePuzzle": False
    }
    try:
        r = session.post(f"{SENTRY_BASE}/request", json=req_payload, timeout=15)
        r.raise_for_status()
        puzzle_response = r.json()
    except Exception as e:
        raise Exception(f"获取拼图失败: {e}")
    if "puzzle" not in puzzle_response:
        raise Exception("响应中无拼图数据")
    try:
        solver = CaptchaSolver()
        answer = solver.solve(puzzle_response)
    except Exception as e:
        raise Exception(f"解答拼图失败: {e}")
    verify_payload = {
        "id": puzzle_response["id"],
        "answer": answer
    }
    try:
        v = session.post(f"{SENTRY_BASE}/verify", json=verify_payload, timeout=15)
        v.raise_for_status()
        verify_result = v.json()
    except Exception as e:
        raise Exception(f"验证失败: {e}")
    return session

class CardKeyGetter(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.enable_auto_detect = True
        self.auto_detect_domains = [
            "auth.platorelay.com",
            "auth.platoboost.com",
            "auth.platoboost.app",
            "auth.platoboost.net",
            "deltaios-executor.com"
        ]

    @event_message_type(EventMessageType.ALL)
    async def on_event(self, event: AstrMessageEvent):
        if event.message_str.startswith('/getkey'):
            await self.handle_getkey(event)
            return

        if self.enable_auto_detect and event.is_group:
            for domain in self.auto_detect_domains:
                pattern = rf'(?:https?://)?{re.escape(domain)}[^\s]+'
                match = re.search(pattern, event.message_str)
                if match:
                    raw_url = match.group()
                    if not raw_url.startswith(('http://', 'https://')):
                        raw_url = 'https://' + raw_url
                    await self.process_url(event, raw_url)
                    return

    async def handle_getkey(self, event: AstrMessageEvent):
        parts = event.message_str.strip().split(maxsplit=1)
        if len(parts) < 2:
            yield event.make_result().message("请提供链接，例如：/getkey https://auth.platorelay.com/a?d=...")
            return
        raw_url = parts[1].strip()
        await self.process_url(event, raw_url)

    async def process_url(self, event: AstrMessageEvent, raw_url: str):
        start_time = time.time()
        yield event.make_result().message(f"⏳ 自动检测到 Plato 链接，开始解析: {raw_url}")

        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })

        try:
            target_url = decode_base64_url(raw_url)
            yield event.make_result().message(f"🔍 目标地址: {target_url}")

            resp = session.get(target_url, timeout=15)
            if resp.status_code != 200:
                elapsed = time.time() - start_time
                yield event.make_result().message(f"❌ 页面访问失败，状态码: {resp.status_code}（耗时 {elapsed:.2f} 秒）")
                return

            if 'sentry' in resp.url or 'captcha' in resp.text.lower():
                yield event.make_result().message("🛡️ 检测到验证码，尝试绕过...")
                try:
                    session = bypass_captcha(session)
                except Exception as e:
                    elapsed = time.time() - start_time
                    yield event.make_result().message(f"❌ 验证码绕过失败: {e}（耗时 {elapsed:.2f} 秒）")
                    return
                resp = session.get(target_url, timeout=15)
                if resp.status_code != 200:
                    elapsed = time.time() - start_time
                    yield event.make_result().message(f"❌ 验证后页面访问失败，状态码: {resp.status_code}（耗时 {elapsed:.2f} 秒）")
                    return

            card_key = extract_card_key(resp.text)
            elapsed = time.time() - start_time
            if card_key:
                yield event.make_result().message(f"✅ 获取到卡密：{card_key}（耗时 {elapsed:.2f} 秒）")
            else:
                yield event.make_result().message(f"❌ 未能在页面中找到卡密，请检查链接或调整解析规则。（耗时 {elapsed:.2f} 秒）")

        except Exception as e:
            elapsed = time.time() - start_time
            yield event.make_result().message(f"❌ 处理过程中发生异常: {e}（耗时 {elapsed:.2f} 秒）")
