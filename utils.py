import aiohttp
import asyncio
import json
from astrbot.api import logger
from functools import wraps
from zoneinfo import ZoneInfo
from datetime import datetime
import random
import asyncio


# 发送请求
async def get_request(session, url, data=None, headers=None, Method="GET", TIMEOUT=10):
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    try:
        if Method == "GET":
            resp = await session.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        if Method == "POST":
            resp = await session.post(url=url, data=data, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
    except Exception as e:
        logger.error(f"请求失败: {e}")
        return None


# 走代理
class GetRequest:
    def __init__(self, url, session, proxy_url, **kwargs):
        self.proxy_url = proxy_url
        self.kwargs = kwargs
        self.session = session
        self.url = url
        self.resp = None

    async def __aenter__(self):
        try:
            method = self.kwargs.get('method', 'GET')
            headers = self.kwargs.get('headers', {})
            timeout = aiohttp.ClientTimeout(total=self.kwargs.get('timeout', 10))
            if method == 'GET':
                self.resp = await self.session.get(self.url, headers=headers, timeout=timeout, proxy=self.proxy_url)
                self.resp.raise_for_status()
                return self.resp
            if method == 'POST':
                data = self.kwargs.get('data', {})
                self.resp = await self.session.post(self.url, data=data, headers=headers, timeout=timeout, proxy=self.proxy_url)
                self.resp.raise_for_status()
                return self.resp
        except Exception as e:
            logger.error(f"请求失败:{e}")
            return None

    async def __aexit__(self, *args):
        if self.resp:
            self.resp.close()


# 定时器，在无通知时间段停止请求
def time_limit(s_h: int, s_m: int, e_h: int, e_m: int):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            while True:
                now = datetime.now(ZoneInfo("Asia/Shanghai"))
                now_min = now.hour * 60 + now.minute
                start_min = s_h * 60 + s_m
                end_min = e_h * 60 + e_m
                if start_min <= now_min <= end_min:
                    return await func(*args, **kwargs)
                if now_min < start_min:
                    wait_seconds = (start_min - now_min) * 60
                else:
                    wait_seconds = (24 * 60 - now_min + start_min) * 60
                logger.info(f"当前时间 {now.strftime('%H:%M')}，不在允许时段，{wait_seconds // 60} 分钟后可以获取通知")
                await asyncio.sleep(min(wait_seconds, 60))

        return wrapper

    return decorator


# 普通文件的读取和写入
def safe_read(fileName, default=None):
    try:
        with open(fileName, 'r') as f:
            return f.read()
    except (FileNotFoundError, PermissionError, OSError) as e:
        logger.error(f"读取文件失败: {e}")
        return default


def safe_write(fileName, content, mode='w'):
    try:
        with open(fileName, mode) as f:
            f.write(content)
        return True
    except (FileNotFoundError, PermissionError, OSError) as e:
        logger.error(f"写入文件失败: {e}")
        return False


# json的读取和写入
def json_read(fileName, default=None, coding='utf-8'):
    try:
        with open(fileName, 'r', encoding=coding) as f:
            return json.load(f)
    except (FileNotFoundError, PermissionError, OSError) as e:
        logger.error(f"读取JSON失败: {e}")
        return default


def json_write(fileName, content, mode='w', coding='utf-8'):
    try:
        with open(fileName, mode, encoding=coding) as f:
            json.dump(content, f, ensure_ascii=False)
            return True
    except (FileNotFoundError, PermissionError, OSError) as e:
        logger.error(f"写入JSON失败: {e}")
        return False


# 处理网页回复的json
async def fetch_json(aioHttpResObj=None):
    try:
        if aioHttpResObj is None:
            logger.error("返回的对象为None")
            return None
        text = await aioHttpResObj.text()
        data = json.loads(text)
        return data
    except json.JSONDecodeError:
        logger.error(f"非json响应：-> {text[0:200]}")
    except Exception as e:
        logger.error(f"解析json失败，{e}")
    return None
