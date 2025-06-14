import datetime
from typing import List, Optional

AL = 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2'
UA = 'Mozilla/5.0 (Windows NT 6.3; Trident/7.0; Touch; rv:11.0) like Gecko'

request_header = {
    'User-Agent': UA,
    'Accept': '*/*',
    'Accept-Language': AL,
}


trade_detail_dict = {
    'f12': '代码',
    'f14': '名称',
    'f3': '涨幅',
    'f2': '最新',
    # 'f4': '涨跌幅',
    # 'f15': '最高',
    # 'f16': '最低',
    # 'f17': '今开',
    # 'f8': '换手率',
    # 'f10': '量比',
    # 'f9': '市盈率',
    # 'f5': '成交量',
    'f6': '成交额',
    # 'f18': '昨收',
    'f20': '总市值',
    # 'f21': '流通市值',
    # 'f13': '编号',
    'f100': '所属板块',
    # 'f124': '更新时间戳',
}

SP_STOCK = {
    # 'A500': '1.000510',
}

STOCK_SECTOR = 'single-stock'
SINGLE_LINE_FIELDS1 = [
    "f1",
    "f2",
    "f3",
    "f4",
    "f5",
    "f6",
    "f7",
    "f8",
    "f9",
    "f10",
    "f11",
    "f12",
    "f13",
]

SINGLE_LINE_FIELDS2 = ["f51", "f52", "f53", "f54", "f55", "f56", "f57", "f58"]

SINGLE_STOCK_FIELDS = [
    # 基本信息
    'f58',
    'f734',
    'f107',
    'f57',
    'f43',
    'f59',
    # 价格相关
    'f169',
    'f170',
    'f152',
    'f177',
    'f111',
    'f46',
    'f60',
    # 涨跌幅
    'f44',
    'f45',
    'f47',
    'f260',
    'f48',
    'f261',
    # 成交量和成交额
    'f279',
    'f277',
    'f278',
    'f288',
    # 开盘价、最高价、最低价、收盘价
    'f19',
    'f17',
    'f531',
    'f15',
    'f13',
    'f11',
    'f20',
    'f18',
    'f16',
    'f14',
    'f12',
    # 买卖盘口
    'f39',
    'f37',
    'f35',
    'f33',
    'f31',
    'f40',
    'f38',
    'f36',
    'f34',
    'f32',
    # 技术指标
    'f211',
    'f212',
    'f213',
    'f214',
    'f215',
    'f210',
    'f209',
    'f208',
    'f207',
    'f206',
    'f168',
    # 其他指标
]
'''
    'f161',
    'f49',
    'f171',
    'f50',
    'f86',
    'f84',
    'f85',
    'f108',
    'f116',
    'f167',
    'f164',
    'f162',
    'f163',
    'f92',
    'f71',
    'f117',
    'f292',
    'f51',
    'f52',
    'f191',
    'f192',
    'f262',
    'f294',
    'f295',
    'f269',
    'f270',
    'f256',
    'f257',
    'f285',
    'f286',
'''

market_dict = {
    '主要指数': 'b:MK0010',
    '外汇': 'm:119,m:120,m:133',
    'stock': 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23',
    '沪深A': 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23',
    '沪深300': 'b:BK0500+f:!50',
    '300': 'b:BK0500+f:!50',
    '上证50': 'b:BK0611+f:!50',
    '上证A': 'm:1 t:2,m:1 t:23',
    '沪A': 'm:1 t:2,m:1 t:23',
    '深证A': 'm:0 t:6,m:0 t:80',
    '深A': 'm:0 t:6,m:0 t:80',
    '北证A': 'm:0 t:81 s:2048',
    '北A': 'm:0 t:81 s:2048',
    '创业板': 'm:0 t:80',
    '科创板': 'm:1 t:23',
    '沪深京A': 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048',
    '沪股通': 'b:BK0707',
    '深股通': 'b:BK0804',
    '风险警示板': 'm:0 f:4,m:1 f:4',
    '两网及退市': 'm:0 s:3',
    '新股': 'm:0 f:8,m:1 f:8',
    '美股': 'm:105,m:106,m:107',
    '港股': 'm:128 t:3,m:128 t:4,m:128 t:1,m:128 t:2',
    '英股': 'm:155 t:1,m:155 t:2,m:155 t:3,m:156 t:1,m:156'
    't:2,m:156 t:5,m:156 t:6,m:156 t:7,m:156 t:8',
    '中概股': 'b:MK0201',
    '中国概念股': 'b:MK0201',
    '地域板块': 'm:90 t:1 f:!50',
    '地域': 'm:90 t:1 f:!50',
    '行业板块': 'm:90 t:2 f:!50',
    '行业': 'm:90 t:2 f:!50',
    '概念板块': 'm:90 t:3 f:!50',
    '概念': 'm:90 t:3 f:!50',
    '上证指数': 'm:1 s:2',
    '上证系列指数': 'm:1 s:2',
    '深证指数': 'm:0 t:5',
    '深证系列指数': 'm:0 t:5',
    '沪深指数': 'm:1 s:2,m:0 t:5',
    '沪深系列指数': 'm:1 s:2,m:0 t:5',
    'bond': 'b:MK0354',
    '债券': 'b:MK0354',
    '可转债': 'b:MK0354',
    'future': 'm:113,m:114,m:115,m:8,m:142',
    '期货': 'm:113,m:114,m:115,m:8,m:142',
    'ETF': 'b:MK0021,b:MK0022,b:MK0023,b:MK0024',
    'LOF': 'b:MK0404,b:MK0405,b:MK0406,b:MK0407',
}

bk_dict = {
    '中概股': 'b:MK0201',
    '中国概念股': 'b:MK0201',
    '地域板块': 'm:90 t:1 f:!50',
    '地域': 'm:90 t:1 f:!50',
    '行业板块': 'm:90 t:2 f:!50',
    '行业': 'm:90 t:2 f:!50',
    '概念板块': 'm:90 t:3 f:!50',
    '概念': 'm:90 t:3 f:!50',
    '上证指数': 'm:1 s:2',
    '上证系列指数': 'm:1 s:2',
    '深证指数': 'm:0 t:5',
    '深证系列指数': 'm:0 t:5',
    '沪深指数': 'm:1 s:2,m:0 t:5',
    '沪深系列指数': 'm:1 s:2,m:0 t:5',
    'bond': 'b:MK0354',
    '债券': 'b:MK0354',
    '可转债': 'b:MK0354',
    'future': 'm:113,m:114,m:115,m:8,m:142',
    '期货': 'm:113,m:114,m:115,m:8,m:142',
    'ETF': 'b:MK0021,b:MK0022,b:MK0023,b:MK0024',
    'LOF': 'b:MK0404,b:MK0405,b:MK0406,b:MK0407',
}

code_id_dict = {
    '上证综指': '1.000001',
    'sh': '1.000001',
    '上证指数': '1.000001',
    '1.000001': '1.000001',
    '深证综指': '0.399106',
    'sz': '0.399106',
    '深证指数': '0.399106',
    '深证成指': '0.399106',
    '创业板指': '0.399006',
    'cyb': '0.399006',
    '创业板': '0.399006',
    '创业板指数': '0.399006',
    '沪深300': '1.000300',
    'hs300': '1.000300',
    '上证50': '1.000016',
    'sz50': '1.000016',
    '上证180': '1.000010',
    'sz180': '1.000010',
    '科创50': '1.000688',
    'kc50': '1.000688',
    '中小100': '0.399005',
    'zxb': '0.399005',
    '中小板': '0.399005',
    '中小板指数': '0.399005',
    '深圳100': '0.399005',
    '标普500': '100.SPX',
    'SPX': '100.SPX',
    'spx': '100.SPX',
    '标普指数': '100.SPX',
    '纳斯达克': '100.NDX',
    '纳斯达克指数': '100.NDX',
    'NSDQ': '100.NDX',
    'nsdq': '100.NDX',
    '道琼斯': '100.DJIA',
    'DJIA': '100.DJIA',
    'dqs': '100.DJIA',
    '道琼斯指数': '100.DJIA',
    '韩国KOSPI': '100.KS11',
    '韩国综合': '100.KS11',
    '韩国综合指数': '100.KS11',
    '韩国指数': '100.KS11',
    '加拿大S&P/TSX': '100.TSX',
    '加拿大指数': '100.TSX',
    '巴西BOVESPA': '100.BVSP',
    '巴西指数': '100.BVSP',
    '墨西哥BOLSA': '100.MXX',
    '墨西哥指数': '100.MXX',
    '俄罗斯RTS': '100.RTS',
    '俄罗斯指数': '100.RTS',
}

# 国际市场
i_code = {
    '上证指数': 'i:1.000001',
    '恒生指数': 'i:100.HSI',
    '日经225': 'i:100.N225',
    '德国DAX30': 'i:100.GDAXI',
    '纳斯达克': 'i:100.NDX',
    '道琼斯': 'i:100.DJIA',
    '标普500': 'i:100.SPX',
    '欧洲斯托克50': 'i:100.SX5E',
    '韩国KOSPI': 'i:100.KS11',
    '英国富时100': 'i:100.FTSE',
    '法国CAC40': 'i:100.FCHI',
    '韩国KOSPI200': 'i:100.KOSPI200',
}


market_dict['国际市场'] = ','.join(i_code.values())

# 大宗商品
commodity = {
    'COMEX黄金': '101.GC00Y',
    'COMEX白银': '101.SI00Y',
    'COMEX铜': '101.HG00Y',
    'NYMEX原油': '102.CL00Y',
    # '综合铝03': '109.LALT',
    '纸浆主连': '113.spm',
    # '综合铜03': '109.LCPT',
    '豆粕主连': '114.mm',
    '综合铅03': '109.LLDT',
    # '综合锌03': '109.LZNT',
    '生猪主连': '114.lhm',
}

# 债券市场
bond = {
    # '三十债主连': '220.TLM',
    # '十债主连': '220.TM',
    # '五债主连': '220.TFM',
    # '二债主连': '220.TSM',
    '中国30年期国债': '171.CN30Y',
    '中国10年期国债': '171.CN10Y',
    # '中国5年期国债': '171.CN5Y',
    '中国2年期国债': '171.CN2Y',
    '美国30年期国债收益率': '171.US30Y',
    '美国10年期国债收益率': '171.US10Y',
    # '美国5年期国债收益率': '171.US5Y',
    '美国2年期国债收益率': '171.US2Y',
    'JP 30Y': '',
    'JP 10Y': '',
}

# b:MK0300 外汇
whsc = {
    '美元兑离岸人民币': '133.USDCNH',
    '美元兑港币': '119.USDHKD',
    '美元兑日元': '119.USDJPY',
    '美元指数': '100.UDI',
    # '欧元兑美元': '119.EURUSD',
    # '英镑兑美元': '119.GBPUSD',
    # '澳元兑美元': '119.AUDUSD',
    # '美元兑泰铢': '119.USDTHB',
}


def create_time_array(
    start_time: str = '09:15', code: Optional[str] = None
) -> List[str]:
    """"""
    if start_time == '09:15':
        AMStart = datetime.datetime.strptime('9:15', '%H:%M')
        AMEnd = datetime.datetime.strptime('11:30', '%H:%M')
        PMStart = datetime.datetime.strptime('13:01', '%H:%M')
        PMEnd = datetime.datetime.strptime('16:00', '%H:%M')
    elif start_time == '21:30':
        AMStart = datetime.datetime.strptime('21:30', '%H:%M')
        AMEnd = datetime.datetime.strptime('23:30', '%H:%M')
        PMStart = datetime.datetime.strptime('23:31', '%H:%M')
        PMEnd = datetime.datetime.strptime('04:00', '%H:%M')
    elif start_time == '22:30':
        AMStart = datetime.datetime.strptime('22:30', '%H:%M')
        AMEnd = datetime.datetime.strptime('23:30', '%H:%M')
        PMStart = datetime.datetime.strptime('23:31', '%H:%M')
        PMEnd = datetime.datetime.strptime('05:00', '%H:%M')
    else:
        AMStart = datetime.datetime.strptime('9:15', '%H:%M')
        AMEnd = datetime.datetime.strptime('11:30', '%H:%M')
        PMStart = datetime.datetime.strptime('13:01', '%H:%M')
        PMEnd = datetime.datetime.strptime('16:00', '%H:%M')

    if code:
        _c = code.split('.')[0]
        if _c in ['101', '119', '133', '100', '102', '114']:
            AMStart = datetime.datetime.strptime('00:00', '%H:%M')
            AMEnd = datetime.datetime.strptime('23:59', '%H:%M')
            PMStart = AMEnd  # 不需要下午时段
            PMEnd = AMEnd
        elif _c in ['105', '106']:
            # 美股，包含盘前（16:00-21:30）、正股（21:30-04:00）、盘后（04:00-08:00），均为北京时间，跨天处理
            AMStart = datetime.datetime.strptime('21:30', '%H:%M')
            AMEnd = datetime.datetime.strptime('23:59', '%H:%M')
            PMStart = datetime.datetime.strptime('00:00', '%H:%M')
            PMEnd = datetime.datetime.strptime('04:00', '%H:%M')

    delta = datetime.timedelta(minutes=1)
    time_array = []

    while AMStart <= AMEnd:
        time_array.append(AMStart.strftime('%H:%M'))
        AMStart += delta
    while PMStart <= PMEnd:
        time_array.append(PMStart.strftime('%H:%M'))
        PMStart += delta
    return time_array
