import aiohttp
import json
from astrbot.api import logger
from functools import wraps
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
import asyncio
from bs4 import BeautifulSoup
import chardet
from tinydb import TinyDB, Query
from pathlib import Path

from .config import NOTICE_LIST_PATH


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
                self.resp = await self.session.post(self.url, data=data, headers=headers, timeout=timeout,
                                                    proxy=self.proxy_url)
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


class NoticesDB:
    """本地存储，课程外通知与课程内活动分表保存

    TinyDB 按用途分成三张表：
      notice  课程外通知，来自通知列表接口，按 uuid 去重
      active  课程内活动，来自活动接口，按 active_id 去重，并记录是否已推送
      state   键值状态，如 schema 版本
    """

    SCHEMA_VERSION = 2

    def __init__(self, notice_res=None, notices_db_path=NOTICE_LIST_PATH):
        self.notice_res = notice_res
        self.db = TinyDB(Path(notices_db_path))
        self.notice_table = self.db.table("notice")
        self.active_table = self.db.table("active")
        self.state_table = self.db.table("state")
        self.query = Query()
        self._migrate()

    # ---------- 状态与迁移 ----------

    def _get_state(self, key, default=None):
        record = self.state_table.get(self.query.key == key)
        return record.get("value") if record else default

    def _set_state(self, key, value):
        if self.state_table.contains(self.query.key == key):
            self.state_table.update({"value": value}, self.query.key == key)
        else:
            self.state_table.insert({"key": key, "value": value})

    def _migrate(self):
        """把旧版 _default 表中的通知搬到 notice 表"""
        if (self._get_state("schema_version") or 0) >= self.SCHEMA_VERSION:
            return
        legacy = self.db.table("_default").all()
        migrated = 0
        for old in legacy:
            uuid = str(old.get("uuid") or "")
            if not uuid or self.notice_table.contains(self.query.uuid == uuid):
                continue
            self.notice_table.insert({
                "uuid": uuid,
                "title": old.get("title"),
                "content": old.get("content"),
                "creater_name": old.get("createrName"),
                "course_id": "",
                "class_id": "",
                "source_type": None,
                "source": None,
                "insert_time": old.get("insertTime"),
                "send_time": old.get("sendTime"),
                "is_read": old.get("isRead"),
                "seen_at": old.get("insertTime"),
            })
            migrated += 1
        self._set_state("schema_version", self.SCHEMA_VERSION)
        if migrated:
            logger.info(f"通知数据库已迁移{migrated}条旧记录")

    # ---------- 通知 ----------

    @staticmethod
    def _notice_record(item):
        tag = str(item.get("tag") or "")
        course_id = tag[len("courseId"):] if tag.startswith("courseId") else ""
        class_id = ""
        for receiver in item.get("receiverArray") or []:
            if isinstance(receiver, dict) and receiver.get("clazzId"):
                class_id = str(receiver["clazzId"])
                break
        return {
            "uuid": item.get("uuid"),
            "title": item.get("title"),
            "content": item.get("content"),
            "creater_name": item.get("createrName"),
            "course_id": course_id,
            "class_id": class_id,
            "source_type": item.get("sourceType"),
            "source": item.get("source"),
            "insert_time": item.get("insertTime"),
            "send_time": item.get("sendTime"),
            "is_read": item.get("isread"),
            "seen_at": int(datetime.now().timestamp() * 1000),
        }

    def save_notices_db(self):
        """保存通知列表中尚未入库的记录，返回新增条数"""
        notices = ((self.notice_res or {}).get("notices") or {}).get("list") or []
        new_count = 0
        for item in notices:
            uuid = str(item.get("uuid") or "")
            if not uuid:
                continue
            if self.notice_table.contains(self.query.uuid == uuid):
                continue
            self.notice_table.insert(self._notice_record(item))
            new_count += 1
        return new_count

    def look_up_notices_by_isRead(self, isRead):
        """按已读状态查询通知"""
        return self.notice_table.search(self.query.is_read == int(isRead))

    def look_up_notices_by_timestamp(self, startTime, endTime):
        """按时间区间查询通知，区间不得短于一天

        参数格式：YYYY-MM-DD-HH-MM
        """
        dt_s = datetime.strptime(startTime, "%Y-%m-%d-%H-%M")
        dt_e = datetime.strptime(endTime, "%Y-%m-%d-%H-%M")
        startTimestamp = int(dt_s.timestamp()*1000)
        endTimestamp = int(dt_e.timestamp()*1000)
        oneDayTimestamp = 1*24*60*60*1000
        if startTimestamp > endTimestamp:
            logger.error("开始时间不能大于结束时间")
            return []
        # 短于一天才拒绝；恰好一天是允许的最短区间
        if endTimestamp - startTimestamp < oneDayTimestamp:
            logger.error("可查询的最短时间间隔为一天")
            return []
        return self.notice_table.search((self.query.insert_time > startTimestamp) &
                                        (self.query.insert_time < endTimestamp))

    def look_up_all(self):
        return self.notice_table.all()

    # ---------- 课程活动 ----------

    def save_actives(self, actives, course_id, class_id):
        """保存活动列表，返回首次出现的活动记录

        actives 为活动接口返回的 activeList；已存在的活动更新 status、
        end_time、remaining、last_seen 四个字段。
        """
        new_actives = []
        now = int(datetime.now().timestamp() * 1000)
        for item in actives or []:
            active_id = str(item.get("id") or "")
            if not active_id:
                continue
            status = item.get("status")
            end_time = item.get("endTime")
            remaining = item.get("nameFour")
            if self.active_table.contains(self.query.active_id == active_id):
                self.active_table.update(
                    {"status": status, "end_time": end_time,
                     "remaining": remaining, "last_seen": now},
                    self.query.active_id == active_id)
                continue
            record = {
                "active_id": active_id,
                "active_type": item.get("activeType"),
                "name": item.get("nameOne"),
                "course_id": str(course_id or ""),
                "class_id": str(class_id or ""),
                "start_time": item.get("startTime"),
                "end_time": end_time,
                "status": status,
                "user_status": item.get("userStatus"),
                "remaining": remaining,
                "pushed": False,
                "last_seen": now,
            }
            self.active_table.insert(record)
            new_actives.append(record)
        return new_actives

    def is_active_pushed(self, active_id):
        record = self.active_table.get(self.query.active_id == str(active_id))
        return bool(record and record.get("pushed"))

    def mark_active_pushed(self, active_id):
        self.active_table.update({"pushed": True}, self.query.active_id == str(active_id))

    # ---------- 维护 ----------

    def prune(self, days=30):
        """删除超过指定天数的通知与活动，返回删除条数"""
        deadline = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
        removed = 0
        removed += len(self.notice_table.remove(self.query.insert_time < deadline))
        removed += len(self.active_table.remove(self.query.end_time < deadline))
        return removed

    def stats(self):
        return {"notices": len(self.notice_table), "actives": len(self.active_table)}

    def close_db(self):
        try:
            self.db.close()
        except Exception as e:
            logger.warning(f"关闭数据库时出错(已忽略):{e}")
