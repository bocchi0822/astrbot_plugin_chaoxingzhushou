"""该插件所需常量"""
from pathlib import Path


# 登录机构(默认为12)
FID = '12'
SPACE = '2'
# 通用头部
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "X-Requested-With": "XMLHttpRequest"
}
# 基础地址
BASE_URL = 'https://passport2.chaoxing.com'
# 登陆时获取登录状态以及登录成功后获取cookies的api
GET_STATUS = "getauthstatus/v2"
# 获取通知列表的api
NOTICE_URL = "https://notice.chaoxing.com/pc/notice/getNoticeList"
# 超时时间
TIMEOUT = 10
# 路径
PLUGIN_DIR = Path(__file__).parent
DATA_DIR = PLUGIN_DIR / 'data'
QRCODE_PATH = str(DATA_DIR / 'qrcode.png')
NOTICE_LIST_PATH = str(DATA_DIR / 'notices_dict.json')
COOKIES_PATH = str(DATA_DIR / 'cookies.pkl')
UMO_PATH = str(DATA_DIR / 'umo.txt')
