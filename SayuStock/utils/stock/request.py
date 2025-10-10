import json
import random
import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple, Union, Literal, Optional

import aiofiles
from gsuid_core.logger import logger
from aiohttp import (
    FormData,
    TCPConnector,
    ClientSession,
    ClientTimeout,
    ContentTypeError,
)

from .get_vix import get_vix_data
from .utils import async_file_cache
from .request_utils import get_code_id
from ..load_data import get_full_security_code
from ..constant import (
    SINGLE_LINE_FIELDS1,
    SINGLE_LINE_FIELDS2,
    SINGLE_STOCK_FIELDS,
    ErroText,
    market_dict,
    request_header,
    trade_detail_dict,
)

MENU_CACHE = {}


async def get_bar():
    URL = 'https://quotederivates.eastmoney.com/datacenter/updowndistribution?mcodelist=0.399002%2C1.000002%2C0.899050&version=100&cver=10.33.6'
    resp = await stock_request(URL)
    if isinstance(resp, int):
        return f'[SayuStock] 请求错误：{resp}'
    return resp


async def get_menu(mode: int = 3) -> Dict:
    '''
    mode = 2 为行业板块
    mode = 3 为概念板块
    '''

    now = datetime.now().strftime('%Y%m%d')
    if now in MENU_CACHE:
        return MENU_CACHE[now][mode]

    URL = 'https://quote.eastmoney.com/center/api/sidemenu_new.json'
    data = await stock_request(URL)
    if isinstance(data, int):
        raise Exception(f'[SayuStock] 请求错误：{data}')

    hyr = {}
    gnr = {}
    for i in data['bklist']:
        if i['type'] == 2:
            hyr[i['name']] = i['code']
        elif i['type'] == 3:
            gnr[i['name']] = i['code']

    MENU_CACHE[now] = {2: hyr, 3: gnr}

    if len(MENU_CACHE) > 1:
        # 删除旧项，保留最新的
        keys_to_remove = list(MENU_CACHE.keys())[:-1]
        for key in keys_to_remove:
            del MENU_CACHE[key]

    return data


@async_file_cache(market='vix_market', sector='{vix_name}', suffix='json')
async def get_vix(vix_name: str):
    trends = await get_vix_data(vix_name)
    if isinstance(trends, str):
        return trends

    price_change_percent = 0.0
    # 确保趋势数据非空且开盘价不为0，以避免除零错误
    if len(trends) > 0:
        latest_price = trends[-1]['price']
        open_price = (
            trends[0]['open'] if trends[0]['open'] != 0 else trends[0]['price']
        )

        price_change_percent: float = ((latest_price - open_price) / open_price) * 100  # type: ignore

    resp = {
        'data': {
            'f43': trends[-1]['price'],
            'f44': trends[-1]['price'],
            'f58': vix_name,
            'f60': open_price,
            'f48': 0,
            'f168': 0,
            'f170': round(float(price_change_percent), 2),
        },
        'trends': trends,
    }

    return resp


async def get_single_fig_data(secid: str):
    params = []
    url = "https://push2.eastmoney.com/api/qt/stock/trends2/get"
    fields1 = ",".join(SINGLE_LINE_FIELDS1)
    fields2 = ",".join(SINGLE_LINE_FIELDS2)
    params.append(('fields1', fields1))
    params.append(('fields2', fields2))
    params.append(('secid', secid))
    resp = await stock_request(url, params=params)

    if isinstance(resp, int):
        return f'[SayuStock] 请求错误, 错误码: {resp}！'
    if resp['data'] is None:
        return ErroText['notStock']

    stock_line_data: list[str] = resp['data']['trends']
    stock_data: list[Dict[str, Union[str, float, int]]] = []
    for item in stock_line_data:
        # 原始数据格式
        # "2024-12-31 14:05,15.63,15.62,15.63,15.61,3300,5154770.00,15.672"
        parts = item.split(',')
        # 原始时间格式为'2024-12-31 14:05'
        datetime = parts[0].split(' ') if len(parts[0]) > 0 else ['', '']
        stock_data.append(
            {
                'datetime': datetime[1],
                'price': float(parts[1]),
                'open': float(parts[2]),
                'high': float(parts[3]),
                'low': float(parts[4]),
                'amount': int(parts[5]),
                'money': float(parts[6]),
                'avg_price': float(parts[7]),
            }
        )
    return stock_data


async def get_gg(
    market: str,
    sector: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
):
    logger.info(f'[SayuStock] get_single_fig_data code: {market}')

    sec_id_data = await get_code_id(market)
    if sec_id_data is None:
        return ErroText['notStock']

    sec_id = get_full_security_code(sec_id_data[0])
    if sec_id is None:
        return ErroText['notStock']

    if sector == 'single-stock':
        result = await _get_gg(sec_id, sec_id_data[2])
    elif sector.startswith('single-stock-kline'):
        kline_code = sector.split('-')[-1]
        if kline_code == '100':
            kline_code = 101
            out_day = 50
        elif kline_code == '101':
            out_day = 245
        elif kline_code == '102':
            out_day = 800
        elif kline_code == '103':
            out_day = 2000
        elif kline_code == '104':
            out_day = 4000
        elif kline_code == '105':
            out_day = 6000
        elif kline_code == '106':
            out_day = 10000
        elif kline_code == '111':
            kline_code = 101
            out_day = 365
        else:
            out_day = 1600

        if start_time is None:
            start_time = datetime.now() - timedelta(days=out_day)
        if end_time is None:
            end_time = datetime.now()
        st_f = start_time.strftime('%Y%m%d') if start_time else ''
        et_f = end_time.strftime('%Y%m%d') if end_time else ''

        result = await _get_gg_kline(
            sec_id,
            sec_id_data[2],
            kline_code,
            st_f,
            et_f,
        )
    else:
        result = {}

    return result


# 个股
@async_file_cache(market='{sec_id}', sector='single-stock', suffix='json')
async def _get_gg(sec_id: str, sec_type: str):
    params = [
        ('pz', '200'),
        ('po', '1'),
        ('np', '1'),
        ('fltt', '2'),
        ('invt', '2'),
        ('fid', 'f3'),
        ('pn', '1'),
    ]

    fields = ",".join(SINGLE_STOCK_FIELDS)
    url = 'https://push2.eastmoney.com/api/qt/stock/get'
    logger.info(f'[SayuStock] get_single_fig_data secid: {sec_id}')
    params.append(('secid', sec_id))
    params.append(('fields', fields))

    resp = await stock_request(url, 'GET', params=params)
    if isinstance(resp, int):
        return f'[SayuStock] 请求错误, 错误码: {resp}！'

    # 处理获取个股数据错误
    if resp['data'] is None:
        return ErroText['notStock']

    secid = next((value for key, value in params if key == 'secid'), None)
    if secid:
        trends = await get_single_fig_data(secid)
        if isinstance(trends, str):
            return resp
        resp['trends'] = trends

    resp['data']['f58'] = f"{resp['data']['f58']} ({sec_type})"

    return resp


# 个股 日K
@async_file_cache(
    market='{sec_id}',
    sector='single-stock-kline-{kline_code}',
    suffix='json',
    sp='{start_time}-{end_time}',
)
async def _get_gg_kline(
    sec_id: str,
    sec_type: str,
    kline_code: Union[str, int],
    start_time: str,
    end_time: str,
):
    url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
    logger.info(f'[SayuStock] get_single_fig_data secid: {sec_id}')
    params = [
        ('fields1', 'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13'),
        ('fields2', 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'),
        ('rtntype', '6'),
        ('klt', kline_code),
        ('fqt', '1'),
        ('secid', sec_id),
        ('beg', start_time),
        ('end', end_time),
    ]

    resp = await stock_request(url, 'GET', params=params)
    if isinstance(resp, int):
        return f'[SayuStock] 请求错误, 错误码: {resp}！'

    if resp['data'] is None:
        return ErroText['notStock']

    print(resp['data'])
    resp['data']['name'] = f"{resp['data']['name']} ({sec_type})"

    return resp


# 大盘云图等批量性
@async_file_cache(
    market='{market}',
    sector='{po}',
    suffix='json',
    sp='{is_loop}-{pz}',
)
async def get_mtdata(
    market: str,
    is_loop: bool = False,
    po: int = 1,  # 0为倒序，1为正序
    pz: int = 20,
):
    params = [
        ('pz', str(pz)),
        ('po', str(po)),
        ('np', '1'),
        ('fltt', '2'),
        ('invt', '2'),
        ('fid', 'f3'),
        ('pn', '1'),
    ]

    url = 'http://push2.eastmoney.com/api/qt/clist/get'
    if market in market_dict:
        fs = market_dict[market]
    else:
        fs = market

    fields = ",".join(trade_detail_dict.keys())
    if fs.startswith(('bk', 'BK')):
        fs = f'b:{fs}'
    params.append(('fs', fs))
    params.append(('fields', fields))

    resp = await stock_request(url, 'GET', params=params)
    if isinstance(resp, int):
        return f'[SayuStock] 错误代码: {resp}'

    if is_loop and resp['data'] and len(resp['data']['diff']) >= 100:
        stop_event = asyncio.Event()
        pn = 2
        TASK = []
        params.remove(('pn', '1'))
        params.remove(('pz', '100'))
        params.append(('pz', str(len(resp['data']['diff']))))

        while not stop_event.is_set():
            for _ in range(10):
                _params = params.copy()
                _params.append(('pn', str(pn)))
                TASK.append(_get_data(resp, url, _params, stop_event))
                pn += 1
            await asyncio.gather(*TASK)
            TASK.clear()

        await asyncio.gather(*TASK)

    return resp


async def _get_data(
    resp: Dict,
    url: str,
    params: List[tuple],
    stop_event: asyncio.Event,
):
    if stop_event.is_set():
        return None
    await asyncio.sleep(random.uniform(0.4, 0.9))
    resp2 = await stock_request(url, params=params)
    if isinstance(resp2, int):
        return stop_event.set()

    if 'code' not in resp2 and resp2['data']:
        resp['data']['diff'].extend(resp2['data']['diff'])
        if len(resp2['data']['diff']) < 100:
            stop_event.set()
    else:
        stop_event.set()


@async_file_cache(
    market='大盘云图',
    sector='大盘云图',
    suffix='json',
)
async def get_hotmap():
    URL = 'https://quote.eastmoney.com/stockhotmap/api/getquotedata'
    resp = await stock_request(URL)
    if isinstance(resp, int):
        return f'[SayuStock] 错误代码: {resp}'

    bk: List[str] = []
    for i in resp['bk']:
        assert isinstance(i, str)
        data = i.split('|')
        bk.append(data[0])

    result = {
        "rc": 0,
        "rt": 6,
        "svr": 180606397,
        "lt": 1,
        "full": 1,
        "dlmkts": "",
        "data": {'total': 0, "diff": []},
    }

    for i in resp['data']:
        assert isinstance(i, str)
        if '|' in i:
            data = i.split('|')
            diff = {
                "f2": float(data[15]) / 100 if data[15] != '-' else 0,
                "f3": float(data[6]) / 100 if data[6] != '-' else 0,
                "f6": float(data[13]) if data[13] != '-' else 0,
                "f12": data[3],
                "f14": data[1],
                "f20": float(data[17]) * 100000 if data[17] != '-' else 0,
                "f100": bk[int(data[0])],
                "dd": data[4][1:-1].split(','),
            }
            result['data']['diff'].append(diff)

    result['data']['total'] = len(result['data']['diff'])
    return result


async def stock_request(
    url: str,
    method: Literal['GET', 'POST'] = 'GET',
    header: Dict[str, str] = request_header,
    params: Union[Dict[str, Any], List[Tuple[str, Any]], None] = None,
    _json: Optional[Dict[str, Any]] = None,
    data: Optional[FormData] = None,
) -> Union[Dict, int]:
    logger.info(f'[SayuStock] 请求: {url}')
    logger.info(f'[SayuStock] Params: {params}')

    async with ClientSession(
        connector=TCPConnector(verify_ssl=True)
    ) as client:
        for _ in range(2):
            async with client.request(
                method,
                url=url,
                headers=header,
                params=params,
                json=_json,
                data=data,
                timeout=ClientTimeout(total=300),
            ) as resp:
                try:
                    raw_data = await resp.json()
                except (ContentTypeError, json.decoder.JSONDecodeError):
                    _raw_data = await resp.text()
                    raw_data = -999
                logger.debug(raw_data)

                if resp.status != 200:
                    logger.error(
                        f'[SayuStock][EM] 访问 {url} 失败, 错误码: {resp.status}'
                        f', 错误返回: {raw_data}'
                    )
                    return -999
                return raw_data
        else:
            return -400016
