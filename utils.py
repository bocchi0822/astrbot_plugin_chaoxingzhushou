import aiohttp
import json
from astrbot.api import logger
from functools import wraps
from zoneinfo import ZoneInfo
from datetime import datetime
import asyncio
from bs4 import BeautifulSoup
import chardet
from tinydb import TinyDB, Query
from pathlib import Path


# 发送请求
async def get_request(session, url, data=None, headers=None, Method="GET", TIMEOUT=10):
    """
    发送HTTP请求，不走代理途径，session接受会话，url接受目标网址
    """
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
    """发送HTTP请求，走代理途径"""
    def __init__(self, url, session, proxy_url, **kwargs):
        self.proxy_url = proxy_url
        self.kwargs = kwargs
        self.session = session
        self.url = url
        self.resp = None

    async def __aenter__(self):
        """
        走代理发送请求
        """
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


def time_limit(s_h: int, s_m: int, e_h: int, e_m: int):
    """定时器，在无通知时间段停止请求"""
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


def safe_read(fileName, default=None):
    """普通文件的读取"""
    try:
        with open(fileName, 'r') as f:
            return f.read()
    except (FileNotFoundError, PermissionError, OSError) as e:
        logger.error(f"读取文件失败: {e}")
        return default


def safe_write(fileName, content, mode='w'):
    """普通文件的写入"""
    try:
        with open(fileName, mode) as f:
            f.write(content)
        return True
    except (FileNotFoundError, PermissionError, OSError) as e:
        logger.error(f"写入文件失败: {e}")
        return False


def json_read(fileName, default=None, coding='utf-8'):
    """json的读取"""
    try:
        with open(fileName, 'r', encoding=coding) as f:
            return json.load(f)
    except (FileNotFoundError, PermissionError, OSError) as e:
        logger.error(f"读取JSON失败: {e}")
        return default


def json_write(fileName, content, mode='w', coding='utf-8'):
    """json的写入"""
    try:
        with open(fileName, mode, encoding=coding) as f:
            json.dump(content, f, ensure_ascii=False)
            return True
    except (FileNotFoundError, PermissionError, OSError) as e:
        logger.error(f"写入JSON失败: {e}")
        return False


async def fetch_json(aioHttpResObj=None):
    """处理网页返回的json"""
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


class FetchHtml:
    """解析登录网站"""
    def __init__(self, aioHttpResObj=None):
        self._res = aioHttpResObj
        self._soup = None
        self._status = aioHttpResObj.status

    async def _parse_html(self):
        """
        通过bs4解析html网页，会自动匹配编码
        """
        if self._status != 200:
            raise ValueError(f"http状态码非200,状态码为{self._status}")
        raw_bytes = await self._res.read()
        detected = chardet.detect(raw_bytes)
        encoding = detected['encoding'] or 'utf-8'
        html = raw_bytes.decode(encoding)
        self._soup = BeautifulSoup(html, "html.parser", from_encoding=encoding)

    async def fetch_id(self, _id, *attrs_name):
        """
        通过id获取该id所属标签其他属性的值，_id接受id的值，*attrs_name目的属性
        """
        if self._soup is None:
            await self._parse_html()
        tag = self._soup.find_all(id=_id)
        if len(tag) > 1 or len(tag) == 0:
            raise ValueError("存在多个重复id或者不存在id")
        target = tag[0]
        attrs = target.attrs
        result = {}
        for item in attrs_name:
            if item in attrs:
                result[item] = target.get(item)
        return result


class SaveDataByTinyDb:
    """
    通过jql保持信息
    """
    def __init__(self, db_path):
        self.db_path = db_path
        self.db = None
        self.q = Query()
        self._isDb = False

    def create_db(self):
        if not self._isDb:
            self.db = TinyDB(self.db_path)
            self._isDb = True
            return self.db

    def add_db(self, data: list):
        if self._isDb:
            self.db.insert_multiple(data)
            return True
        logger.error("请先创建数据表")
        return False

    def search_db(self, **kwargs):
        if self._isDb:
            cond = None
            for k, v in kwargs.items():
                c = getattr(self.db, k) == v
                if cond is None:
                    cond = c
                else:
                    cond = cond & c
            return self.db.search(cond)

    def delete_db(self, **kwargs):
        if self._isDb:
            cond = None
            for k, v in kwargs.items():
                c = getattr(self.db, k) == v
                if cond is None:
                    cond = c
                else:
                    cond = cond & c
            self.db.delete(cond)
            return True

    def update_db(self, **kwargs):
        pass



