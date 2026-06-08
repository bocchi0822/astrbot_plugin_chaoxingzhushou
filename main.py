from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult, MessageChain
from astrbot.api.star import Context, Star
from astrbot.api import logger  # 使用 astrbot 提供的 logger 接口
from .config import FID, SPACE, BASE_URL, HEADERS, GET_STATUS, NOTICE_URL, QRCODE_PATH, COOKIES_PATH, NOTICE_LIST_PATH, \
    DATA_DIR, UMO_PATH
import aiohttp
import time
import re
from .utils import get_request, time_limit, safe_write, json_write, json_read, fetch_json, safe_read
import pickle
from pathlib import Path
import asyncio
import random


class ChaoXingPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        self.session = None
        self.is_cookies = False
        self.refer = None
        self.logged_in = False
        self._notice_task = None
        self._have_new_notice = False
        self._umo = None

    # 获取用于请求登录页面的refer
    def _creat_refer(self):
        t = str(int(time.time() * 1000))
        refer = f"http://i.chaoxing.com/base?t={t}"
        return refer

    # 请求登录页面获取uuid和enc
    async def _get_uuid_enc(self, session, refer):
        url = f'{BASE_URL}/login?fid={FID}&refer={refer}&space={SPACE}'
        response = await get_request(session, url, headers=HEADERS, Method="GET")
        print(response)
        if response is None:
            logger.error("获取登录页面失败")
            return None
        login_html = await response.text()
        # 解析uuid和enc
        result = re.findall(r'<input type="hidden" value="([^"]+)" id="(uuid|enc)"/>', login_html, re.S)
        # 获取qrcode的地址
        img_match = re.search(r'<img src="([^"]+)" id="quickCode">', login_html, re.S)
        if not result:
            logger.error(f"uuid/enc未匹配到，HTML片段: {login_html[:2000]}")
            return None
        if not img_match:
            logger.error(f"二维码图片未匹配到，HTML片段: {login_html[:2000]}")
            return None
        if result and img_match:
            uuid = result[0][0]
            enc = result[1][0]
            img_url = f'{BASE_URL}{img_match.group(1)}'
            return uuid, enc, img_url
        else:
            return None

    async def _get_qrcode(self, session, img_url):
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

    # 登陆时轮询登录状态，获取cookies
    async def _get_status(self, session, refer, uuid, enc):
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
        content_type = response.headers.get("Content-Type", "")
        if 'application/json' not in content_type:
            return await fetch_json(response)
        return await response.json()

    def _save_cookies(self, session):
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

    async def _get_notice_list(self, session):
        data = {
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
            "year": 2026,
            "tag": '',
            "fidsCode": '',
            "queryFolderNoticePrevYear": 0,
            "filterSenderPuids": '',
            "filterTags": '',
        }
        res = await get_request(session, NOTICE_URL, data=data, headers=HEADERS, Method="POST")
        if res is None:
            logger.error("通知列表为None")
            return None
        # 判断登录状态
        cookies_path = Path(COOKIES_PATH)
        content_type = res.headers.get('Content-Type')
        if res.status == 302 or "text/html" in content_type:
            logger.warning("cookie失效，请重新登录")
            cookies_path.unlink(missing_ok=True)
            self.is_cookies = False
            return None
        if "application/json" not in content_type:
            return await fetch_json(res)
        return await res.json()

    # 解析通知列表
    def _parse_notice_list(self, notice_res):
        title = ''
        content = ''
        try:
            notice_list = notice_res["notices"]["list"]
            notices_dict = {}
            if not notice_res["notices"]["list"]:
                return None
            for item in notice_list:
                # 去掉换行
                cleaned_title = item["title"].replace("\r", "").replace("\n", "")
                cleaned_content = item["content"].replace("\r", "").replace("\n", "")
                notices_dict[item["uuid"]] = [cleaned_title, cleaned_content]
            # 记录本次通知的唯一id的集合
            current_uuid = set(notices_dict.keys())
            # 读取上次通知的唯一id的集合
            old_uuid = set()
            old_notices_dict = Path(NOTICE_LIST_PATH)
            if old_notices_dict.exists():
                old_uuid = set(json_read(old_notices_dict).keys())
            # 获取新增的通知的uuid
            new_uuid = current_uuid - old_uuid
            if new_uuid:
                for uuid in new_uuid:
                    title = notices_dict[uuid][0]
                    content = notices_dict[uuid][1]
                    logger.info(f"新通知：标题【{title}】, 内容【{content}】")
                    # 保存本次通知
                    json_write(NOTICE_LIST_PATH, notices_dict)
                    # 更新状态
                    self._have_new_notice = True
                return title, content
            else:
                logger.debug("无新通知")
                # 更新状态
                self._have_new_notice = False
                return None
        except Exception as e:
            logger.error(f"解析通知时出错,{e}")

    # 生命周期：调用插件前
    async def initialize(self):
        """初始化插件，创建session"""
        self.refer = self._creat_refer()
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
                    logger.info("本地cookies加载成功")
            except Exception as e:
                logger.error(f"本地cookies加载失败:{e}")
        logger.info("初始化session成功")

        # 启动后台轮询获取消息
        if self.is_cookies:
            self._notice_task = asyncio.create_task(self._get_notices_loop())
            logger.info("已开启通知列表监听")

    # 获取cookies
    @filter.command("获取二维码")
    async def login_by_qr(self, event: AstrMessageEvent):
        refer = self.refer
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
                    if not self._notice_task or self._notice_task.done():
                        self._notice_task = asyncio.create_task(self._get_notices_loop())
                        logger.error("已开启通知列表监听")
                    break
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
        while True:
            second = random.uniform(25, 35)
            await asyncio.sleep(second)
            try:
                notice_res = await self._get_notice_list(self.session)
                if notice_res:
                    result = self._parse_notice_list(notice_res)
                    if result:
                        title, content = result
                        chain = MessageChain().message(f"学习通通知\n标题：{title}\n内容：{content}")
                        await self.context.send_message(self._umo, chain)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"通知轮询出错{e}")

    # 删除登录状态
    @filter.command("重置登录状态")
    async def del_cookies(self, event: AstrMessageEvent):
        if self.is_cookies:
            Path(COOKIES_PATH).unlink(missing_ok=True)
            self.is_cookies = False
            logger.warning("已删除cookies")
            yield event.plain_result("登录状态已经重置")
            await asyncio.sleep(1)
        yield event.plain_result("登录状态已经重置")

    # 启动消息监听
    @filter.command("开启通知推送")
    async def start_get_notice(self, event: AstrMessageEvent):
        if not self.is_cookies:
            yield event.plain_result("请先登录")
            return
        if not self._notice_task or self._notice_task.done():
            self._notice_task = asyncio.create_task(self._get_notices_loop())
            yield event.plain_result("已开启,可以使用[/关闭通知推送]来关闭")

    # 手动关闭消息通知
    @filter.command("关闭通知推送")
    async def close_get_notices(self, event: AstrMessageEvent):
        if not self.is_cookies:
            yield event.plain_result("请先登录")
            return
        if self._notice_task and not self._notice_task.done():
            self._notice_task.cancel()
            yield event.plain_result("已关闭通知推送")

    # 卸载插件时自动释放资源
    async def terminate(self):
        await self.session.close()
        if self._notice_task and not self._notice_task.done():
            self._notice_task.cancel()
