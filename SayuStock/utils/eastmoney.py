import json
import random
import asyncio
from typing import Any, Dict, List, Tuple, Union, Literal, Optional, TypedDict

from yarl import URL
from aiohttp import (
    FormData,
    TCPConnector,
    ClientSession,
    ClientTimeout,
    ContentTypeError,
    ClientConnectorError,
    ServerDisconnectedError,
)

from gsuid_core.logger import logger

from .constant import (
    DC_COOKIES,
    SINGLE_LINE_FIELDS1,
    SINGLE_LINE_FIELDS2,
    SINGLE_STOCK_FIELDS,
    ErroText,
    market_dict,
    header_simple,
    chinese_stocks,
    request_header,
    trade_detail_dict,
)
from .stock.utils import async_file_cache
from ..stock_config.stock_config import STOCK_CONFIG

EastMoneyResponse = Union[Dict[str, Any], int]
EastMoneyParams = Union[Dict[str, Any], List[Tuple[str, Any]], Tuple[Tuple[str, Any], ...], None]
EastMoneyValueType = Literal["pe", "pb"]
EastMoneyKlineCode = Literal[
    "5",
    "15",
    "30",
    "60",
    "100",
    "101",
    "102",
    "103",
    "104",
    "105",
    "106",
    "111",
]

EASTMONEY_VALUE_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
EASTMONEY_VALUE_FIELD_MAP: Dict[EastMoneyValueType, str] = {
    "pe": "PE_TTM",
    "pb": "PB_MRQ",
}
EASTMONEY_VALUE_NAME_MAP: Dict[EastMoneyValueType, str] = {
    "pe": "市盈率(PE_TTM)",
    "pb": "市净率(PB_MRQ)",
}
EASTMONEY_KLINE_DEFAULT_DAYS: Dict[str, int] = {
    "5": 30,
    "15": 40,
    "30": 60,
    "60": 100,
    "100": 50,
    "101": 400,
    "102": 1000,
    "103": 2400,
    "104": 4500,
    "105": 7000,
    "106": 13000,
    "111": 365,
}


class EastMoneyStockItem(TypedDict):
    secid: str
    code: str
    name: str
    sec_type: str


class EastMoneyValueSeriesData(TypedDict):
    code: str
    secid: str
    name: str
    sec_type: str
    value_type: EastMoneyValueType
    value_name: str
    rows: List[Dict[str, Union[str, float]]]


class EastMoneyRequester:
    """东方财富 API 请求封装。

    该类集中管理东方财富相关 HTTP 请求、请求日志、Cookie 注入、备用域名
    切换和文件缓存。每个公开接口方法对应一个东方财富 API 语义请求，最终
    都通过 `stock_request` 请求工厂发起网络访问。
    """

    def __init__(self) -> None:
        self.now_queue = 0
        self.menu_cache: Dict[str, Dict[int, Dict[str, str]]] = {}

    async def stock_request(
        self,
        url: str,
        method: Literal["GET", "POST"] = "GET",
        header: Dict[str, str] = request_header,
        params: EastMoneyParams = None,
        _json: Optional[Dict[str, Any]] = None,
        data: Optional[FormData] = None,
    ) -> EastMoneyResponse:
        """东方财富请求工厂。

        Args:
            url: 请求地址。
            method: HTTP 方法。
            header: 请求头。
            params: 查询参数。
            _json: JSON 请求体。
            data: 表单请求体。

        Returns:
            成功时返回东方财富接口 JSON 字典；失败时返回负数错误码。
        """
        logger.debug(f"[SayuStock][EM] 请求: {url}")
        logger.debug(f"[SayuStock][EM] Params: {params}")

        request_headers = dict(header)
        cookies = STOCK_CONFIG.get_config("eastmoney_cookie").data
        if cookies:
            logger.debug("[SayuStock][EM] 使用配置中的 Cookie")
            request_headers["Cookie"] = cookies

        if url.startswith(
            (
                "https://quote.eastmoney.com/center/api/sidemenu_new.json",
                "https://quote.eastmoney.com/stockhotmap/api/getquotedata",
                "https://quotederivates.eastmoney.com",
            )
        ):
            request_headers = dict(header_simple)

        urls = [url]
        if "push2.eastmoney.com" in url:
            urls.append(url.replace("push2.eastmoney.com", "push2delay.eastmoney.com", 1))

        async with ClientSession(
            connector=TCPConnector(verify_ssl=True),
            headers=request_headers,
            cookies=DC_COOKIES,
        ) as client:
            for req_url in urls:
                final_url = str(URL(req_url).with_query(params or {}))
                logger.debug(f"[SayuStock][EM] 最终请求URL：{final_url}")

                while self.now_queue >= 6:
                    await asyncio.sleep(random.uniform(0.4, 0.9))

                for _ in range(2):
                    try:
                        self.now_queue += 1
                        async with client.request(
                            method,
                            url=req_url,
                            headers=request_headers,
                            params=params,
                            json=_json,
                            data=data,
                            timeout=ClientTimeout(total=300),
                        ) as resp:
                            try:
                                raw_data = await resp.json(content_type=None)
                            except (ContentTypeError, json.decoder.JSONDecodeError):
                                raw_text = await resp.text()
                                logger.debug(f"[SayuStock][EM] 非JSON响应: {raw_text[:500]}")
                                raw_data = -999
                            logger.debug(raw_data)

                            if resp.status != 200:
                                logger.error(
                                    f"[SayuStock][EM] 访问 {req_url} 失败, 错误码: {resp.status}, 错误返回: {raw_data}"
                                )
                                if req_url != urls[-1]:
                                    break
                                return -999
                            if isinstance(raw_data, int):
                                if req_url != urls[-1]:
                                    break
                                return raw_data
                            return raw_data
                    except ServerDisconnectedError:
                        logger.warning(f"[SayuStock][EM] 请求 {req_url} 连接断开，稍后重试。")
                        await asyncio.sleep(random.uniform(0.2, 0.9))
                    except ClientConnectorError as error:
                        logger.error(f"[SayuStock][EM] 请求 {req_url} 连接失败: {error}")
                    finally:
                        self.now_queue -= 1
                else:
                    if req_url == urls[-1]:
                        return -400016
                    logger.warning(f"[SayuStock][EM] 请求 {req_url} 失败, 尝试切换到备用域名...")
        return -400016

    async def resolve_stock(self, query: str) -> Optional[EastMoneyStockItem]:
        """解析股票名称或代码为东方财富证券标识。

        Args:
            query: 股票名称、简称、代码或带市场后缀的输入，例如 `茅台`、`600519`、`0700.hk`。

        Returns:
            解析成功时返回标准股票条目，包含 `secid`、纯代码、名称和证券类型；失败时返回 `None`。
        """
        from .load_data import get_full_security_code
        from .stock.request_utils import get_code_id

        code_info = await get_code_id(query)
        if code_info is None:
            return None
        secid, name, sec_type = code_info
        full_secid = get_full_security_code(secid)
        return {
            "secid": full_secid,
            "code": full_secid.split(".")[-1],
            "name": name or secid,
            "sec_type": sec_type,
        }

    async def parse_stock_input(self, raw_input: str) -> List[EastMoneyStockItem]:
        """解析多个股票输入为标准股票条目列表。

        Args:
            raw_input: 以空格、中文逗号或英文逗号分隔的股票名称/代码。

        Returns:
            去重后的标准股票条目列表，供 PE/PB、对比图等数据层复用。
        """
        normalized_input = raw_input.replace("，", " ").replace(",", " ")
        stock_list: List[EastMoneyStockItem] = []
        seen: set[str] = set()
        for query in normalized_input.split():
            stock = await self.resolve_stock(query)
            if stock is None:
                logger.warning(f"[SayuStock][EM] 未找到股票: {query}")
                continue
            if stock["code"] in seen:
                continue
            seen.add(stock["code"])
            stock_list.append(stock)
        return stock_list

    async def get_menu(self, mode: int = 3) -> Dict[str, str]:
        """获取东方财富侧边栏板块菜单。

        Args:
            mode: `2` 表示行业板块，`3` 表示概念板块。

        Returns:
            板块名称到板块代码的映射，例如 `{ "人工智能": "BKxxxx" }`。
        """
        from datetime import datetime

        today_key = datetime.now().strftime("%Y%m%d")
        if today_key in self.menu_cache:
            return self.menu_cache[today_key][mode]

        url = "https://quote.eastmoney.com/center/api/sidemenu_new.json"
        data_resp = await self.stock_request(url)
        if isinstance(data_resp, int):
            raise RuntimeError(f"[SayuStock] 请求错误：{data_resp}")

        industry_result: Dict[str, str] = {}
        concept_result: Dict[str, str] = {}
        bk_list = data_resp["bklist"]
        for item in bk_list:
            item_type = item["type"]
            if item_type == 2:
                industry_result[item["name"]] = item["code"]
            elif item_type == 3:
                concept_result[item["name"]] = item["code"]

        self.menu_cache[today_key] = {2: industry_result, 3: concept_result}
        if len(self.menu_cache) > 1:
            keys_to_remove = list(self.menu_cache.keys())[:-1]
            for key in keys_to_remove:
                del self.menu_cache[key]

        return self.menu_cache[today_key][mode]

    @async_file_cache(market="{sec_id}", sector="single-stock-trends", suffix="json", minutes=2)
    async def get_stock_trends(self, sec_id: str) -> Union[List[Dict[str, Union[str, float, int]]], str]:
        """获取个股当日分时走势。

        Args:
            sec_id: 东方财富完整证券 ID，例如 `1.600519`。

        Returns:
            分时点列表。每个点包含时间、价格、开盘、最高、最低、成交量、
            成交额和均价；请求失败时返回错误文本。
        """
        params: List[Tuple[str, Any]] = []
        url = "https://push2.eastmoney.com/api/qt/stock/trends2/get"
        params.append(("fields1", ",".join(SINGLE_LINE_FIELDS1)))
        params.append(("fields2", ",".join(SINGLE_LINE_FIELDS2)))
        params.append(("secid", sec_id))
        resp = await self.stock_request(url, params=params)

        if isinstance(resp, int):
            return f"[SayuStock] 请求错误, 错误码: {resp}！"
        if resp["data"] is None:
            return ErroText["notStock"]

        stock_line_data: List[str] = resp["data"]["trends"]
        stock_data: List[Dict[str, Union[str, float, int]]] = []
        for item in stock_line_data:
            parts = item.split(",")
            date_time = parts[0].split(" ") if len(parts[0]) > 0 else ["", ""]
            stock_data.append(
                {
                    "datetime": date_time[1],
                    "price": float(parts[1]),
                    "open": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "amount": int(parts[5]),
                    "money": float(parts[6]),
                    "avg_price": float(parts[7]),
                }
            )
        return stock_data

    @async_file_cache(market="{sec_id}", sector="single-stock", suffix="json", minutes=2)
    async def get_single_stock(self, sec_id: str, sec_type: str) -> Union[Dict[str, Any], str]:
        """获取个股实时盘口并合并当日分时。

        Args:
            sec_id: 东方财富完整证券 ID，例如 `0.300750`。
            sec_type: 证券类型名称，例如 `沪深A`、`ETF`、`港股`。

        Returns:
            东方财富个股实时行情 JSON，并附加 `trends` 分时数组；找不到标的
            或请求失败时返回错误文本。
        """
        params: List[Tuple[str, Any]] = [
            ("pz", "200"),
            ("po", "1"),
            ("np", "1"),
            ("fltt", "2"),
            ("invt", "2"),
            ("fid", "f3"),
            ("pn", "1"),
            ("secid", sec_id),
            ("fields", ",".join(SINGLE_STOCK_FIELDS)),
        ]
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        resp = await self.stock_request(url, "GET", params=params)
        if isinstance(resp, int):
            return f"[SayuStock] 请求错误, 错误码: {resp}！"
        if resp["data"] is None:
            return ErroText["notStock"]

        trends = await self.get_stock_trends(sec_id)
        if not isinstance(trends, str):
            resp["trends"] = trends
        resp["data"]["f58"] = f"{resp['data']['f58']} ({sec_type})"
        return resp

    @async_file_cache(
        market="{sec_id}",
        sector="single-stock-kline-{kline_code}",
        suffix="json",
        sp="{start_time}-{end_time}",
        minutes=1440,
    )
    async def get_stock_kline(
        self,
        sec_id: str,
        sec_type: str,
        kline_code: Union[str, int],
        start_time: str,
        end_time: str,
    ) -> Union[Dict[str, Any], str]:
        """获取个股历史 K 线。

        Args:
            sec_id: 东方财富完整证券 ID。
            sec_type: 证券类型名称。
            kline_code: K 线周期代码：`5`=5分钟K，`15`=15分钟K，`30`=30分钟K，`60`=60分钟K，
                `100`=最近K线，`101`=日K，`102`=周K，`103`=月K，`104`=季K，
                `105`=半年K，`106`=年K，`111`=一年日K对比专用。
            start_time: 开始日期，格式 `YYYYMMDD`。
            end_time: 结束日期，格式 `YYYYMMDD`。

        Returns:
            东方财富 K 线 JSON，`data.klines` 为逗号分隔的 K 线字符串列表；
            找不到标的或请求失败时返回错误文本。历史财务/估值类数据缓存
            周期较长，此处按日缓存。
        """
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params: List[Tuple[str, Any]] = [
            ("fields1", "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"),
            ("fields2", "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"),
            ("rtntype", "6"),
            ("klt", kline_code),
            ("fqt", "1"),
            ("secid", sec_id),
            ("beg", start_time),
            ("end", end_time),
        ]
        resp = await self.stock_request(url, "GET", params=params)
        if isinstance(resp, int):
            return f"[SayuStock] 请求错误, 错误码: {resp}！"
        if resp["data"] is None:
            return ErroText["notStock"]

        resp["data"]["name"] = f"{resp['data']['name']} ({sec_type})"
        return resp

    async def get_intraday_by_query(self, query: str) -> Union[Dict[str, Any], str]:
        """按股票输入获取当日分时图数据。

        Args:
            query: 股票名称或代码。

        Returns:
            实时行情 JSON，包含 `data` 与 `trends`。`trends` 为分时点列表，适合 `个股 茅台` 或多股分时图。
        """
        stock = await self.resolve_stock(query)
        if stock is None:
            return ErroText["notStock"]
        return await self.get_single_stock(stock["secid"], stock["sec_type"])

    async def get_kline_by_query(
        self,
        query: str,
        kline_code: EastMoneyKlineCode,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> Union[Dict[str, Any], str]:
        """按股票输入和周期获取历史 K 线。

        Args:
            query: 股票名称或代码。
            kline_code: K 线周期代码：`5`=5分钟K，`15`=15分钟K，`30`=30分钟K，`60`=60分钟K，
                `100`=最近K线，`101`=日K，`102`=周K，`103`=月K，`104`=季K，
                `105`=半年K，`106`=年K，`111`=一年日K对比专用。
            start_time: 可选开始日期，格式 `YYYYMMDD`。
            end_time: 可选结束日期，格式 `YYYYMMDD`。

        Returns:
            东方财富 K 线 JSON，`data.klines` 为 K 线字符串列表；失败时返回错误文本。
        """
        from datetime import datetime, timedelta

        stock = await self.resolve_stock(query)
        if stock is None:
            return ErroText["notStock"]
        request_code: Union[str, int] = 101 if kline_code in ("100", "111") else kline_code
        if start_time is None:
            days = EASTMONEY_KLINE_DEFAULT_DAYS[kline_code]
            start_time = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        if end_time is None:
            end_time = datetime.now().strftime("%Y%m%d")
        return await self.get_stock_kline(stock["secid"], stock["sec_type"], request_code, start_time, end_time)

    async def get_5min_kline(self, query: str) -> Union[Dict[str, Any], str]:
        """获取 5 分钟 K 线；返回 `data.klines` 分钟 K 字符串列表。"""
        return await self.get_kline_by_query(query, "5")

    async def get_15min_kline(self, query: str) -> Union[Dict[str, Any], str]:
        """获取 15 分钟 K 线；返回 `data.klines` 分钟 K 字符串列表。"""
        return await self.get_kline_by_query(query, "15")

    async def get_30min_kline(self, query: str) -> Union[Dict[str, Any], str]:
        """获取 30 分钟 K 线；返回 `data.klines` 分钟 K 字符串列表。"""
        return await self.get_kline_by_query(query, "30")

    async def get_60min_kline(self, query: str) -> Union[Dict[str, Any], str]:
        """获取 60 分钟 K 线；返回 `data.klines` 小时级 K 线字符串列表。"""
        return await self.get_kline_by_query(query, "60")

    async def get_daily_kline(self, query: str) -> Union[Dict[str, Any], str]:
        """获取日 K；返回 `data.klines` 日线字符串列表。"""
        return await self.get_kline_by_query(query, "101")

    async def get_weekly_kline(self, query: str) -> Union[Dict[str, Any], str]:
        """获取周 K；返回 `data.klines` 周线字符串列表。"""
        return await self.get_kline_by_query(query, "102")

    async def get_monthly_kline(self, query: str) -> Union[Dict[str, Any], str]:
        """获取月 K；返回 `data.klines` 月线字符串列表。"""
        return await self.get_kline_by_query(query, "103")

    async def get_quarterly_kline(self, query: str) -> Union[Dict[str, Any], str]:
        """获取季 K；返回 `data.klines` 季线字符串列表。"""
        return await self.get_kline_by_query(query, "104")

    async def get_halfyear_kline(self, query: str) -> Union[Dict[str, Any], str]:
        """获取半年 K；返回 `data.klines` 半年线字符串列表。"""
        return await self.get_kline_by_query(query, "105")

    async def get_yearly_kline(self, query: str) -> Union[Dict[str, Any], str]:
        """获取年 K；返回 `data.klines` 年线字符串列表。"""
        return await self.get_kline_by_query(query, "106")

    @async_file_cache(market="{code}", sector="eastmoney-value-{value_type}", suffix="json", minutes=4320)
    async def get_value_series(
        self,
        code: str,
        secid: str,
        name: str,
        sec_type: str,
        value_type: EastMoneyValueType,
    ) -> Union[EastMoneyValueSeriesData, str]:
        """获取东方财富 PE/PB 历史估值序列。

        Args:
            code: 股票纯代码，例如 `600519`。
            secid: 东方财富证券 ID，例如 `1.600519`。
            name: 股票名称。
            sec_type: 证券类型。
            value_type: `pe` 表示市盈率 PE_TTM，`pb` 表示市净率 PB_MRQ。

        Returns:
            标准估值序列字典：`rows` 为按日期升序排列的 `{date, value}` 列表；
            无数据或失败时返回错误文本。估值数据缓存 3 天。
        """
        value_field = EASTMONEY_VALUE_FIELD_MAP[value_type]
        params: Dict[str, str] = {
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageNumber": "1",
            "pageSize": "720",
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
        }
        resp = await self.stock_request(EASTMONEY_VALUE_URL, params=params)
        if isinstance(resp, int):
            return f"[SayuStock] 错误代码: {resp}"
        result = resp["result"] if resp["result"] else {"data": []}
        rows: List[Dict[str, Any]] = result["data"]
        date_value_map: Dict[str, float] = {}
        for row in rows:
            trade_date = row["TRADE_DATE"] if "TRADE_DATE" in row else None
            raw_value = row[value_field] if value_field in row else None
            if trade_date is None or raw_value is None:
                continue
            value = float(raw_value)
            if value <= 0:
                continue
            date_value_map[str(trade_date)[:10]] = value
        if not date_value_map:
            return f"❌未获取到{EASTMONEY_VALUE_NAME_MAP[value_type]}历史数据，可能该标的不支持东方财富估值接口。"
        sorted_rows = [{"date": date, "value": date_value_map[date]} for date in sorted(date_value_map)]
        return {
            "code": code,
            "secid": secid,
            "name": name,
            "sec_type": sec_type,
            "value_type": value_type,
            "value_name": EASTMONEY_VALUE_NAME_MAP[value_type],
            "rows": sorted_rows,
        }

    async def get_pe_series(self, stock: EastMoneyStockItem) -> Union[EastMoneyValueSeriesData, str]:
        """获取单只股票 PE_TTM 历史序列；返回标准估值序列字典。"""
        return await self.get_value_series(stock["code"], stock["secid"], stock["name"], stock["sec_type"], "pe")

    async def get_pb_series(self, stock: EastMoneyStockItem) -> Union[EastMoneyValueSeriesData, str]:
        """获取单只股票 PB_MRQ 历史序列；返回标准估值序列字典。"""
        return await self.get_value_series(stock["code"], stock["secid"], stock["name"], stock["sec_type"], "pb")

    @async_file_cache(market="{market}", sector="{po}", suffix="json", sp="{is_loop}-{pz}", minutes=5)
    async def get_market_list(
        self,
        market: str,
        is_loop: bool = False,
        po: int = 1,
        pz: int = 20,
    ) -> Union[Dict[str, Any], str]:
        """获取行情列表/板块成分列表。

        Args:
            market: 市场或板块名称，也可以是东方财富 `fs` 表达式。
            is_loop: 是否循环拉取全部分页。
            po: 排序方向，`0` 为倒序，`1` 为正序。
            pz: 每页数量。

        Returns:
            东方财富行情列表 JSON，`data.diff` 为股票/板块明细数组；失败时
            返回错误文本。
        """
        params: List[Tuple[str, Any]] = [
            ("pz", str(pz)),
            ("po", str(po)),
            ("np", "1"),
            ("fltt", "2"),
            ("invt", "2"),
            ("fid", "f3"),
            ("pn", "1"),
        ]
        url = "http://push2.eastmoney.com/api/qt/clist/get"
        fs = market_dict[market] if market in market_dict else market
        if fs.startswith(("bk", "BK")):
            fs = f"b:{fs}"
        params.append(("fs", fs))
        params.append(("fields", ",".join(trade_detail_dict.keys())))

        resp = await self.stock_request(url, "GET", params=params)
        if isinstance(resp, int):
            return f"[SayuStock] 错误代码: {resp}"

        if is_loop and resp["data"] and len(resp["data"]["diff"]) >= 100:
            stop_event = asyncio.Event()
            pn = 2
            tasks = []
            params.remove(("pn", "1"))
            params.remove(("pz", "100"))
            params.append(("pz", str(len(resp["data"]["diff"]))))

            while not stop_event.is_set():
                for _ in range(10):
                    page_params = params.copy()
                    page_params.append(("pn", str(pn)))
                    tasks.append(self._append_market_page(resp, url, page_params, stop_event))
                    pn += 1
                await asyncio.gather(*tasks)
                tasks.clear()

            await asyncio.gather(*tasks)

        return resp

    async def _append_market_page(
        self,
        resp: Dict[str, Any],
        url: str,
        params: List[Tuple[str, Any]],
        stop_event: asyncio.Event,
    ) -> None:
        """追加行情列表分页数据。

        Args:
            resp: 第一页响应，会被原地追加 `data.diff`。
            url: 行情列表 API 地址。
            params: 当前页查询参数。
            stop_event: 停止分页拉取的异步事件。

        Returns:
            无返回值；分页数据会直接追加到 `resp`。
        """
        if stop_event.is_set():
            return
        await asyncio.sleep(random.uniform(0.4, 0.9))
        resp2 = await self.stock_request(url, params=params)
        if isinstance(resp2, int):
            stop_event.set()
            return

        if "code" not in resp2 and resp2["data"]:
            resp["data"]["diff"].extend(resp2["data"]["diff"])
            if len(resp2["data"]["diff"]) < 100:
                stop_event.set()
            return
        stop_event.set()

    @async_file_cache(market="大盘云图", sector="大盘云图", suffix="json", minutes=5)
    async def get_hotmap(self) -> Union[Dict[str, Any], str]:
        """获取东方财富股票热力图原始数据并转换为云图结构。

        Returns:
            兼容原云图渲染的 JSON，`data.diff` 中包含名称、涨跌幅、市值、
            所属行业等字段；失败时返回错误文本。
        """
        url = "https://quote.eastmoney.com/stockhotmap/api/getquotedata"
        resp = await self.stock_request(url)
        if isinstance(resp, int):
            return f"[SayuStock] 错误代码: {resp}"

        result: Dict[str, Any] = {
            "rc": 0,
            "rt": 6,
            "svr": 180606397,
            "lt": 1,
            "full": 1,
            "dlmkts": "",
            "data": {"total": 0, "diff": []},
        }

        for item in resp["data"]:
            assert isinstance(item, str)
            if "|" not in item:
                continue
            data_items = item.split("|")
            code = data_items[1]
            if code in chinese_stocks:
                stock_info = chinese_stocks[code]
                name = stock_info["name"]
                industry_l1 = stock_info["industry_l1"]
            else:
                name = code
                industry_l1 = code
            diff = {
                "f2": float(data_items[12]) / 100 if data_items[12] != "-" else 0,
                "f3": float(data_items[3]) / 100 if data_items[3] != "-" else 0,
                "f6": float(data_items[10]) if data_items[10] != "-" else 0,
                "f12": code,
                "f14": name,
                "f20": float(data_items[13]) * 100000 if data_items[13] != "-" else 0,
                "f100": industry_l1,
            }
            result["data"]["diff"].append(diff)

        result["data"]["total"] = len(result["data"]["diff"])
        return result


EASTMONEY_REQUESTER = EastMoneyRequester()
