"""超星（学习通）插件所需的全部常量与路径配置"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# 登录配置
FID = '12'          # 登录机构编号（默认 12）
SPACE = '2'         # 登录场景标识

# 通知列表请求使用的年份，随当前年份自动变化，避免跨年后失效
YEAR = datetime.now(ZoneInfo("Asia/Shanghai")).year

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

# 课程活动接口，返回签到、抢答、讨论、选人等全部课程活动
ACTIVE_LIST_URL = "https://mobilelearn.chaoxing.com/v2/apis/active/student/activelist"

# 活动接口的 GET 参数
#   fid       机构号，固定为 '0'
#   classId   班级 ID，取自通知的 receiverArray[].clazzId
#   courseId  课程 ID，取自通知的 tag 字段去掉 courseId 前缀
#   _         毫秒时间戳，用于避免缓存
ACTIVE_FID = '0'

# activeType 与活动类型的对应关系
ACTIVE_TYPES = {
    2: "签到",
    4: "抢答",
    5: "主题讨论",
    11: "选人",
    14: "问卷",
    23: "评分",
    35: "分组任务",
    42: "随堂测试",
    43: "投票",
    45: "通知",
    46: "群聊",
    80: "抽签",
}

# 时效较短、需要优先提醒的活动类型
ACTIVE_URGENT_TYPES = {2, 4, 80}

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
