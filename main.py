from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star
from astrbot.api import logger
from .config import FID, SPACE, BASE_URL, HEADERS, GET_STATUS, NOTICE_URL, QRCODE_PATH, COOKIES_PATH, NOTICE_LIST_PATH, \
    DATA_DIR, UMO_PATH, YEAR, ACTIVE_LIST_URL, ACTIVE_FID, ACTIVE_TYPES, ACTIVE_URGENT_TYPES
import aiohttp
import time
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from .utils import get_request, time_limit, safe_write, json_write, json_read, fetch_json, safe_read, FetchHtml, \
    NoticesDB
import pickle
from pathlib import Path
import asyncio
import random
from tinydb import TinyDB, Query

# 从通知正文提取字段用的正则
# 作业类以 \r 分行，任务类以逗号分隔
RE_COURSE_NAME = re.compile(r"课程名称[:：]\s*([^,\r\n]+)")
RE_ASSIGN_TIMES = re.compile(r"开始时间[:：]\s*([\d\-: ]+?)\s*[\r\n]+\s*结束时间[:：]\s*([\d\-: ]+)")

# 活动轮询间隔（秒）
ACTIVE_POLL_SECONDS = 20
# 单轮最多轮询的课程数，避免课程多时请求放大
ACTIVE_MAX_COURSES = 5


class ChaoXingPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        self.session = None
        self.is_cookies = False
        self.refer = None
        self.logged_in = False
        self._notice_task = None
        self._active_task = None
        self._validate_task = None
        self._have_new_notice = False
        self._umo = None
        self.notices_db = None

    def _create_refer(self):
        """生成带时间戳的refer，用于请求登录页面"""
        t = str(int(time.time() * 1000))
        refer = f"http://i.chaoxing.com/base?t={t}"
        return refer

    async def _get_uuid_enc(self, session, refer):
        """获取登录页面中服务器生成的uuid、enc和图片地址"""
        url = f'{BASE_URL}/login?fid={FID}&refer={refer}&space={SPACE}'
        response = await get_request(session, url, headers=HEADERS, Method="GET")
        if response is None:
            logger.error("获取登录页面失败")
            return None
        fetchData = FetchHtml(response)
        login_html = await response.text()
        # 解析uuid和enc
        uuid = await fetchData.fetch_id("uuid", "value")
        enc = await fetchData.fetch_id("enc", "value")
        # 获取qrcode的地址
        img_match = await fetchData.fetch_id("quickCode", "src")
        if not uuid or not enc:
            logger.error(f"uuid/enc未匹配到，HTML片段: {login_html[:2000]}")
            return None
        if not img_match:
            logger.error(f"二维码图片未匹配到，HTML片段: {login_html[:2000]}")
            return None
        # 拼接二维码完整地址
        img_url = f"{BASE_URL}{img_match['src']}"
        return uuid['value'], enc['value'], img_url

    async def _get_qrcode(self, session, img_url):
        """获取并保存二维码"""
        response = await get_request(session, img_url, headers=HEADERS, Method="GET")
        if response is None:
            logger.error("获取二维码失败")
            return None
        try:
            data = await response.read()
            safe_write(QRCODE_PATH, data, mode='wb')
            logger.info(f"二维码已保存到{QRCODE_PATH}")
            return QRCODE_PATH
        except Exception as e:
            logger.error(f"二维码保存失败: {e}")

    async def _get_status(self, session, refer, uuid, enc):
        """在登陆时获取登录状态并且登录成功后获取cookies"""
        url = f'{BASE_URL}/{GET_STATUS}'
        get_status_header = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Host": "passport2.chaoxing.com",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{BASE_URL}/login?fid={FID}&refer={refer}&space={SPACE}",
        }
        data = {
            "enc": enc,
            "uuid": uuid,
            "doubleFactorLogin": 0,
            "forbidotherlogin": 0,
        }
        response = await get_request(session, url, data, headers=get_status_header, Method="POST")
        if response is None:
            logger.error("获取登录状态失败，请求无响应")
            return None
        content_type = response.headers.get("Content-Type") or ''
        if 'application/json' not in content_type:
            return await fetch_json(response)
        return await response.json()

    def _save_cookies(self, session):
        """保存cookies到data目录"""
        try:
            cookies_dict = {}
            for cookie in session.cookie_jar:
                cookies_dict[cookie.key] = cookie.value
            with open(COOKIES_PATH, 'wb') as f:
                pickle.dump(cookies_dict, f)
                self.is_cookies = True
                logger.info(f"cookies已保存到{COOKIES_PATH}")
        except Exception as e:
            logger.error(f"保存cookies时出错,{e}")

    def _notice_query_data(self):
        """通知列表接口的固定请求参数"""
        return {
            "type": 2,
            "notice_type": '',
            "lastValue": '',
            "sort": '',
            "folderUUID": '',
            "kw": '',
            "startTime": '',
            "endTime": '',
            "gKw": '',
            "gName": '',
            "year": YEAR,
            "tag": '',
            "fidsCode": '',
            "queryFolderNoticePrevYear": 0,
            "filterSenderPuids": '',
            "filterTags": '',
        }

    async def _get_notice_list(self, session):
        """获取通知列表"""
        res = await get_request(session, NOTICE_URL, data=self._notice_query_data(),
                                headers=HEADERS, Method="POST")
        if res is None:
            logger.error("通知列表为None")
            return None
        # 判断登录状态
        content_type = res.headers.get('Content-Type') or ''
        if res.status == 302 or "text/html" in content_type:
            logger.warning("cookie失效，请重新登录")
            Path(COOKIES_PATH).unlink(missing_ok=True)
            self.is_cookies = False
            return None
        payload = await fetch_json(res)
        if payload is None:
            logger.error(f"通知接口未返回合法JSON，状态码{res.status}")
            return None
        # 接口异常时会返回不含 notices 的数据
        if not isinstance(payload, dict) or not isinstance(payload.get('notices'), dict) \
                or not isinstance(payload['notices'].get('list'), list):
            logger.error(f"通知接口返回结构异常，缺少notices.list字段，响应片段：{str(payload)[:300]}")
            return None
        return payload

    async def _get_active_list(self, course_id, class_id):
        """获取课程活动列表

        course_id 取自通知的 tag 字段，class_id 取自 receiverArray[].clazzId。
        返回 data.activeList 数组；请求失败或结构异常时返回 None。
        """
        params = {
            "fid": ACTIVE_FID,
            "classId": class_id,
            "courseId": course_id,
            "_": int(time.time() * 1000),
        }
        url = f"{ACTIVE_LIST_URL}?{urlencode(params)}"
        res = await get_request(self.session, url, headers=HEADERS, Method="GET")
        if res is None:
            logger.error(f"获取活动列表失败：courseId={course_id} classId={class_id}")
            return None
        payload = await fetch_json(res)
        if not isinstance(payload, dict):
            logger.error(f"活动接口返回非JSON内容，状态码{res.status}")
            return None
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("activeList"), list):
            logger.error(f"活动接口返回结构异常，响应片段：{str(payload)[:200]}")
            return None
        return data["activeList"]

    def _active_targets(self):
        """从通知记录中取出待轮询的 (courseId, classId) 组合"""
        db = NoticesDB([], NOTICE_LIST_PATH)
        try:
            records = db.look_up_all()
        finally:
            db.close_db()
        targets = []
        seen = set()
        for record in records:
            course_id = str(record.get("course_id") or "")
            class_id = str(record.get("class_id") or "")
            if not course_id or not class_id:
                continue
            key = (course_id, class_id)
            if key in seen:
                continue
            seen.add(key)
            targets.append(key)
        return targets[:ACTIVE_MAX_COURSES]

    def _course_name_by_id(self, course_id):
        """按课程 ID 从通知记录中取课程名称"""
        db = NoticesDB([], NOTICE_LIST_PATH)
        try:
            records = db.look_up_all()
        finally:
            db.close_db()
        for record in records:
            if str(record.get("course_id") or "") == str(course_id):
                name = self._course_name(record)
                if name:
                    return name
        return ""

    def _format_active(self, record, course_name):
        """把一条活动格式化为待推送的文本"""
        active_type = record.get("active_type")
        label = ACTIVE_TYPES.get(active_type, "活动")
        urgent = active_type in ACTIVE_URGENT_TYPES
        lines = ["🔴 学习通活动提醒" if urgent else "学习通活动提醒"]
        lines.append(f"【{label}】{record.get('name') or ''}".rstrip())
        remaining = str(record.get("remaining") or "")
        if remaining:
            lines.append(remaining)
        if course_name:
            lines.append(f"课程：{course_name}")
        return "\n".join(lines)

    async def _get_actives_loop(self):
        """轮询课程活动，把新出现的活动推送到绑定会话"""
        if not self._umo:
            logger.error("未找到推送目标会话(umo)，活动轮询未启动")
            return
        targets = self._active_targets()
        if not targets:
            logger.warning("通知记录中没有 courseId/classId，活动轮询未启动")
            return
        db = NoticesDB([], NOTICE_LIST_PATH)
        # 轮询开始之前就已存在的活动只入库，不推送
        watching_since = int(time.time() * 1000)
        try:
            while True:
                await asyncio.sleep(ACTIVE_POLL_SECONDS)
                try:
                    for course_id, class_id in targets:
                        actives = await self._get_active_list(course_id, class_id)
                        if not actives:
                            continue
                        course_name = self._course_name_by_id(course_id)
                        for record in db.save_actives(actives, course_id, class_id):
                            active_id = record.get("active_id")
                            started = int(record.get("start_time") or 0)
                            if record.get("active_type") == 45 or record.get("status") != 1 \
                                    or started < watching_since:
                                db.mark_active_pushed(active_id)
                                continue
                            text = self._format_active(record, course_name)
                            await self.context.send_message(self._umo, MessageChain().message(text))
                            db.mark_active_pushed(active_id)
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.error(f"活动轮询出错{e}")
        finally:
            db.close_db()

    def _start_actives_loop(self):
        """启动活动轮询后台任务，已在运行时不重复创建"""
        if self._active_task and not self._active_task.done():
            return False
        self._active_task = asyncio.create_task(self._get_actives_loop())
        logger.info("已开启课程活动监听")
        return True

    async def _stop_actives_loop(self):
        """取消活动轮询后台任务并等待其退出"""
        task = self._active_task
        self._active_task = None
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"停止活动轮询时出错:{e}")
        return True

    async def _probe_login(self):
        """探测本地 cookies 是否仍然有效

        返回 "valid"（有效）、"invalid"（已失效）、"unknown"（网络异常，无法判断）。
        """
        res = await get_request(self.session, NOTICE_URL, data=self._notice_query_data(),
                                headers=HEADERS, Method="POST")
        if res is None:
            return "unknown"
        content_type = res.headers.get('Content-Type') or ''
        if res.status == 302 or "text/html" in content_type:
            return "invalid"
        payload = await fetch_json(res)
        if payload is None:
            return "unknown"
        if not isinstance(payload, dict) or not isinstance(payload.get('notices'), dict) \
                or not isinstance(payload['notices'].get('list'), list):
            logger.error(f"登录态探测返回结构异常，响应片段：{str(payload)[:300]}")
            return "invalid"
        return "valid"

    async def _validate_login_state(self):
        """启动时校验本地 cookies 是否仍有效，有效则自动开启通知轮询"""
        state = await self._probe_login()
        if state == "valid":
            self.is_cookies = True
            logger.info("本地cookies有效，自动开启通知轮询")
            self._start_notices_loop()
        elif state == "invalid":
            self.is_cookies = False
            self.session.cookie_jar.clear()
            Path(COOKIES_PATH).unlink(missing_ok=True)
            logger.warning("本地cookies已失效，已清除，请重新执行[/获取二维码]登录")
            await self._notify_login_issue(
                "学习通登录状态已失效，通知推送已停止。\n请重新执行 /获取二维码 扫码登录。"
            )
        else:
            logger.warning("网络异常，无法确认本地cookies是否有效；未自动开启轮询，"
                           "可稍后手动执行[/开启通知推送]")
            await self._notify_login_issue(
                "无法确认学习通登录状态（网络异常），通知推送未自动开启。\n"
                "可稍后执行 /开启通知推送 重试。"
            )

    async def _notify_login_issue(self, text):
        """把登录相关的提示推送到已绑定的会话

        text 为要发送的完整文案。未绑定会话或推送失败时记录日志。
        """
        if not self._umo:
            logger.warning("未绑定会话，无法推送登录状态提示")
            return
        try:
            chain = MessageChain().message(text)
            await self.context.send_message(self._umo, chain)
            logger.info("已推送登录状态提示")
        except Exception as e:
            logger.error(f"推送登录状态提示失败:{e}")

    async def _cancel_login_check(self):
        """取消启动时的登录态校验任务（若仍在运行）"""
        task = self._validate_task
        self._validate_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # 通知内容格式化：作业与公告分开推送

    def _notice_kind(self, item):
        """判定通知类型：作业/考试类，还是普通公告"""
        return "作业" if item.get("sourceType") == 14 else "公告"

    def _course_name(self, item):
        """从正文的“课程名称：”字段取课程名称，取不到时返回空串

        正文片段形如“课程名称：针灸学”。
        """
        match = RE_COURSE_NAME.search(str(item.get("content") or ""))
        if not match:
            return ""
        return match.group(1).strip()

    def _assign_times(self, item):
        """取出作业的截止时间文本；正文里没有起止时间时返回 None

        正文格式形如：作业名称：xxx\\r开始时间：2026-10-14 11:07\\r结束时间：2026-10-17 12:07
        """
        m = RE_ASSIGN_TIMES.search(str(item.get("content") or ""))
        return m.group(2).strip() if m else None

    def _deadline_text(self, end_str):
        """把截止时间格式化成带倒计时的文本，结束时间距今超过一年时返回空串"""
        try:
            end = datetime.strptime(end_str, "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            return ""
        now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        left = (end - now).total_seconds()
        if left < 0:
            return f"已截止（{end_str}）"
        days = left / 86400
        # 超星对部分作业会把结束时间填成十几年后，这种不是真实截止时间
        if days > 365:
            return ""
        if days >= 1:
            return f"截止：{end_str}（剩{days:.1f}天）"
        hours = left / 3600
        if hours >= 1:
            return f"截止：{end_str}（剩{hours:.1f}小时）"
        return f"截止：{end_str}（剩{left / 60:.0f}分钟）"

    def _is_personal_sender(self, item):
        """判断通知是否由个人发送

        source=1 为个人发送（如任课老师），source=2 为机构或系统群发。
        """
        return item.get("source") == 1

    def _format_one_notice(self, item):
        """把单条通知格式化为待推送的文本块

        作业类输出类型、标题、截止时间、课程名称；公告类在此基础上追加正文各行。
        个人发送的通知在类型后标出发送者。
        """
        kind = self._notice_kind(item)
        title = str(item.get("title") or "（无标题）").strip()
        course = self._course_name(item)
        header = f"【{kind}】"
        if self._is_personal_sender(item):
            header = f"【{kind}·{item.get('createrName')}】"
        lines = [f"{header}{title}"]

        end_str = self._assign_times(item)
        deadline = self._deadline_text(end_str) if end_str else ""
        if deadline:
            lines.append(deadline)
        if course:
            lines.append(f"课程：{course}")

        # 公告类追加正文，正文中的转义 \r\n 先还原为换行
        if kind == "公告":
            body = str(item.get("content") or "").replace("\\r", "\n").replace("\\n", "\n").strip()
            for line in body.splitlines():
                line = line.strip()
                # 跳过空行及与标题、课程名重复的行
                if not line or RE_COURSE_NAME.match(line) or line in (title, deadline):
                    continue
                lines.append(line)
        return "\n".join(lines)

    def _build_push_text(self, notices):
        """把本轮新增通知拼成一条消息，作业与公告分区展示"""
        # 先剔除非法项，再按 uuid 去重，最后按时间倒序（最新的排最前）
        if not notices:
            return "学习通新通知 0 条"
        valid = []
        for item in notices:
            if not isinstance(item, dict):
                continue
            if not item.get("uuid"):
                continue
            valid.append(item)

        seen = set()
        unique = []
        for item in valid:
            uuid = str(item.get("uuid"))
            if uuid in seen:
                continue
            seen.add(uuid)
            unique.append(item)
        unique.sort(key=lambda i: i.get("insertTime") or 0, reverse=True)

        assignments = [i for i in unique if self._notice_kind(i) == "作业"]
        announcements = [i for i in unique if self._notice_kind(i) == "公告"]

        blocks = [f"学习通新通知 {len(unique)} 条"]
        if assignments:
            blocks.append(f"—— 作业 {len(assignments)} 条 ——")
            blocks.extend(self._format_one_notice(i) for i in assignments)
        if announcements:
            blocks.append(f"—— 公告 {len(announcements)} 条 ——")
            blocks.extend(self._format_one_notice(i) for i in announcements)
        return "\n\n".join(blocks)

    async def _parse_notice_list(self, notice_res):
        """解析通知列表"""
        try:
            self.notices_db = NoticesDB(notice_res, NOTICE_LIST_PATH)
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, self.notices_db.save_notices_db)
            logger.info(f"新增了{result}条通知")
            return result

        except Exception as e:
            logger.error(f"解析通知时出错,{e}")

    def _start_notices_loop(self):
        """启动通知与活动轮询后台任务，已在运行时不重复创建"""
        if self._notice_task and not self._notice_task.done():
            return False
        self._notice_task = asyncio.create_task(self._get_notices_loop())
        self._start_actives_loop()
        logger.info("已开启通知列表监听")
        return True

    async def _stop_notices_loop(self):
        """取消通知与活动轮询后台任务并等待其真正退出"""
        await self._stop_actives_loop()
        task = self._notice_task
        self._notice_task = None
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"停止通知轮询时出错:{e}")
        logger.info("已停止通知列表监听")
        return True

    async def initialize(self):
        """插件初始化：创建 session、加载本地 cookies，并在后台校验其有效性"""
        self.refer = self._create_refer()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        jar = aiohttp.CookieJar()
        self.session = aiohttp.ClientSession(cookie_jar=jar)
        self._umo = safe_read(Path(UMO_PATH))
        cookies_path = Path(COOKIES_PATH)
        if cookies_path.exists():
            try:
                with open(COOKIES_PATH, 'rb') as f:
                    cookies_dict = pickle.load(f)
                self.session.cookie_jar.update_cookies(cookies_dict)
                self.is_cookies = True
                logger.info("本地cookies加载成功，开始校验是否仍然有效")
            except Exception as e:
                logger.error(f"本地cookies加载失败:{e}")
                self.is_cookies = False
        else:
            logger.warning("未发现本地cookies，请先执行[/获取二维码]登录")
            self.is_cookies = False
        # 登录态校验在后台任务中执行
        if self.is_cookies:
            self._validate_task = asyncio.create_task(self._validate_login_state())

    async def terminate(self):
        """插件卸载时关闭 session 并取消后台任务。"""
        await self._cancel_login_check()
        await self._stop_notices_loop()
        if self.session and not self.session.closed:
            await self.session.close()

    @filter.command("获取二维码")
    async def login_by_qr(self, event: AstrMessageEvent):
        """二维码登录流程：下载二维码 -> 轮询登录 -> 保存 cookies。"""
        refer = self.refer
        # 复位登录标志位
        self.logged_in = False
        # 如果未发现本地cookies
        if not self.is_cookies:
            login_info = await self._get_uuid_enc(self.session, refer)
            if not login_info:
                logger.error("无法获取必要参数，可能是被风控了")
                yield event.plain_result("请等待30min后再进行尝试")
                return
            uuid, enc, img_url = login_info
            # 保存二维码
            qr_path = await self._get_qrcode(self.session, img_url)
            info_msg = f"二维码已保存到{qr_path}"
            yield event.image_result(qr_path)
            await asyncio.sleep(1)
            yield event.plain_result("请在1分钟之内扫描二维码登录")
            max_turn = 20
            turn = 0
            while True:
                login_status = await self._get_status(self.session, refer, uuid, enc)
                if login_status is None:
                    logger.error("get_status无响应")
                    break
                if not isinstance(login_status, dict) or 'status' not in login_status:
                    logger.error(f"登录状态响应格式异常:{login_status}")
                    break
                if login_status['status']:
                    logger.info("登陆成功")
                    yield event.plain_result("登陆成功！")
                    # 保存cookies
                    self._save_cookies(self.session)
                    # 保存用户唯一标签
                    self._umo = event.unified_msg_origin
                    safe_write(Path(UMO_PATH), self._umo)
                    self.logged_in = True
                    # 自动启动通知监听
                    self._start_notices_loop()
                    # 登录成功后结束本指令
                    return
                if turn >= max_turn:
                    logger.warning("登录超时，请重新获取二维码")
                    yield event.plain_result("登录超时，请重新获取二维码")
                    break
                if login_status['type'] == 4:
                    logger.info("扫描成功")
                logger.info(login_status)
                turn += 1
                await asyncio.sleep(3)
            if not self.logged_in:
                logger.error("登录失败，退出")
                yield event.plain_result("登陆失败")
                return
        # 如果本地存在cookies
        yield event.plain_result("若需要重新登陆，请执行[/重置登录状态]后再进行登录")

    # 获取通知
    async def _get_notices_loop(self):
        # 未绑定会话时结束轮询
        if not self._umo:
            logger.error("未找到推送目标会话(umo)，通知轮询未启动")
            return
        while True:
            second = random.uniform(25, 35)
            await asyncio.sleep(second)
            try:
                notice_res = await self._get_notice_list(self.session)
                if notice_res:
                    # 先记录入库前已有的 uuid，用于区分本轮真正新增的通知
                    existing = NoticesDB([], NOTICE_LIST_PATH)
                    try:
                        before = set()
                        for record in existing.look_up_all():
                            before.add(str(record.get("uuid")))
                    finally:
                        existing.close_db()
                    result = await self._parse_notice_list(notice_res)
                    if result:
                        new_items = []
                        for item in notice_res["notices"]["list"]:
                            if not isinstance(item, dict):
                                continue
                            if str(item.get("uuid")) not in before:
                                new_items.append(item)
                        if new_items:
                            text = self._build_push_text(new_items)
                        else:
                            text = f"学习通新通知 {result} 条"
                        chain = MessageChain().message(text)
                        await self.context.send_message(self._umo, chain)
            except asyncio.CancelledError:
                # 结束循环并让任务真正进入 done 状态，便于下次重新创建
                return
            except Exception as e:
                logger.error(f"通知轮询出错{e}")

    # 删除登录状态
    @filter.command("重置登录状态")
    async def del_cookies(self, event: AstrMessageEvent):
        """删除本地 cookies，重置登录状态并停止后台轮询。"""
        # 启动时的登录态校验若仍在进行，先取消，否则它稍后可能又把登录态置回有效
        await self._cancel_login_check()
        await self._stop_notices_loop()
        self.is_cookies = False
        self.logged_in = False
        self.session.cookie_jar.clear()
        Path(COOKIES_PATH).unlink(missing_ok=True)
        logger.warning("已删除cookies并停止通知轮询")
        yield event.plain_result("登录状态已经重置，通知推送已停止")

    # 启动消息监听
    @filter.command("开启通知推送")
    async def start_get_notice(self, event: AstrMessageEvent):
        """手动开启通知后台轮询。"""
        if not self.is_cookies:
            yield event.plain_result("请先登录")
            return
        if self._start_notices_loop():
            yield event.plain_result("已开启,可以使用[/关闭通知推送]来关闭")
        else:
            yield event.plain_result("通知推送已经在运行中")

    # 手动关闭消息通知
    @filter.command("关闭通知推送")
    async def close_get_notices(self, event: AstrMessageEvent):
        """手动关闭通知后台轮询。"""
        if await self._stop_notices_loop():
            yield event.plain_result("已关闭通知推送")
        else:
            yield event.plain_result("通知推送当前未在运行")

    @filter.command("调试")
    async def debug(self, event: AstrMessageEvent):
        notice_res = await self._get_notice_list(self.session)
        if not notice_res:
            yield event.plain_result("获取通知列表失败，请检查登录状态或查看日志")
            return
        result = await self._parse_notice_list(notice_res)
        logger.info(result)
        yield event.plain_result(f"调试完成，新增{result}条通知")
