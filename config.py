"""超星（学习通）插件所需的全部常量与路径配置"""

from pathlib import Path

# 登录配置
FID = '12'          # 登录机构编号（默认 12）
SPACE = '2'         # 登录场景标识

# 通用请求头
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "X-Requested-With": "XMLHttpRequest",
}

# 接口地址
BASE_URL = 'https://passport2.chaoxing.com'          # 登录服务基础地址
GET_STATUS = "getauthstatus/v2"                      # 登录状态/获取 cookies 的 API 路径
NOTICE_URL = "https://notice.chaoxing.com/pc/notice/getNoticeList"  # 通知列表 API

# 请求超时与代理
TIMEOUT = 10         # HTTP 请求超时时间（秒）
PORT = ''            # 代理端口（预留）
PROXIES = [          # 代理 IP 池（备用）
    "223.15.228.134:9797",
    "49.91.12.54:3128",
    "118.239.191.113:8080",
    "220.175.227.219:8080",
]

# 本地存储路径
PLUGIN_DIR = Path(__file__).parent
DATA_DIR = PLUGIN_DIR / 'data'                        # 数据目录
QRCODE_PATH = str(DATA_DIR / 'qrcode.png')            # 登录二维码图片
NOTICE_LIST_PATH = str(DATA_DIR / 'notices_dict.json')  # 上次通知记录（uuid 集合）
COOKIES_PATH = str(DATA_DIR / 'cookies.pkl')          # 持久化 cookies
UMO_PATH = str(DATA_DIR / 'umo.txt')                  # 绑定用户的消息标识
