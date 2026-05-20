"""
Binance Futures API client for trading operations
Using official python-binance SDK
"""
from binance.client import Client as BinanceClientBase
from binance.enums import (
    KLINE_INTERVAL_1MINUTE,
    KLINE_INTERVAL_3MINUTE,
    KLINE_INTERVAL_5MINUTE,
    KLINE_INTERVAL_15MINUTE,
    KLINE_INTERVAL_30MINUTE,
    KLINE_INTERVAL_1HOUR,
    KLINE_INTERVAL_2HOUR,
    KLINE_INTERVAL_4HOUR,
    KLINE_INTERVAL_6HOUR,
    KLINE_INTERVAL_8HOUR,
    KLINE_INTERVAL_12HOUR,
    KLINE_INTERVAL_1DAY,
    SIDE_BUY,
    SIDE_SELL,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_LIMIT,
    FUTURE_ORDER_TYPE_STOP_MARKET,
    FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
    TIME_IN_FORCE_GTC,
)
from typing import List, Optional, Dict, Tuple
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
import time
import os
import sys
import shutil
import logging

from trade_relay.env_loader import load_env

try:
    import certifi as _certifi
except ImportError:
    _certifi = None

# 配置logger
logger = logging.getLogger(__name__)


def _fix_ssl_cert_env():
    """修复 PyInstaller 打包环境下的 SSL 证书问题（详见 binance_api.py 中的说明）。"""
    # Step 1: 清除失效的 SSL 环境变量
    for _env_var in ('REQUESTS_CA_BUNDLE', 'SSL_CERT_FILE', 'CURL_CA_BUNDLE'):
        _val = os.environ.get(_env_var, '')
        if _val and not os.path.exists(_val):
            logger.warning(
                f"Removing stale SSL env var {_env_var} (path not found): {_val}"
            )
            del os.environ[_env_var]

    # Step 2: PyInstaller 打包模式下，将 CA bundle 复制到稳定位置（防止 temp 目录被清理）
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS') and _certifi:
        src = _certifi.where()
        if os.path.exists(src):
            stable_dir = os.path.join(os.path.expanduser('~'), '.ssl_cache')
            stable_ca = os.path.join(stable_dir, 'cacert.pem')
            try:
                os.makedirs(stable_dir, exist_ok=True)
                shutil.copy2(src, stable_ca)
                os.environ['REQUESTS_CA_BUNDLE'] = stable_ca
                logger.info(f"SSL CA bundle copied to stable location: {stable_ca}")
            except Exception as _e:
                logger.warning(f"Failed to copy CA bundle to stable location: {_e}")


_fix_ssl_cert_env()



class BinanceClient:
    """Client for interacting with Binance Futures API using official SDK"""
    
    # ✅ 统一超时配置
    CONNECT_TIMEOUT = 5  # 连接超时（秒）
    READ_TIMEOUT = 10  # 读取超时（秒）
    DEFAULT_TIMEOUT = 10  # 默认超时（秒，用于向后兼容）
    MAX_RETRIES = 2  # 默认最大重试次数

    @staticmethod
    def _normalize_credential(value: Optional[str]) -> str:
        """Normalize API credentials by trimming whitespace and optional quotes."""
        if value is None:
            return ''
        return str(value).strip().strip('"').strip("'")
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        proxy_config: Optional[Dict] = None,
        testnet: bool = False,
    ):
        # 加载 .env 文件中的环境变量
        # Use override=True to avoid stale shell env vars mixing old key with new secret.
        load_env(override=True)
        
        # 从环境变量或参数中加载配置（参数优先级高于环境变量）
        self.api_key = self._normalize_credential(
            api_key
            or os.getenv('TRADE_RELAY_BINANCE_API_KEY', '')
            or os.getenv('BINANCE_API_KEY', '')
        )
        self.secret_key = self._normalize_credential(
            secret_key
            or os.getenv('TRADE_RELAY_BINANCE_API_SECRET', '')
            or os.getenv('BINANCE_SECRET_KEY', '')
            or os.getenv('BINANCE_API_SECRET', '')
        )
        self.base_url = base_url or os.getenv('BINANCE_BASE_URL', '').strip() or 'https://fapi.binance.com'
        self.testnet = testnet
        
        # 检查必需的参数
        if not self.api_key or not self.secret_key:
            raise ValueError(
                "必须提供 api_key 和 secret_key。可以通过以下方式提供：\n"
                "1. 作为参数传入：BinanceClient(api_key='...', secret_key='...')\n"
                "2. 在 .env.production 中设置：TRADE_RELAY_BINANCE_API_KEY=... 和 TRADE_RELAY_BINANCE_API_SECRET=..."
            )
        
        # 解析并设置代理配置
        # 优先级：proxy_config 参数 > PROXY 环境变量 > BINANCE_PROXY_URL 环境变量
        self.proxy_config = None
        
        if proxy_config:
            # 如果提供了 proxy_config 参数，使用它
            proxy_url = self._parse_proxy_config(proxy_config)
            if proxy_url:
                self.proxy_config = {
                    'http': proxy_url,
                    'https': proxy_url
                }
        else:
            # 从环境变量读取代理配置
            proxy_url = os.getenv('PROXY', '').strip()
            if not proxy_url:
                # 兼容旧的 BINANCE_PROXY_URL 配置
                proxy_url = os.getenv('BINANCE_PROXY_URL', '').strip()
            
            if proxy_url:
                self.proxy_config = {
                    'http': proxy_url,
                    'https': proxy_url
                }
                import platform
                system = platform.system()
                logger.debug(f"🌐 使用代理配置 (OS: {system}): {proxy_url[:50]}..." if len(proxy_url) > 50 else f"🌐 使用代理配置 (OS: {system}): {proxy_url}")
        
        # 初始化 Binance Futures API 客户端
        self.client = BinanceClientBase(
            api_key=self.api_key,
            api_secret=self.secret_key,
            testnet=self.testnet
        )
        # Disable SSL verification when using a proxy that does TLS interception
        if self.proxy_config:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            try:
                self.client.session.verify = False
            except Exception:
                pass
        # Disable SSL verification when using a proxy that does TLS interception
        if self.proxy_config:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            try:
                self.client.session.verify = False
            except Exception:
                pass
        
        # 初始化默认请求头（用于 Algo Order API 等需要签名的请求）
        self.default_headers = {
            'X-MBX-APIKEY': self.api_key,
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        # 初始化的时候设置时间戳偏移
        self.last_time_sync = 0  # 记录上次同步时间
        self.time_sync_interval = 60  # 每60秒重新同步一次时间（缩短间隔，减少偏差累积）
        self.timestamp_buffer = 3000  # 时间戳缓冲（增加到3000ms，考虑网络延迟和时钟漂移）
        self.DEFAULT_RECV_WINDOW = 60000  # 默认接收窗口 60 秒（最大限度增加容错性，彻底解决时间戳问题）
        self.MAX_TIMESTAMP_RETRIES = 2  # 时间戳错误最大重试次数
        self.set_timestamp_offset()
    
    
    def set_timestamp_offset(self, force: bool = False):
        """
        同步时间戳偏移，避免时间戳错误
        
        Args:
            force: 是否强制同步（忽略时间间隔检查）
        """
        import time as time_module
        
        # 如果不是强制同步，检查是否需要重新同步
        if not force:
            current_time = time_module.time()
            if current_time - self.last_time_sync < self.time_sync_interval:
                return  # 距离上次同步时间太短，不需要重新同步
        
        try:
            # ✅ 改进：测量网络延迟，更准确地计算offset
            # 记录请求开始时间
            request_start_ms = int(time_module.time() * 1000)
            
            # Sync timestamp_offset with server time to avoid timestamp errors
            # SDK uses time.time() * 1000 + timestamp_offset, so we need to set offset correctly
            server_time = self.client.get_server_time()
            
            # 记录请求结束时间（用于计算网络延迟）
            request_end_ms = int(time_module.time() * 1000)
            network_latency_ms = request_end_ms - request_start_ms
            
            if server_time:
                # 使用请求结束时的本地时间（更准确）
                local_time_ms = request_end_ms
                server_time_ms = server_time['serverTime']
                
                # Calculate offset: server_time - local_time
                # This ensures SDK's (time.time() * 1000 + offset) equals server_time
                offset = server_time_ms - local_time_ms
                
                # ✅ 改进：增加缓冲时间，考虑网络延迟和时钟漂移
                # 缓冲时间 = 基础缓冲 + 网络延迟 + 额外安全缓冲
                total_buffer = self.timestamp_buffer + max(network_latency_ms, 0) + 500
                self.client.timestamp_offset = offset - total_buffer
                self.last_time_sync = time_module.time()

                logger.info(
                    "[BINANCE_SYNC] phase=sync force=%s server_time=%s local_time=%s latency_ms=%s raw_offset_ms=%s total_buffer_ms=%s applied_offset_ms=%s",
                    force,
                    server_time_ms,
                    local_time_ms,
                    network_latency_ms,
                    offset,
                    total_buffer,
                    self.client.timestamp_offset,
                )
            else:
                # If can't get server time, set offset to negative value to slow down timestamp
                self.client.timestamp_offset = -3000  # 增加默认缓冲
                self.last_time_sync = time_module.time()
                logger.warning(
                    "[BINANCE_SYNC] phase=missing_server_time applied_offset_ms=%s",
                    self.client.timestamp_offset,
                )
        except Exception as e:
            # 如果同步失败，设置一个保守的负偏移量
            self.client.timestamp_offset = -3000  # 增加默认缓冲
            self.last_time_sync = time_module.time()
            logger.warning(
                "[BINANCE_SYNC] phase=sync_error error=%s applied_offset_ms=%s",
                e,
                self.client.timestamp_offset,
            )
    
    def _should_resync_time(self) -> bool:
        """
        检查是否需要重新同步时间
        
        Returns:
            bool: 如果需要重新同步返回 True，否则返回 False
        """
        import time as time_module
        if not hasattr(self, 'last_time_sync') or self.last_time_sync == 0:
            return True
        elapsed = time_module.time() - self.last_time_sync
        return elapsed > self.time_sync_interval
    
    def _is_timestamp_error(self, response) -> bool:
        """
        检查响应是否是时间戳错误
        
        Args:
            response: requests.Response 对象
            
        Returns:
            bool: 如果是时间戳错误返回 True，否则返回 False
        """
        if response.status_code != 400:
            return False
        try:
            error_data = response.json()
            # Binance API 错误码 -1021 表示时间戳超出接收窗口
            return error_data.get('code') == -1021
        except (ValueError, KeyError):
            return False
    
    def _is_timestamp_error_exception(self, exception: Exception) -> bool:
        """
        检查异常是否是时间戳错误（-1021）
        
        Args:
            exception: 异常对象
            
        Returns:
            bool: 如果是时间戳错误返回 True，否则返回 False
        """
        response = getattr(exception, 'response', None)
        if response is not None:
            try:
                error_data = response.json()
                if error_data.get('code') == -1021:
                    return True
            except Exception:
                pass

        error_str = str(exception)
        lowered = error_str.lower()
        # 仅匹配 Binance 的时间戳错误码或典型错误文本，避免把普通请求参数里的
        # timestamp 字段误判成时间同步问题。
        return (
            '-1021' in error_str
            or 'timestamp for this request' in lowered
            or 'outside of the recvwindow' in lowered
            or 'ahead of the server' in lowered
        )
    
    def _handle_timestamp_error(self, response, request_func, attempt: int = 0):
        """
        处理时间戳错误，重新同步时间并重试
        
        Args:
            response: requests.Response 对象（包含时间戳错误）
            request_func: 重新执行请求的函数（无参数），返回 requests.Response 对象
            attempt: 当前重试次数
            
        Returns:
            重试后的 Response 对象，如果达到最大重试次数或重试失败返回 None
        """
        if attempt >= self.MAX_TIMESTAMP_RETRIES:
            logger.warning(f"⏰ 时间戳错误重试次数已达上限 ({self.MAX_TIMESTAMP_RETRIES})，放弃重试")
            return None
        
        logger.warning(f"⏰ 检测到时间戳错误，重新同步时间并重试 (attempt {attempt + 1}/{self.MAX_TIMESTAMP_RETRIES})")
        
        # 强制重新同步时间
        self.set_timestamp_offset(force=True)
        
        # 等待一小段时间确保时间同步完成
        import time
        time.sleep(0.1)
        
        # 重新执行请求
        try:
            retry_response = request_func()
            return retry_response
        except Exception as e:
            logger.error(f"⏰ 时间戳错误重试失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _generate_signed_request_body(self, params: Dict, debug: bool = False) -> Tuple[str, str]:
        """生成带签名的请求体
        
        Args:
            params: 请求参数字典（不包含signature）
            debug: 是否打印调试信息
            
        Returns:
            tuple: (signature, request_body) - 签名和请求体字符串
        """
        import hmac
        import hashlib
        from urllib.parse import urlencode
        import time
        
        # ⚡ 关键优化：在签名前的最后一刻生成时间戳，减少时间偏差
        if 'timestamp' not in params or params['timestamp'] is None:
            # 使用已同步的偏移量计算准确的服务器时间
            local_time_ms = int(time.time() * 1000)
            timestamp_offset = getattr(self.client, 'timestamp_offset', -3000)
            params['timestamp'] = str(local_time_ms + timestamp_offset)
        else:
            # 确保timestamp是字符串
            params['timestamp'] = str(params['timestamp'])
        
        # ⚡ 关键优化：确保所有请求都包含 recvWindow 参数
        if 'recvWindow' not in params:
            params['recvWindow'] = str(self.DEFAULT_RECV_WINDOW)
        
        # 所有参数值必须转换为字符串（根据Binance文档要求）
        params_for_signature = {}
        for k, v in params.items():
            if k != 'signature':
                # 确保所有值都是字符串
                params_for_signature[k] = str(v) if v is not None else ''
        
        # 按字母顺序排序参数并创建查询字符串
        sorted_params = sorted(params_for_signature.items())
        query_string = urlencode(sorted_params)
        
        if debug:
            logger.debug(f"🔐 签名查询字符串: {query_string}")
            logger.debug(f"🔐 使用的 secret_key 前缀: {self.secret_key[:10] if self.secret_key else 'N/A'}...")
            logger.debug(f"🔐 使用的 api_key 前缀: {self.api_key[:10] if self.api_key else 'N/A'}...")
        
        # 计算HMAC SHA256签名
        secret_key_bytes = self.secret_key.encode('utf-8')
        query_string_bytes = query_string.encode('utf-8')
        
        signature = hmac.new(
            secret_key_bytes,
            query_string_bytes,
            hashlib.sha256
        ).hexdigest()
        
        if debug:
            logger.debug(f"🔐 生成的签名: {signature}")
        
        # 重要：使用与签名相同的排序顺序构建请求体
        # Binance要求请求体参数顺序与签名查询字符串顺序一致
        request_params_list = list(sorted_params) + [('signature', signature)]
        request_body = urlencode(request_params_list)
        
        if debug:
            logger.debug(f"🔐 请求体（与签名顺序一致）: {request_body[:150]}...")
        
        return signature, request_body
    
    def _retry_request(self, request_func, max_retries: int = None, operation_name: str = "API请求") -> Optional[dict]:
        """
        通用的重试辅助函数，用于处理网络超时和连接错误
        
        Args:
            request_func: 一个无参数的函数，返回 requests.Response 对象
            max_retries: 最大重试次数（默认使用类常量 MAX_RETRIES）
            operation_name: 操作名称，用于日志记录
            
        Returns:
            成功时返回 response.json()，失败时返回 None
        """
        if max_retries is None:
            max_retries = self.MAX_RETRIES
        
        import requests
        from requests.exceptions import Timeout, ConnectionError, RequestException
        
        last_exception = None
        for attempt in range(max_retries + 1):  # 总共 max_retries + 1 次尝试
            try:
                response = request_func()
                
                if response.status_code == 200:
                    result = response.json()
                    if attempt > 0:
                        logger.info(f"✅ {operation_name}成功 (重试 {attempt} 次后)")
                    return result
                elif response.status_code in [500, 502, 503, 504]:
                    # ✅ 服务器错误，可以重试
                    error_text = response.text
                    logger.warning(f"⚠️ {operation_name}失败 (服务器错误, status={response.status_code}, attempt={attempt + 1}/{max_retries + 1}): {error_text}")
                    if attempt < max_retries:
                        wait_time = 2 ** attempt  # 指数退避：1秒, 2秒, 4秒...
                        logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                    else:
                        # 所有重试都失败
                        logger.error(f"❌ {operation_name}失败 (所有重试都失败, status={response.status_code})")
                        return None
                else:
                    # ✅ 其他HTTP错误（如400, 401, 404等），不重试
                    error_text = response.text
                    logger.warning(f"⚠️ {operation_name}失败 (HTTP错误, status={response.status_code}): {error_text}")
                    return None
                    
            except (Timeout, ConnectionError) as e:
                # ✅ 网络超时或连接错误，可以重试
                last_exception = e
                logger.warning(f"⚠️ {operation_name}失败 (网络错误, attempt={attempt + 1}/{max_retries + 1}): {str(e)}")
                if attempt < max_retries:
                    wait_time = 2 ** attempt  # 指数退避：1秒, 2秒, 4秒...
                    logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                    continue
                else:
                    # 所有重试都失败
                    logger.error(f"❌ {operation_name}失败 (网络错误, 所有重试都失败): {str(e)}")
                    return None
            except RequestException as e:
                # ✅ 其他请求异常，可以重试
                last_exception = e
                logger.warning(f"⚠️ {operation_name}失败 (请求异常, attempt={attempt + 1}/{max_retries + 1}): {str(e)}")
                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"❌ {operation_name}失败 (请求异常, 所有重试都失败): {str(e)}")
                    return None
            except Exception as e:
                # ✅ 其他异常，不重试（可能是参数错误等）
                logger.error(f"❌ {operation_name}失败 (未知错误): {str(e)}")
                return None
        
        # 如果所有重试都失败
        if last_exception:
            logger.error(f"❌ {operation_name}失败 (最终失败): {str(last_exception)}")
        return None
    
    def _parse_proxy_config(self, proxy_config) -> Optional[str]:
        """解析代理配置，支持HTTP和SOCKS5两种格式"""
        if not proxy_config:
            return None
        
        # 如果proxy_config是字符串格式（如 "http://127.0.0.1:10809"）
        if isinstance(proxy_config, str):
            return proxy_config
        
        # 如果proxy_config是字典格式
        if isinstance(proxy_config, dict):
            host = proxy_config.get('host')
            port = proxy_config.get('port')
            username = proxy_config.get('username')
            password = proxy_config.get('password')
            proxy_type = proxy_config.get('type', 'socks5')  # 默认为socks5
            
            if not host or not port:
                return None
            
            # 构建代理URL
            if username and password:
                proxy_url = f"{proxy_type}://{username}:{password}@{host}:{port}"
            else:
                proxy_url = f"{proxy_type}://{host}:{port}"
            
            return proxy_url
        
        return None
    
    def _request(self, method: str, endpoint: str, params: dict = None) -> dict:
        """Make HTTP request to Binance API with proxy support"""
        import requests
        url = f"{self.base_url}{endpoint}"
        headers = {'X-MBX-APIKEY': self.config.BINANCE_API_KEY}
        
        # 使用代理配置（如果有）
        proxies = self.proxy_config if self.proxy_config else None
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, params=params, proxies=proxies, timeout=10)
            else:
                response = requests.post(url, headers=headers, params=params, proxies=proxies, timeout=10)
            
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.debug(f"API request failed: {e}")
            raise
    
    def get_all_tickers(self) -> List[str]:
        """Get all active futures trading pairs (USDT only)"""
        try:
            # 使用 SDK 获取期货交易所信息
            exchange_info = self.client.futures_exchange_info()
            # 只获取 USDT 交易对
            tickers = [
                symbol['symbol'] for symbol in exchange_info['symbols']
                if symbol['status'] == 'TRADING' 
                   and symbol['contractType'] == 'PERPETUAL'
                   and symbol['symbol'].endswith('USDT')
            ]
            return tickers
        except Exception as e:
            logger.debug(f"Failed to get tickers: {e}")
            return []
    
    def get_open_interest(self, symbol: str) -> Optional[float]:
        """Get current open interest quantity for a symbol"""
        try:
            data = self.client.futures_open_interest(symbol=symbol)
            return float(data.get('openInterest', 0))
        except Exception as e:
            logger.debug(f"Failed to get open interest for {symbol}: {e}")
            return None
    
    def get_open_interest_value(self, symbol: str) -> Optional[float]:
        """Get current open interest value in USDT"""
        try:
            # Get OI quantity
            oi_data = self.client.futures_open_interest(symbol=symbol)
            oi_quantity = float(oi_data.get('openInterest', 0))
            
            # Get current price
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            current_price = float(ticker.get('price', 0))
            
            # Calculate OI value in USDT
            oi_value = oi_quantity * current_price
            return oi_value
        except Exception as e:
            logger.debug(f"Failed to get open interest value for {symbol}: {e}")
            return None
    
    def get_open_interest_history(self, symbol: str, period: str = '15m', limit: int = 2, 
                              start_time: Optional[datetime] = None, 
                              end_time: Optional[datetime] = None) -> List[dict]:
        """Get open interest history using /futures/data/openInterestHist endpoint
        
        Args:
            symbol: Trading pair symbol
            period: Time period (5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d)
            limit: Number of records (max 500, default 30)
            start_time: Start time (optional, only last 1 month data available)
            end_time: End time (optional, only last 1 month data available)
        """
        try:
            import requests
            url = f"{self.base_url}/futures/data/openInterestHist"
            params = {
                'symbol': symbol,
                'period': period,
                'limit': min(limit, 500)  # API max limit is 500
            }
            
            # Add time range parameters if provided
            if start_time:
                params['startTime'] = int(start_time.timestamp() * 1000)
            if end_time:
                params['endTime'] = int(end_time.timestamp() * 1000)
            
            headers = {'X-MBX-APIKEY': self.config.BINANCE_API_KEY}
            
            # Use proxy if configured
            proxies = self.proxy_config if self.proxy_config else None
            response = requests.get(url, headers=headers, params=params, proxies=proxies, timeout=10)
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logger.debug(f"Failed to get OI history for {symbol}: {e}")
            return []
    
    def get_24h_volume(self, symbol: str) -> Optional[float]:
        """Get 24-hour trading volume for a symbol"""
        try:
            data = self.client.futures_ticker(symbol=symbol)
            return float(data.get('volume', 0))
        except Exception as e:
            logger.debug(f"Failed to get volume for {symbol}: {e}")
            return None
        
    def get_24h_price_change(self, symbol: str) -> Optional[float]:
        """Get 24-hour price change percentage for a symbol"""
        try:
            data = self.client.futures_ticker(symbol=symbol)
            price_change_percent = float(data.get('priceChangePercent', 0))
            return price_change_percent
        except Exception as e:
            logger.debug(f"Failed to get 24h price change for {symbol}: {e}")
            return None
    
    def get_all_24h_price_changes(self) -> Dict[str, float]:
        """Get 24h price changes for all symbols (batch, more efficient)
        
        Returns:
            Dictionary mapping symbol to price change percentage
        """
        try:
            # Get all tickers in one API call
            tickers = self.client.futures_ticker()
            
            # Convert to dictionary keyed by symbol
            result = {}
            for ticker in tickers:
                symbol = ticker.get('symbol')
                if symbol:
                    price_change_percent = ticker.get('priceChangePercent', 0)
                    result[symbol] = float(price_change_percent) if price_change_percent else 0.0
            
            return result
        except Exception as e:
            logger.debug(f"Failed to get all 24h price changes (batch): {e}")
            return {}
    
    def get_all_24h_ticker_data(self) -> List[Dict]:
        """Get 24h ticker data for all symbols (batch)
        
        Returns:
            List of ticker data dictionaries with symbol, priceChangePercent, quoteVolume, volume, etc.
        """
        try:
            # Get all tickers in one API call
            tickers = self.client.futures_ticker()
            return tickers
        except Exception as e:
            logger.debug(f"Failed to get all 24h ticker data (batch): {e}")
            return []
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price for a symbol"""
        try:
            data = self.client.futures_symbol_ticker(symbol=symbol)
            return float(data.get('price', 0))
        except Exception as e:
            logger.debug(f"Failed to get price for {symbol}: {e}")
            return None
    
    def get_price_precision(self, symbol: str) -> Optional[int]:
        """Get price precision for a symbol from exchange info"""
        try:
            exchange_info = self.client.futures_exchange_info()
            symbol_info = None
            for s in exchange_info.get('symbols', []):
                if s['symbol'] == symbol:
                    symbol_info = s
                    break
            
            if symbol_info:
                filters = symbol_info.get('filters', [])
                for filter_item in filters:
                    if filter_item.get('filterType') == 'PRICE_FILTER':
                        tick_size = float(filter_item.get('tickSize', '1'))
                        # Calculate precision from tick_size
                        # e.g., tickSize=0.01 -> precision=2, tickSize=0.0001 -> precision=4
                        if tick_size >= 1:
                            precision = 0
                        else:
                            step_str = str(tick_size)
                            if '.' in step_str:
                                precision = len(step_str.split('.')[-1].rstrip('0'))
                            else:
                                precision = 0
                        return precision
            return None
        except Exception as e:
            logger.debug(f"Failed to get price precision for {symbol}: {e}")
            return None
    
    def get_symbol_precision_info(self, symbol: str) -> Optional[dict]:
        """Get all precision information for a symbol from exchange info"""
        try:
            exchange_info = self.client.futures_exchange_info()
            symbol_info = None
            for s in exchange_info.get('symbols', []):
                if s['symbol'] == symbol:
                    symbol_info = s
                    break
            
            if not symbol_info:
                return None
            
            result = {}
            
            # Get base and quote asset precision
            result['base_asset_precision'] = symbol_info.get('baseAssetPrecision')
            result['quote_asset_precision'] = symbol_info.get('quotePrecision')
            
            # Get filters
            filters = symbol_info.get('filters', [])
            
            for filter_item in filters:
                filter_type = filter_item.get('filterType')
                
                if filter_type == 'PRICE_FILTER':
                    tick_size = float(filter_item.get('tickSize', '1'))
                    max_price = float(filter_item.get('maxPrice', '0'))
                    min_price = float(filter_item.get('minPrice', '0'))
                    
                    result['tick_size'] = tick_size
                    result['max_price'] = max_price
                    result['min_price'] = min_price
                    
                    # Calculate price precision from tick_size
                    # Handle scientific notation (e.g., 1e-05) by converting to Decimal first
                    if tick_size >= 1:
                        price_precision = 0
                    else:
                        # Convert to Decimal to handle scientific notation properly
                        tick_decimal = Decimal(str(tick_size))
                        # Convert to normalized string without scientific notation
                        tick_str = format(tick_decimal.normalize(), 'f')
                        if '.' in tick_str:
                            decimal_part = tick_str.split('.')[-1].rstrip('0')
                            price_precision = len(decimal_part)
                        else:
                            price_precision = 0
                    result['price_precision'] = price_precision
                
                elif filter_type == 'LOT_SIZE':
                    step_size = float(filter_item.get('stepSize', '1'))
                    result['step_size'] = step_size  # Store step_size for precise rounding
                    
                    # Calculate quantity precision from stepSize
                    # Handle scientific notation (e.g., 1e-05) by converting to Decimal first
                    if step_size >= 1:
                        quantity_precision = 0
                    else:
                        # Convert to Decimal to handle scientific notation properly
                        step_decimal = Decimal(str(step_size))
                        # Convert to normalized string without scientific notation
                        step_str = format(step_decimal.normalize(), 'f')
                        if '.' in step_str:
                            decimal_part = step_str.split('.')[-1].rstrip('0')
                            quantity_precision = len(decimal_part)
                        else:
                            quantity_precision = 0
                    result['quantity_precision'] = quantity_precision
            
            return result
        except Exception as e:
            logger.debug(f"Failed to get symbol precision info for {symbol}: {e}")
            return None
    
    def get_kline_data(self, symbol: str, interval: str = '15m', limit: int = 2) -> List:
        """Get kline/candlestick data"""
        try:
            # 将间隔转换为 SDK 的格式
            interval_map = {
                '1m': KLINE_INTERVAL_1MINUTE,
                '3m': KLINE_INTERVAL_3MINUTE,
                '5m': KLINE_INTERVAL_5MINUTE,
                '15m': KLINE_INTERVAL_15MINUTE,
                '30m': KLINE_INTERVAL_30MINUTE,
                '1h': KLINE_INTERVAL_1HOUR,
                '2h': KLINE_INTERVAL_2HOUR,
                '4h': KLINE_INTERVAL_4HOUR,
                '6h': KLINE_INTERVAL_6HOUR,
                '8h': KLINE_INTERVAL_8HOUR,
                '12h': KLINE_INTERVAL_12HOUR,
                '1d': KLINE_INTERVAL_1DAY,
            }
            
            kline_interval = interval_map.get(interval, KLINE_INTERVAL_15MINUTE)
            data = self.client.futures_klines(symbol=symbol, interval=kline_interval, limit=limit)
            return data
        except Exception as e:
            logger.debug(f"Failed to get kline data for {symbol}: {e}")
            return []
    
    def get_server_time(self) -> Optional[datetime]:
        """Get Binance server time and return as datetime object"""
        try:
            # Use SDK's get_server_time method which returns server time in milliseconds
            server_time_ms = self.client.get_server_time()
            server_time = datetime.fromtimestamp(server_time_ms['serverTime'] / 1000.0)
            return server_time
        except Exception as e:
            logger.debug(f"Failed to get server time: {e}")
            return None
    
    def get_account_info(self) -> dict:
        """Get account information (supports sub-account if configured)"""
        for attempt in range(self.MAX_TIMESTAMP_RETRIES + 1):
            try:
                self.set_timestamp_offset(force=(attempt > 0))
                data = self.client.futures_account(recvWindow=self.DEFAULT_RECV_WINDOW)
                return data
            except Exception as e:
                if self._is_timestamp_error_exception(e) and attempt < self.MAX_TIMESTAMP_RETRIES:
                    logger.warning(f"⏰ get_account_info 时间戳错误重试 (attempt {attempt + 1}/{self.MAX_TIMESTAMP_RETRIES + 1})")
                    self.set_timestamp_offset(force=True)
                    continue
                logger.debug(f"Failed to get account info: {e}")
                return {}

    def get_position_information(self, symbol: str | None = None, recv_window: int | None = None) -> List[dict]:
        """Get raw futures position information (all positions including zero-qty).

        Uses /fapi/v2/positionRisk directly so the response includes the
        ``leverage`` field (the SDK's futures_position_information targets v3
        which does not expose leverage).  Falls back to the SDK if the direct
        request fails.
        """
        import requests as _requests
        for attempt in range(self.MAX_TIMESTAMP_RETRIES + 1):
            try:
                # positionRisk is a signed direct HTTP call; force a fresh time sync
                # before each attempt to reduce -1021 timestamp drift under proxies.
                self.set_timestamp_offset(force=True)
                url = f"{self.base_url}/fapi/v2/positionRisk"
                params: dict[str, object] = {
                    "recvWindow": recv_window or self.DEFAULT_RECV_WINDOW,
                }
                if symbol:
                    params["symbol"] = symbol
                _, query = self._generate_signed_request_body(params, debug=False)
                request_timestamp = params.get("timestamp")
                request_recv_window = params.get("recvWindow")
                response = _requests.get(
                    f"{url}?{query}",
                    headers={"X-MBX-APIKEY": self.api_key},
                    proxies=self.proxy_config,
                    timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT),
                )
                response.raise_for_status()
                return response.json() or []
            except Exception as e:
                if self._is_timestamp_error_exception(e) and attempt < self.MAX_TIMESTAMP_RETRIES:
                    response_text = ""
                    if hasattr(e, "response") and getattr(e, "response") is not None:
                        try:
                            response_text = getattr(e.response, "text", "") or ""
                        except Exception:
                            response_text = ""
                    logger.warning(
                        "[BINANCE_POSITION_RISK] phase=retry attempt=%s/%s symbol=%s request_timestamp=%s recv_window=%s applied_offset_ms=%s response=%s",
                        attempt + 1,
                        self.MAX_TIMESTAMP_RETRIES + 1,
                        symbol or "ALL",
                        request_timestamp,
                        request_recv_window,
                        getattr(self.client, 'timestamp_offset', None),
                        response_text[:500] if response_text else str(e),
                    )
                    self.set_timestamp_offset(force=True)
                    continue
                logger.debug(f"get_position_information v2 failed, falling back to SDK: {e}")
                break

        # Fallback: SDK (v3, no leverage field)
        request_kwargs: Dict[str, object] = {
            'recvWindow': recv_window or self.DEFAULT_RECV_WINDOW,
        }
        if symbol:
            request_kwargs['symbol'] = symbol
        try:
            self.set_timestamp_offset(force=True)
            rows = self.client.futures_position_information(**request_kwargs)
            return rows or []
        except Exception as e:
            logger.debug(f"Failed to get raw position information: {e}")
            return []

    def get_account_balance(self, asset: str = "USDT") -> float:
        """Return available balance for *asset* in the futures wallet."""
        info = self.get_account_info()
        assets = info.get("assets", [])
        for entry in assets:
            if entry.get("asset", "").upper() == asset.upper():
                return float(entry.get("availableBalance", 0.0))
        return 0.0

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        """Set leverage for a symbol and return Binance's response payload."""
        try:
            return self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except Exception as e:
            status_code = getattr(e, 'status_code', None)
            error_code = getattr(e, 'code', None)
            error_message = getattr(e, 'message', None) or str(e)
            logger.warning(
                "Failed to set leverage for %s to %s (status=%s code=%s): %s",
                symbol,
                leverage,
                status_code,
                error_code,
                error_message,
            )
            raise RuntimeError(error_message) from e
    
    def get_position_mode(self) -> Optional[bool]:
        """Get position mode: True = Hedge Mode, False = One-way Mode"""
        try:
            # 在请求前强制同步时间戳（确保时间准确）
            self.set_timestamp_offset(force=True)
            result = self.client.futures_get_position_mode()
            # result is a dict with 'dualSidePosition' key
            return result.get('dualSidePosition', False)
        except Exception as e:
            logger.debug(f"Failed to get position mode: {e}")
            return None
    
    def set_position_mode(self, hedge_mode: bool = False) -> bool:
        """Set position mode: True = Hedge Mode, False = One-way Mode"""
        try:
            self.client.futures_change_position_mode(dualSidePosition=hedge_mode)
            return True
        except Exception as e:
            logger.debug(f"Failed to set position mode: {e}")
            return False
    
    def place_market_order(self, symbol: str, side: str, quantity: float, position_side: str = None, reduce_only: bool = False) -> Optional[dict]:
        """Place a market order
        
        Args:
            symbol: Trading pair symbol
            side: Order side (BUY/SELL)
            quantity: Order quantity
            position_side: Position side (LONG/SHORT) for hedge mode
            reduce_only: If True, order will only reduce position (for closing positions)
        """
        # 实现时间戳错误重试机制
        for attempt in range(self.MAX_TIMESTAMP_RETRIES + 1):
            try:
                # 在每次尝试前同步时间戳
                if attempt > 0:
                    logger.warning(f"⏰ 时间戳错误重试 (attempt {attempt + 1}/{self.MAX_TIMESTAMP_RETRIES + 1})")
                    self.set_timestamp_offset(force=True)
                    import time
                    time.sleep(0.1)
                else:
                    # 第一次尝试也要确保时间戳是最新的
                    self.set_timestamp_offset()
                
                # Format quantity according to symbol's precision requirements
                quantity_str = self.format_quantity_by_precision(quantity, symbol)
                quantity = float(quantity_str)  # Convert back to float for SDK (if needed)
                
                # Check position mode
                # 默认使用对冲模式（双向持仓），因为 Binance 账户设置的就是双向持仓模式
                # 即使 get_position_mode() 因时间戳等错误失败，也不会错误地以单向模式下单
                position_mode = self.get_position_mode()
                if position_mode is None:
                    logger.warning(f"⚠️ get_position_mode() 失败，默认使用对冲模式（双向持仓）: symbol={symbol}, side={side}")
                    position_mode = True  # 默认对冲模式
                
                # 在双向持仓模式下，直接使用 REST API 而不是 SDK，以避免 SDK 自动添加 reduceOnly 参数
                if position_mode is True:
                    # Hedge Mode: 使用 REST API 直接下单
                    import requests
                    
                    url = f"{self.base_url}/fapi/v1/order"
                    
                    # ⚡ 不再在这里设置 timestamp，让 _generate_signed_request_body 在签名前的最后一刻生成
                    # 这样可以最小化时间偏差
                    
                    # Prepare parameters (不包含 timestamp，将由 _generate_signed_request_body 自动添加)
                    params = {
                        'symbol': symbol,
                        'side': side.upper(),  # BUY or SELL
                        'type': 'MARKET',
                        'quantity': quantity_str  # Use formatted string
                    }
                    
                    # Hedge Mode: MUST add positionSide, CANNOT add reduceOnly
                    if position_side:
                        params['positionSide'] = position_side
                    else:
                        # position_side 在对冲模式下必须明确指定，否则无法判断是开仓还是平仓
                        raise ValueError(
                            f"对冲模式下 place_market_order 必须传入 position_side 参数 "
                            f"(symbol={symbol}, side={side})。请检查调用方是否正确传入 position_side。"
                        )
                    
                    # Note: In hedge mode, reduceOnly is not supported by Binance API
                    # We rely on positionSide to close the correct position
                    
                    # 打印下单参数用于调试
                    import json
                    logger.debug(f"📤 准备下单: {symbol}, side={side}, quantity={quantity}, position_side={params.get('positionSide', 'N/A')}, position_mode={position_mode}")
                    logger.debug(f"   📋 完整订单参数: {json.dumps(params, indent=2, default=str)}")
                    logger.debug(f"   🔑 API Key前缀: {self.api_key[:10] if self.api_key else 'N/A'}...")
                    logger.debug(f"   🌐 Base URL: {self.base_url}")
                    
                    # 使用内部方法生成签名和请求体
                    signature, request_body = self._generate_signed_request_body(params, debug=False)
                    
                    # Make request using default headers and proxy config
                    response = requests.post(
                        url,
                        headers=self.default_headers,
                        data=request_body,
                        proxies=self.proxy_config,
                        timeout=10
                    )
                    
                    # Check response
                    if response.status_code != 200:
                        error_text = response.text
                        logger.debug(f"❌ 下单API错误 (status={response.status_code}): {error_text}")
                        try:
                            error_json = response.json()
                            error_msg = error_json.get('msg', error_text)
                            error_code = error_json.get('code', 'UNKNOWN')
                            raise Exception(f"APIError(code={error_code}): {error_msg}")
                        except:
                            raise Exception(f"HTTP {response.status_code}: {error_text}")
                    
                    result = response.json()
                else:
                    # One-way Mode: 使用 SDK
                    order_params = {
                        'symbol': symbol,
                        'side': SIDE_BUY if side.upper() == 'BUY' else SIDE_SELL,
                        'type': ORDER_TYPE_MARKET,
                        'quantity': quantity  # Use formatted float value
                    }
                    
                    # ⚡ 重要：即使检测到单向持仓模式，也要添加 positionSide（双向持仓账户必需）
                    # 如果账户实际是双向持仓，但 get_position_mode() 检测失败或返回 False，会导致 -4061 错误
                    if position_mode is True or position_side:
                        # 双向持仓模式：必须指定 positionSide
                        if position_side:
                            order_params['positionSide'] = position_side
                        else:
                            # 根据订单方向自动设置
                            order_params['positionSide'] = 'LONG' if side.upper() == 'BUY' else 'SHORT'
                    # 单向持仓模式：可以使用 reduceOnly
                    elif reduce_only:
                        order_params['reduceOnly'] = True
                    
                    # 打印下单参数用于调试
                    import json
                    logger.debug(f"📤 准备下单: {symbol}, side={side}, quantity={quantity}, position_mode={position_mode}, position_side={order_params.get('positionSide', 'N/A')}")
                    logger.debug(f"   📋 完整订单参数: {json.dumps(order_params, indent=2, default=str)}")
                    logger.debug(f"   🔑 API Key前缀: {self.api_key[:10] if self.api_key else 'N/A'}...")
                    logger.debug(f"   🌐 Base URL: {self.base_url}")
                    
                    # 在下单前强制同步时间戳（确保时间准确）
                    self.set_timestamp_offset(force=True)
                    
                    # 使用 SDK 的 futures 下单方法
                    result = self.client.futures_create_order(**order_params)
                
                # 打印API返回结果
                logger.debug(f"📥 API返回结果:")
                if result is None:
                    logger.debug(f"   ❌ 返回结果: None")
                elif isinstance(result, dict):
                    logger.debug(f"   ✅ 返回字典:")
                    logger.debug(json.dumps(result, indent=2, default=str))
                else:
                    logger.debug(f"   ⚠️  返回类型: {type(result)}, 值: {result}")
                
                # 检查返回结果
                if result is None:
                    error_msg = "futures_create_order返回None（无订单信息）"
                    logger.debug(f"❌ {error_msg} for {symbol}")
                    return {
                        'error': True,
                        'error_type': 'NoneResult',
                        'error_message': error_msg,
                        'symbol': symbol,
                        'side': side
                    }
                
                # 检查是否有orderId（成功下单应该有orderId）
                if not result.get('orderId'):
                    error_msg = f"下单返回结果异常：缺少orderId，返回结果={result}"
                    logger.debug(f"❌ {error_msg} for {symbol}")
                    return {
                        'error': True,
                        'error_type': 'InvalidResult',
                        'error_message': error_msg,
                        'symbol': symbol,
                        'side': side,
                        'raw_result': str(result)
                    }
                
                logger.debug(f"✅ 下单成功: {symbol}, order_id={result.get('orderId')}")
                return result
            
            except Exception as e:
                # 检查是否是时间戳错误，如果是且还有重试次数，则继续重试
                if self._is_timestamp_error_exception(e) and attempt < self.MAX_TIMESTAMP_RETRIES:
                    logger.warning(f"⏰ 检测到时间戳错误: {str(e)}")
                    # continue 会进入下一次循环，重新同步时间并重试
                    continue
                
                # 其他错误或达到最大重试次数，记录并返回错误
                error_msg = str(e)
                error_type = type(e).__name__
                logger.debug(f"❌ Failed to place {side} order for {symbol}: [{error_type}] {error_msg}")
                # 记录完整的异常堆栈
                logger.exception(f"下单异常详情")
                # 返回包含错误信息的字典，而不是None，这样调用方可以获取错误详情
                return {
                    'error': True,
                    'error_type': error_type,
                    'error_message': error_msg,
                    'symbol': symbol,
                    'side': side
                }
        
        # 如果所有重试都失败（正常不应该到这里）
        return {
            'error': True,
            'error_type': 'MaxRetriesExceeded',
            'error_message': f'达到最大重试次数 ({self.MAX_TIMESTAMP_RETRIES + 1})，下单失败',
            'symbol': symbol,
            'side': side
        }
    
    def place_conditional_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
        price: Optional[float] = None,
        position_side: str = None,
    ) -> Optional[dict]:
        """Place a conditional order via the standard futures /fapi/v1/order endpoint.

        Args:
            symbol:        Trading pair symbol, e.g. "BTCUSDT"
            side:          "BUY" or "SELL"
            quantity:      Order quantity in base asset units
            stop_price:    Trigger price (stopPrice)
            price:         Limit price – when provided the order type is STOP (trigger-limit);
                           when None / 0 the order type is STOP_MARKET (trigger-market).
            position_side: "LONG" or "SHORT" for hedge-mode accounts; None for one-way mode.

        Returns:
            Binance order dict on success, None on failure.
        """
        order_type = "STOP" if (price is not None and price > 0) else "STOP_MARKET"

        for attempt in range(self.MAX_TIMESTAMP_RETRIES + 1):
            try:
                if attempt > 0:
                    logger.warning("⏰ 时间戳错误重试 (attempt %d/%d)", attempt + 1, self.MAX_TIMESTAMP_RETRIES + 1)
                    self.set_timestamp_offset(force=True)
                    import time as _time; _time.sleep(0.1)
                else:
                    self.set_timestamp_offset()

                stop_price_str = self.format_price_by_precision(stop_price, symbol)
                if not stop_price_str:
                    raise ValueError(f"Cannot format stop_price={stop_price} for {symbol}")

                from decimal import Decimal, ROUND_HALF_UP
                qty_str = f"{float(Decimal(str(quantity)).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)):.3f}"

                position_mode = self.get_position_mode()

                import requests

                url = f"{self.base_url}/fapi/v1/order"

                params: dict = {
                    "symbol":    symbol,
                    "side":      side.upper(),
                    "type":      order_type,
                    "quantity":  qty_str,
                    "stopPrice": stop_price_str,
                    "workingType": "CONTRACT_PRICE",
                    "timeInForce": "GTC",
                }

                if order_type == "STOP":
                    price_str = self.format_price_by_precision(price, symbol)
                    params["price"] = price_str
                    params["timeInForce"] = "GTC"

                if position_mode is True:
                    params["positionSide"] = position_side if position_side else (
                        "LONG" if side.upper() == "BUY" else "SHORT"
                    )
                else:
                    params["positionSide"] = "BOTH"
                    params["reduceOnly"] = "false"

                _sig, request_body = self._generate_signed_request_body(params, debug=False)

                import time as _time
                from requests.exceptions import Timeout, ConnectionError as ConnError, RequestException

                max_retries = self.MAX_RETRIES
                last_exc = None
                for req_attempt in range(max_retries + 1):
                    try:
                        resp = requests.post(
                            url,
                            headers=self.default_headers,
                            data=request_body,
                            proxies=self.proxy_config,
                            timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT),
                        )
                        if resp.status_code == 200:
                            return resp.json()
                        body = resp.json() if resp.content else {}
                        code = body.get("code", resp.status_code)
                        msg  = body.get("msg", resp.text)
                        # Timestamp error → retry at outer loop
                        if code in (-1021,):
                            raise Exception(f"TIMESTAMP_ERROR: {msg}")
                        if resp.status_code in (500, 502, 503, 504) and req_attempt < max_retries:
                            _time.sleep(2 ** req_attempt); continue
                        raise Exception(f"APIError(code={code}): {msg}")
                    except (Timeout, ConnError, RequestException) as exc:
                        last_exc = exc
                        if req_attempt < max_retries:
                            _time.sleep(2 ** req_attempt); continue
                        raise
                if last_exc:
                    raise last_exc

            except Exception as exc:
                msg_str = str(exc)
                if "TIMESTAMP_ERROR" in msg_str and attempt < self.MAX_TIMESTAMP_RETRIES:
                    continue
                logger.exception("place_conditional_order failed symbol=%s side=%s type=%s qty=%s stop=%s price=%s: %s",
                                 symbol, side, order_type, quantity, stop_price, price, exc)
                return None

        logger.error("place_conditional_order: max timestamp retries exceeded symbol=%s", symbol)
        return None

    def place_limit_order(self, symbol: str, side: str, quantity: float, price: float, position_side: str = None, post_only: bool = False, expire_seconds: int = None) -> Optional[dict]:
        """Place a limit order
        
        Args:
            symbol: Trading pair symbol
            side: Order side (BUY/SELL)
            quantity: Order quantity
            price: Limit price
            position_side: Position side (LONG/SHORT) for hedge mode
            post_only: Whether to submit as maker-only (GTX)
            expire_seconds: Order expiration time in seconds (default: None, means GTC - Good Till Cancel)
        """
        # 实现时间戳错误重试机制
        for attempt in range(self.MAX_TIMESTAMP_RETRIES + 1):
            try:
                # 在每次尝试前同步时间戳
                if attempt > 0:
                    logger.warning(f"⏰ 时间戳错误重试 (attempt {attempt + 1}/{self.MAX_TIMESTAMP_RETRIES + 1})")
                    self.set_timestamp_offset(force=True)
                    import time
                    time.sleep(0.1)
                else:
                    # 第一次尝试也要确保时间戳是最新的
                    self.set_timestamp_offset()
                
                # Format price according to symbol's precision
                price_str = self.format_price_by_precision(price, symbol)
                price = float(price_str)
                
                # Format quantity to 3 decimal places using Decimal for precision
                from decimal import Decimal, ROUND_HALF_UP
                quantity_decimal = Decimal(str(quantity)).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
                quantity = float(quantity_decimal)
                # Format as string with exactly 3 decimal places
                quantity_str = f"{quantity:.3f}"
                
                # Check position mode
                position_mode = self.get_position_mode()
                
                # 在双向持仓模式下，直接使用 REST API 而不是 SDK
                if position_mode is True:
                    # Hedge Mode: 使用 REST API 直接下单
                    import requests
                    import time
                    from datetime import datetime, timedelta
                    
                    url = f"{self.base_url}/fapi/v1/order"
                    
                    # ⚡ 不再在这里设置 timestamp，让 _generate_signed_request_body 在签名前的最后一刻生成
                    # 这样可以最小化时间偏差
                    
                    # Prepare parameters (不包含 timestamp，将由 _generate_signed_request_body 自动添加)
                    # Use the quantity_str already formatted above (with 3 decimal places)
                    params = {
                        'symbol': symbol,
                        'side': side.upper(),  # BUY or SELL
                        'type': 'LIMIT',
                        'quantity': quantity_str,  # Use formatted string with 3 decimal places
                        'price': price_str,  # Use formatted price string
                        'timeInForce': 'GTX' if post_only else 'GTC'
                    }
                    
                    # 如果指定了过期时间，尝试使用 GTD (Good Till Date)
                    # 注意：币安期货API使用 goodTillDate 参数（不是 expireTime）
                    if not post_only and expire_seconds is not None and expire_seconds > 0:
                        try:
                            # 计算过期时间（UTC时间）
                            expire_time = datetime.now() + timedelta(seconds=expire_seconds)
                            expire_timestamp = int(expire_time.timestamp() * 1000)
                            params['timeInForce'] = 'GTD'  # Good Till Date
                            params['goodTillDate'] = expire_timestamp  # Binance API 使用 goodTillDate 参数
                            logger.debug(f"📅 限价单设置过期时间: {expire_seconds}秒后 ({expire_time.strftime('%Y-%m-%d %H:%M:%S')})")
                        except Exception as e:
                            logger.debug(f"⚠️  设置过期时间失败，使用GTC: {e}")
                            # 如果设置失败，继续使用GTC，将在应用层实现定时取消
                            params['timeInForce'] = 'GTC'  # 回退到 GTC
                            params.pop('goodTillDate', None)  # 移除可能存在的 goodTillDate
                    
                    # Hedge Mode: MUST add positionSide
                    if position_side:
                        params['positionSide'] = position_side
                    else:
                        # Default: LONG for BUY, SHORT for SELL
                        params['positionSide'] = 'LONG' if side.upper() == 'BUY' else 'SHORT'
                    
                    # 打印下单参数用于调试
                    import json
                    logger.debug(f"📤 准备下限价单: {symbol}, side={side}, quantity={quantity}, price={price_str}, position_side={params.get('positionSide', 'N/A')}, position_mode={position_mode}")
                    logger.debug(f"   📋 完整订单参数: {json.dumps(params, indent=2, default=str)}")
                    
                    # 使用内部方法生成签名和请求体
                    signature, request_body = self._generate_signed_request_body(params, debug=False)
                    
                    # Make request using default headers and proxy config
                    response = requests.post(
                        url,
                        headers=self.default_headers,
                        data=request_body,
                        proxies=self.proxy_config,
                        timeout=10
                    )
                    
                    # Check response
                    if response.status_code != 200:
                        error_text = response.text
                        logger.debug(f"❌ 限价单API错误 (status={response.status_code}): {error_text}")
                        try:
                            error_json = response.json()
                            error_msg = error_json.get('msg', error_text)
                            error_code = error_json.get('code', 'UNKNOWN')
                            raise Exception(f"APIError(code={error_code}): {error_msg}")
                        except:
                            raise Exception(f"HTTP {response.status_code}: {error_text}")
                    
                    # 成功获取响应结果
                    result = response.json()
                else:
                    # One-way Mode: 使用 SDK
                    # Use the quantity_str already formatted above (with 3 decimal places)
                    order_params = {
                        'symbol': symbol,
                        'side': SIDE_BUY if side.upper() == 'BUY' else SIDE_SELL,
                        'type': ORDER_TYPE_LIMIT,
                        'quantity': quantity_str,  # Use formatted string with 3 decimal places
                        'price': price_str,
                        'timeInForce': 'GTX' if post_only else TIME_IN_FORCE_GTC
                    }
                    
                    # ⚡ 重要：即使检测到单向持仓模式，也要添加 positionSide（双向持仓账户必需）
                    # 如果账户实际是双向持仓，但 get_position_mode() 检测失败或返回 False，会导致 -4061 错误
                    if position_side:
                        # 双向持仓模式：必须指定 positionSide
                        order_params['positionSide'] = position_side
                    else:
                        # 根据订单方向自动设置
                        order_params['positionSide'] = 'LONG' if side.upper() == 'BUY' else 'SHORT'
                    
                    # 如果指定了过期时间，尝试添加过期时间参数
                    # 注意：Binance SDK 使用 goodTillDate 参数（不是 expireTime）
                    if not post_only and expire_seconds is not None and expire_seconds > 0:
                        try:
                            from datetime import datetime, timedelta
                            expire_time = datetime.now() + timedelta(seconds=expire_seconds)
                            expire_timestamp = int(expire_time.timestamp() * 1000)
                            order_params['timeInForce'] = 'GTD'  # Good Till Date
                            order_params['goodTillDate'] = expire_timestamp  # Binance SDK 使用 goodTillDate 参数
                            logger.debug(f"📅 限价单设置过期时间: {expire_seconds}秒后 ({expire_time.strftime('%Y-%m-%d %H:%M:%S')})")
                        except Exception as e:
                            logger.debug(f"⚠️  设置过期时间失败，使用GTC: {e}")
                            # 如果设置失败，继续使用GTC，将在应用层实现定时取消
                            order_params['timeInForce'] = TIME_IN_FORCE_GTC  # 回退到 GTC
                            order_params.pop('goodTillDate', None)  # 移除可能存在的 goodTillDate
                    
                    # 打印下单参数用于调试
                    import json
                    logger.debug(f"📤 准备下限价单: {symbol}, side={side}, quantity={quantity}, price={price_str}, position_mode={position_mode}, position_side={order_params.get('positionSide', 'N/A')}")
                    logger.debug(f"   📋 完整订单参数: {json.dumps(order_params, indent=2, default=str)}")
                    
                    # 使用 SDK 的 futures 下单方法
                    result = self.client.futures_create_order(**order_params)
                
                # 打印API返回结果
                logger.debug(f"📥 限价单API返回结果:")
                if result is None:
                    logger.debug(f"   ❌ 返回结果: None")
                elif isinstance(result, dict):
                    logger.debug(f"   ✅ 返回字典:")
                    import json
                    logger.debug(json.dumps(result, indent=2, default=str))
                else:
                    logger.debug(f"   ⚠️  返回类型: {type(result)}, 值: {result}")
                
                # 检查返回结果
                if result is None:
                    error_msg = "futures_create_order返回None（无订单信息）"
                    logger.debug(f"❌ {error_msg} for {symbol}")
                    return {
                        'error': True,
                        'error_type': 'NoneResult',
                        'error_message': error_msg,
                        'symbol': symbol,
                        'side': side
                    }
                
                # 检查是否有orderId（成功下单应该有orderId）
                if not result.get('orderId'):
                    error_msg = f"限价单返回结果异常：缺少orderId，返回结果={result}"
                    logger.debug(f"❌ {error_msg} for {symbol}")
                    return {
                        'error': True,
                        'error_type': 'InvalidResult',
                        'error_message': error_msg,
                        'symbol': symbol,
                        'side': side,
                        'raw_result': str(result)
                    }
                
                logger.debug(f"✅ 限价单下单成功: {symbol}, order_id={result.get('orderId')}, price={price_str}")
                return result
            
            except Exception as e:
                # 检查是否是时间戳错误，如果是且还有重试次数，则继续重试
                if self._is_timestamp_error_exception(e) and attempt < self.MAX_TIMESTAMP_RETRIES:
                    logger.warning(f"⏰ 检测到时间戳错误: {str(e)}")
                    # continue 会进入下一次循环，重新同步时间并重试
                    continue
                
                # 其他错误或达到最大重试次数，记录并返回错误
                error_msg = str(e)
                error_type = type(e).__name__
                logger.debug(f"❌ Failed to place limit order for {symbol}: [{error_type}] {error_msg}")
                # 记录完整的异常堆栈
                logger.exception(f"下限价单异常详情")
                # 返回包含错误信息的字典，而不是None，这样调用方可以获取错误详情
                return {
                    'error': True,
                    'error_type': error_type,
                    'error_message': error_msg,
                    'symbol': symbol,
                    'side': side
                }
        
        # 如果所有重试都失败（正常不应该到这里）
        return {
            'error': True,
            'error_type': 'MaxRetriesExceeded',
            'error_message': f'达到最大重试次数 ({self.MAX_TIMESTAMP_RETRIES + 1})，下单失败',
            'symbol': symbol,
            'side': side
        }
    
    def format_price_by_precision(self, price: float, symbol: str) -> str:
        """Format price according to symbol's price precision from Binance API
        
        Args:
            price: The price to format
            symbol: Trading pair symbol
            
        Returns:
            Formatted price string that meets Binance precision requirements
        """
        # Import Decimal locally to ensure it's available in this scope
        from decimal import Decimal
        
        try:
            # Get precision info from Binance API (always use latest data)
            precision_info = self.get_symbol_precision_info(symbol)
            tick_size = None
            price_precision = None
            
            if precision_info:
                tick_size = precision_info.get('tick_size')
                price_precision = precision_info.get('price_precision')
            
            # Process tick_size if we have it (from API)
            if tick_size and tick_size > 0:
                # Round to nearest tick_size using decimal arithmetic to avoid floating point errors
                price_decimal = Decimal(str(price))
                tick_decimal = Decimal(str(tick_size))
                
                # Check if tick_size is too large (would cause rounding to 0)
                if tick_size >= price:
                    logger.debug(f"⚠️  Warning: tick_size {tick_size} >= price {price} for {symbol}, using original price without rounding")
                    rounded_price = price
                else:
                    # Round to nearest tick
                    rounded_decimal = (price_decimal / tick_decimal).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * tick_decimal
                    rounded_price = float(rounded_decimal)
                    
                    # Ensure rounded_price is valid and close to original price
                    if rounded_price <= 0 or abs(rounded_price - price) > price * 0.5:
                        logger.debug(f"⚠️  Warning: Rounded price {rounded_price} is invalid or too far from original {price} for {symbol}, using original price")
                        rounded_price = price
                
                # If rounded_price is 0 but original price is not 0, use original price
                if rounded_price == 0 and price != 0:
                    logger.debug(f"⚠️  Warning: Rounded price is 0 but original was {price} for {symbol} (tick_size={tick_size}), using original price")
                    rounded_price = price
                
                # Determine precision for formatting
                # Use price_precision from database or API, but ensure it's reasonable
                if price_precision is not None:
                    # Use the precision from database/API (already calculated from tick_size)
                    precision = price_precision
                else:
                    # Calculate precision from tick_size if not already calculated
                    if tick_size >= 1:
                        precision = 0
                    else:
                        tick_decimal = Decimal(str(tick_size))
                        # Convert to normalized string without scientific notation
                        tick_str = format(tick_decimal.normalize(), 'f')
                        if '.' in tick_str:
                            decimal_part = tick_str.split('.')[-1].rstrip('0')
                            precision = len(decimal_part)
                        else:
                            precision = 8  # Default fallback
                
                # Ensure precision is valid (non-negative and reasonable, max 8 for Binance)
                if precision < 0:
                    precision = 8  # Fallback to 8 decimal places
                if precision > 8:
                    precision = 8  # Cap at 8 decimal places (Binance max)
                
                # Format with determined precision
                #print(f"🔧 格式化价格: {rounded_price} 使用精度 {precision} 位小数")
                formatted = f"{rounded_price:.{precision}f}"
                #print(f"🔧 格式化结果: {formatted}")
                
                # Final validation: if formatted result is "0" but original price is not 0, use original price
                if formatted == "0" or formatted == "0.0":
                    if price != 0:
                        logger.debug(f"⚠️  Warning: Formatted result is '0' but original was {price} for {symbol}, using original price with fallback precision")
                        # Use original price with calculated precision
                        formatted = f"{price:.{precision}f}"
                
                # Remove trailing zeros and decimal point if needed, but ensure result is not empty
                # Special handling: if the result would be empty (e.g., "0" -> ""), keep at least "0"
                if formatted == "0" or formatted == "0.0":
                    # Only return "0" if original price was actually 0
                    if price == 0:
                        result = "0"
                    else:
                        # Use original price with calculated precision
                        result = f"{price:.{precision}f}".rstrip('0').rstrip('.')
                        if not result or result == '':
                            result = formatted
                else:
                    result = formatted.rstrip('0').rstrip('.')
                    # If result is empty after stripping, return the formatted string
                    if not result or result == '':
                        result = formatted
                
                return result
            
            # Fallback: use default precision if API call fails (max 8 decimal places)
            formatted = f"{price:.8f}"
            
            # Remove trailing zeros but ensure not empty
            # Special handling: if the result would be empty (e.g., "0" -> ""), keep at least "0"
            if formatted == "0" or formatted == "0.0":
                result = "0"
            else:
                result = formatted.rstrip('0').rstrip('.')
                if not result or result == '':
                    result = formatted
            return result
            
        except Exception as e:
            logger.debug(f"⚠️  Error formatting price for {symbol}: {e}, using fallback")
            logger.exception(f"价格格式化异常详情")
            # Fallback formatting - ensure we always return a valid string (max 8 decimal places)
            formatted = f"{price:.8f}"
            # Special handling: if the result would be empty (e.g., "0" -> ""), keep at least "0"
            if formatted == "0" or formatted == "0.0":
                result = "0"
            else:
                result = formatted.rstrip('0').rstrip('.')
                if not result or result == '':
                    result = formatted
            return result
    
    def format_quantity_by_precision(self, quantity: float, symbol: str) -> str:
        """Format quantity according to symbol's quantity precision from Binance API
        
        Args:
            quantity: The quantity to format
            symbol: Trading pair symbol
            
        Returns:
            Formatted quantity string that meets Binance precision requirements
        """
        from decimal import Decimal, ROUND_HALF_UP
        
        try:
            # Get precision info from Binance API (always use latest data)
            precision_info = self.get_symbol_precision_info(symbol)
            step_size = None
            quantity_precision = None
            
            if precision_info:
                step_size = precision_info.get('step_size')
                quantity_precision = precision_info.get('quantity_precision')
            
            # Process step_size if we have it (from API)
            if step_size and step_size > 0:
                # Round to nearest step_size using decimal arithmetic to avoid floating point errors
                quantity_decimal = Decimal(str(quantity))
                step_decimal = Decimal(str(step_size))
                
                # Check if step_size is too large (would cause rounding to 0)
                if step_size >= quantity:
                    logger.debug(f"⚠️  Warning: step_size {step_size} >= quantity {quantity} for {symbol}, using original quantity without rounding")
                    rounded_quantity = quantity
                else:
                    # Round to nearest step
                    rounded_decimal = (quantity_decimal / step_decimal).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * step_decimal
                    rounded_quantity = float(rounded_decimal)
                    
                    # Ensure rounded_quantity is valid and close to original quantity
                    if rounded_quantity <= 0 or abs(rounded_quantity - quantity) > quantity * 0.5:
                        logger.debug(f"⚠️  Warning: Rounded quantity {rounded_quantity} is invalid or too far from original {quantity} for {symbol}, using original quantity")
                        rounded_quantity = quantity
                
                # If rounded_quantity is 0 but original quantity is not 0, use original quantity
                if rounded_quantity == 0 and quantity != 0:
                    logger.debug(f"⚠️  Warning: Rounded quantity is 0 but original was {quantity} for {symbol} (step_size={step_size}), using original quantity")
                    rounded_quantity = quantity
                
                # Determine precision for formatting
                if quantity_precision is not None:
                    # Use the precision from API (already calculated from step_size)
                    precision = quantity_precision
                else:
                    # Calculate precision from step_size if not already calculated
                    if step_size >= 1:
                        precision = 0
                    else:
                        step_decimal = Decimal(str(step_size))
                        # Convert to normalized string without scientific notation
                        step_str = format(step_decimal.normalize(), 'f')
                        if '.' in step_str:
                            decimal_part = step_str.split('.')[-1].rstrip('0')
                            precision = len(decimal_part)
                        else:
                            precision = 8  # Default fallback
                
                # Ensure precision is valid (non-negative and reasonable, max 8 for Binance)
                if precision < 0:
                    precision = 8  # Fallback to 8 decimal places
                if precision > 8:
                    precision = 8  # Cap at 8 decimal places (Binance max)
                
                # Format with determined precision
                formatted = f"{rounded_quantity:.{precision}f}"
                
                # Final validation: if formatted result is "0" but original quantity is not 0, use original quantity
                if formatted == "0" or formatted == "0.0":
                    if quantity != 0:
                        logger.debug(f"⚠️  Warning: Formatted result is '0' but original was {quantity} for {symbol}, using original quantity with fallback precision")
                        formatted = f"{quantity:.{precision}f}"
                
                # Remove trailing zeros and decimal point if needed, but ensure result is not empty
                if formatted == "0" or formatted == "0.0":
                    if quantity == 0:
                        result = "0"
                    else:
                        result = f"{quantity:.{precision}f}".rstrip('0').rstrip('.')
                        if not result or result == '':
                            result = formatted
                else:
                    result = formatted.rstrip('0').rstrip('.')
                    if not result or result == '':
                        result = formatted
                
                return result
            
            # Fallback: use default precision if API call fails (max 8 decimal places)
            formatted = f"{quantity:.8f}"
            
            # Remove trailing zeros but ensure not empty
            if formatted == "0" or formatted == "0.0":
                result = "0"
            else:
                result = formatted.rstrip('0').rstrip('.')
                if not result or result == '':
                    result = formatted
            return result
            
        except Exception as e:
            logger.debug(f"⚠️  Error formatting quantity for {symbol}: {e}, using fallback")
            logger.exception(f"数量格式化异常详情")
            # Fallback formatting - ensure we always return a valid string (max 8 decimal places)
            formatted = f"{quantity:.8f}"
            if formatted == "0" or formatted == "0.0":
                result = "0"
            else:
                result = formatted.rstrip('0').rstrip('.')
                if not result or result == '':
                    result = formatted
            return result
    
    def place_stop_loss_order(self, symbol: str, side: str, stop_price: float, quantity: float, position_side: str = None) -> Optional[dict]:
        """Place a stop-loss order
        
        Args:
            symbol: Trading pair symbol
            side: Order side (BUY/SELL)
            stop_price: Stop price
            quantity: Order quantity
            position_side: Position side (LONG/SHORT) for hedge mode
        """
        try:
            # Check position mode
            position_mode = self.get_position_mode()
            # position_mode: True = Hedge Mode, False = One-way Mode, None = unknown
            
            # Format stop_price using symbol's actual precision from Binance API
            stop_price_str = self.format_price_by_precision(stop_price, symbol)
            
            # Validate formatted string is not empty
            if not stop_price_str or stop_price_str.strip() == '':
                raise ValueError(f"Formatted stop_price is empty for {symbol} (original: {stop_price})")
            
            # Convert back to float for validation
            try:
                stop_price_validated = float(stop_price_str)
            except ValueError as e:
                raise ValueError(f"Could not convert formatted stop_price '{stop_price_str}' to float for {symbol} (original: {stop_price}): {e}")
            
            # Ensure price is positive and valid
            if stop_price_validated <= 0:
                raise ValueError(f"Invalid stop_price: {stop_price_validated} (original: {stop_price}, formatted: {stop_price_str})")
            
            # Binance now requires using Algo Order API for STOP_MARKET and TAKE_PROFIT_MARKET orders
            # API endpoint: POST /fapi/v1/algoOrder
            import requests
            import time
            
            url = f"{self.base_url}/fapi/v1/algoOrder"
            
            # Get server time to ensure timestamp is correct
            try:
                server_time = self.get_server_time()
                if server_time:
                    # Use server time + small offset to account for network delay
                    timestamp = int(server_time.timestamp() * 1000) + 100  # Add 100ms offset
                    logger.debug(f"🔐 使用Binance服务器时间: {server_time}, timestamp={timestamp}")
                else:
                    # Fallback to local time
                    timestamp = int(time.time() * 1000)
                    logger.debug(f"⚠️  无法获取服务器时间，使用本地时间: timestamp={timestamp}")
            except Exception as e:
                # Fallback to local time if server time fetch fails
                timestamp = int(time.time() * 1000)
                logger.debug(f"⚠️  获取服务器时间失败: {e}，使用本地时间: timestamp={timestamp}")
            
            # Prepare parameters according to Binance Algo Order API
            params = {
                'algoType': 'CONDITIONAL',  # Required: CONDITIONAL for STOP_MARKET/TAKE_PROFIT_MARKET
                'symbol': symbol,
                'side': side.upper(),  # BUY or SELL
                'type': 'STOP_MARKET',  # Stop Market order type
                'quantity': str(quantity),  # Convert to string
                'triggerPrice': stop_price_str,  # Use triggerPrice instead of stopPrice
                'timeInForce': 'GTC',  # Good Till Cancel
                'workingType': 'CONTRACT_PRICE'  # Use contract price
            }
            
            # Add positionSide and other parameters based on position mode
            if position_mode is True:
                # Hedge Mode: MUST add positionSide, CANNOT add reduceOnly or closePosition
                if position_side:
                    params['positionSide'] = position_side
                else:
                    # Default: opposite side for stop loss
                    params['positionSide'] = 'SHORT' if side.upper() == 'BUY' else 'LONG'
                # Do NOT add reduceOnly or closePosition in hedge mode
            else:
                # One-way Mode: use BOTH for positionSide, add reduceOnly
                params['positionSide'] = 'BOTH'
                params['reduceOnly'] = 'true'  # Reduce only for stop loss
                # closePosition is optional in one-way mode, but we don't need it since we specify quantity
            
            # Add timestamp (must be string according to Binance docs)
            params['timestamp'] = str(timestamp)
            
            # 使用内部方法生成签名和请求体
            signature, request_body = self._generate_signed_request_body(params, debug=False)
            
            # ✅ 使用重试机制发送请求
            from requests.exceptions import Timeout, ConnectionError, RequestException
            
            max_retries = self.MAX_RETRIES
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    # Make request using default headers and proxy config from initialization
                    response = requests.post(
                        url, 
                        headers=self.default_headers, 
                        data=request_body, 
                        proxies=self.proxy_config, 
                        timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT)
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        if attempt > 0:
                            logger.info(f"✅ 下止损单成功 (重试 {attempt} 次后): {symbol}")
                        if result is None:
                            return {
                                'error': True,
                                'error_type': 'NoneResult',
                                'error_message': 'STOP_MARKET algo order returned no JSON body',
                                'symbol': symbol,
                                'side': side,
                            }
                        return result
                    elif response.status_code in [500, 502, 503, 504]:
                        # ✅ 服务器错误，可以重试
                        error_text = response.text
                        logger.warning(f"⚠️ 下止损单失败 (服务器错误, status={response.status_code}, attempt={attempt + 1}/{max_retries + 1}): {error_text}")
                        if attempt < max_retries:
                            wait_time = 2 ** attempt
                            logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                            time.sleep(wait_time)
                            continue
                        else:
                            error_text = response.text
                            try:
                                error_json = response.json()
                                error_msg = error_json.get('msg', error_text)
                                error_code = error_json.get('code', 'UNKNOWN')
                                raise Exception(f"APIError(code={error_code}): {error_msg}")
                            except:
                                raise Exception(f"HTTP {response.status_code}: {error_text}")
                    else:
                        # ✅ 其他HTTP错误，不重试
                        error_text = response.text
                        logger.debug(f"❌ Algo Order API 错误 (status={response.status_code}): {error_text}")
                        try:
                            error_json = response.json()
                            error_msg = error_json.get('msg', error_text)
                            error_code = error_json.get('code', 'UNKNOWN')
                            raise Exception(f"APIError(code={error_code}): {error_msg}")
                        except:
                            raise Exception(f"HTTP {response.status_code}: {error_text}")
                            
                except (Timeout, ConnectionError) as e:
                    # ✅ 网络超时或连接错误，可以重试
                    last_exception = e
                    logger.warning(f"⚠️ 下止损单失败 (网络错误, attempt={attempt + 1}/{max_retries + 1}): {str(e)}")
                    if attempt < max_retries:
                        wait_time = 2 ** attempt
                        logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise
                except RequestException as e:
                    # ✅ 其他请求异常，可以重试
                    last_exception = e
                    logger.warning(f"⚠️ 下止损单失败 (请求异常, attempt={attempt + 1}/{max_retries + 1}): {str(e)}")
                    if attempt < max_retries:
                        wait_time = 2 ** attempt
                        logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise
            
            # 如果所有重试都失败
            if last_exception:
                raise last_exception
        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__
            logger.debug(f"Failed to place stop loss order for {symbol}: [{error_type}] {error_msg}")
            if 'stop_price_str' in locals():
                logger.debug(f"  stopPrice={stop_price}, formatted_string='{stop_price_str}' (length={len(stop_price_str) if stop_price_str else 0})")
            else:
                logger.debug(f"  stopPrice={stop_price}, formatted_string=N/A (format_price_by_precision failed)")
            if 'params' in locals():
                logger.debug(f"  请求参数: {params}")
            logger.exception(f"下止损单异常详情")
            return {
                'error': True,
                'error_type': error_type,
                'error_message': error_msg,
                'symbol': symbol,
                'side': side,
            }

    def place_close_all_stop_loss_order(self, symbol: str, side: str, stop_price: float) -> Optional[dict]:
        """Place a one-way close-all STOP_MARKET order via the algo order API."""
        try:
            position_mode = self.get_position_mode()
            if position_mode is True:
                raise ValueError("closePosition=true STOP_MARKET is only supported by this helper in one-way mode")

            stop_price_str = self.format_price_by_precision(stop_price, symbol)
            if not stop_price_str or stop_price_str.strip() == '':
                raise ValueError(f"Formatted stop_price is empty for {symbol} (original: {stop_price})")

            stop_price_validated = float(stop_price_str)
            if stop_price_validated <= 0:
                raise ValueError(f"Invalid stop_price: {stop_price_validated} (original: {stop_price})")

            import requests
            from requests.exceptions import Timeout, ConnectionError, RequestException

            url = f"{self.base_url}/fapi/v1/algoOrder"
            params = {
                'algoType': 'CONDITIONAL',
                'symbol': symbol,
                'side': side.upper(),
                'positionSide': 'BOTH',
                'type': 'STOP_MARKET',
                'triggerPrice': stop_price_str,
                'closePosition': 'true',
                'workingType': 'CONTRACT_PRICE',
                'timeInForce': 'GTC',
                'timestamp': str(int(time.time() * 1000)),
            }

            _, request_body = self._generate_signed_request_body(params, debug=False)

            max_retries = self.MAX_RETRIES
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    response = requests.post(
                        url,
                        headers=self.default_headers,
                        data=request_body,
                        proxies=self.proxy_config,
                        timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT)
                    )

                    if response.status_code == 200:
                        result = response.json()
                        if attempt > 0:
                            logger.info(f"✅ 下全仓止损单成功 (重试 {attempt} 次后): {symbol}")
                        if result is None:
                            return {
                                'error': True,
                                'error_type': 'NoneResult',
                                'error_message': 'close-all STOP_MARKET algo order returned no JSON body',
                                'symbol': symbol,
                                'side': side,
                            }
                        return result

                    error_text = response.text
                    if response.status_code in [500, 502, 503, 504]:
                        logger.warning(f"⚠️ 下全仓止损单失败 (服务器错误, status={response.status_code}, attempt={attempt + 1}/{max_retries + 1}): {error_text}")
                        if attempt < max_retries:
                            wait_time = 2 ** attempt
                            logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                            time.sleep(wait_time)
                            continue

                    try:
                        error_json = response.json()
                        error_msg = error_json.get('msg', error_text)
                        error_code = error_json.get('code', 'UNKNOWN')
                        raise Exception(f"APIError(code={error_code}): {error_msg}")
                    except Exception:
                        raise Exception(f"HTTP {response.status_code}: {error_text}")

                except (Timeout, ConnectionError, RequestException) as exc:
                    last_exception = exc
                    logger.warning(f"⚠️ 下全仓止损单失败 (网络/请求异常, attempt={attempt + 1}/{max_retries + 1}): {str(exc)}")
                    if attempt < max_retries:
                        wait_time = 2 ** attempt
                        logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                    raise

            if last_exception:
                raise last_exception
        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__
            logger.debug(f"Failed to place close-all stop loss order for {symbol}: [{error_type}] {error_msg}")
            if 'params' in locals():
                logger.debug(f"  请求参数: {params}")
            logger.exception("下全仓止损单异常详情")
            return {
                'error': True,
                'error_type': error_type,
                'error_message': error_msg,
                'symbol': symbol,
                'side': side,
            }
    
    def place_take_profit_order(self, symbol: str, side: str, price: float, quantity: float, position_side: str = None) -> Optional[dict]:
        """Place a take-profit order
        
        Args:
            symbol: Trading pair symbol
            side: Order side (BUY/SELL)
            price: Take profit price
            quantity: Order quantity
            position_side: Position side (LONG/SHORT) for hedge mode
        """
        try:
            # Check position mode
            position_mode = self.get_position_mode()
            # position_mode: True = Hedge Mode, False = One-way Mode, None = unknown
            
            # Format price using symbol's actual precision from Binance API
            price_str = self.format_price_by_precision(price, symbol)
            
            # Convert back to float for validation
            price_validated = float(price_str)
            
            # Ensure price is positive and valid
            if price_validated <= 0:
                raise ValueError(f"Invalid price: {price_validated} (original: {price})")
            
            # Binance now requires using Algo Order API for STOP_MARKET and TAKE_PROFIT_MARKET orders
            # API endpoint: POST /fapi/v1/algoOrder
            import requests
            import time
            
            url = f"{self.base_url}/fapi/v1/algoOrder"
            
            # Get server time to ensure timestamp is correct
            try:
                server_time = self.get_server_time()
                if server_time:
                    # Use server time + small offset to account for network delay
                    timestamp = int(server_time.timestamp() * 1000) + 100  # Add 100ms offset
                    logger.debug(f"🔐 使用Binance服务器时间: {server_time}, timestamp={timestamp}")
                else:
                    # Fallback to local time
                    timestamp = int(time.time() * 1000)
                    logger.debug(f"⚠️  无法获取服务器时间，使用本地时间: timestamp={timestamp}")
            except Exception as e:
                # Fallback to local time if server time fetch fails
                timestamp = int(time.time() * 1000)
                logger.debug(f"⚠️  获取服务器时间失败: {e}，使用本地时间: timestamp={timestamp}")
            
            # Prepare parameters according to Binance Algo Order API
            params = {
                'algoType': 'CONDITIONAL',  # Required: CONDITIONAL for STOP_MARKET/TAKE_PROFIT_MARKET
                'symbol': symbol,
                'side': side.upper(),  # BUY or SELL
                'type': 'TAKE_PROFIT_MARKET',  # Take Profit Market order type
                'quantity': str(quantity),  # Convert to string
                'triggerPrice': price_str,  # Use triggerPrice instead of stopPrice
                'timeInForce': 'GTC',  # Good Till Cancel
                'workingType': 'CONTRACT_PRICE'  # Use contract price
            }
            
            # Add positionSide and other parameters based on position mode
            if position_mode is True:
                # Hedge Mode: MUST add positionSide, CANNOT add reduceOnly or closePosition
                if position_side:
                    params['positionSide'] = position_side
                else:
                    # Default: opposite side for take profit
                    params['positionSide'] = 'SHORT' if side.upper() == 'BUY' else 'LONG'
                # Do NOT add reduceOnly or closePosition in hedge mode
            else:
                # One-way Mode: use BOTH for positionSide, add reduceOnly
                params['positionSide'] = 'BOTH'
                params['reduceOnly'] = 'true'  # Reduce only for take profit
                # closePosition is optional in one-way mode, but we don't need it since we specify quantity
            
            # Add timestamp (must be string according to Binance docs)
            params['timestamp'] = str(timestamp)
            
            # 使用内部方法生成签名和请求体
            signature, request_body = self._generate_signed_request_body(params, debug=False)
            
            # ✅ 使用重试机制发送请求
            from requests.exceptions import Timeout, ConnectionError, RequestException
            
            max_retries = self.MAX_RETRIES
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    # Make request using default headers and proxy config from initialization
                    response = requests.post(
                        url, 
                        headers=self.default_headers, 
                        data=request_body, 
                        proxies=self.proxy_config, 
                        timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT)
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        if attempt > 0:
                            logger.info(f"✅ 下止盈单成功 (重试 {attempt} 次后): {symbol}")
                        if result is None:
                            return {
                                'error': True,
                                'error_type': 'NoneResult',
                                'error_message': 'TAKE_PROFIT_MARKET algo order returned no JSON body',
                                'symbol': symbol,
                                'side': side,
                            }
                        return result
                    elif response.status_code in [500, 502, 503, 504]:
                        # ✅ 服务器错误，可以重试
                        error_text = response.text
                        logger.warning(f"⚠️ 下止盈单失败 (服务器错误, status={response.status_code}, attempt={attempt + 1}/{max_retries + 1}): {error_text}")
                        if attempt < max_retries:
                            wait_time = 2 ** attempt
                            logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                            time.sleep(wait_time)
                            continue
                        else:
                            error_text = response.text
                            try:
                                error_json = response.json()
                                error_msg = error_json.get('msg', error_text)
                                error_code = error_json.get('code', 'UNKNOWN')
                                raise Exception(f"APIError(code={error_code}): {error_msg}")
                            except:
                                raise Exception(f"HTTP {response.status_code}: {error_text}")
                    else:
                        # ✅ 其他HTTP错误，不重试
                        error_text = response.text
                        logger.debug(f"❌ Algo Order API 错误 (status={response.status_code}): {error_text}")
                        try:
                            error_json = response.json()
                            error_msg = error_json.get('msg', error_text)
                            error_code = error_json.get('code', 'UNKNOWN')
                            raise Exception(f"APIError(code={error_code}): {error_msg}")
                        except:
                            raise Exception(f"HTTP {response.status_code}: {error_text}")
                            
                except (Timeout, ConnectionError) as e:
                    # ✅ 网络超时或连接错误，可以重试
                    last_exception = e
                    logger.warning(f"⚠️ 下止盈单失败 (网络错误, attempt={attempt + 1}/{max_retries + 1}): {str(e)}")
                    if attempt < max_retries:
                        wait_time = 2 ** attempt
                        logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise
                except RequestException as e:
                    # ✅ 其他请求异常，可以重试
                    last_exception = e
                    logger.warning(f"⚠️ 下止盈单失败 (请求异常, attempt={attempt + 1}/{max_retries + 1}): {str(e)}")
                    if attempt < max_retries:
                        wait_time = 2 ** attempt
                        logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise
            
            # 如果所有重试都失败
            if last_exception:
                raise last_exception
        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__
            logger.debug(f"Failed to place take profit order for {symbol}: [{error_type}] {error_msg}")
            logger.debug(f"  stopPrice={price}, formatted_string={price_str if 'price_str' in locals() else 'N/A'}")
            if 'params' in locals():
                logger.debug(f"  请求参数: {params}")
            logger.exception(f"下止盈单异常详情")
            return {
                'error': True,
                'error_type': error_type,
                'error_message': error_msg,
                'symbol': symbol,
                'side': side,
            }

    def place_close_all_take_profit_order(self, symbol: str, side: str, trigger_price: float) -> Optional[dict]:
        """Place a one-way close-all TAKE_PROFIT_MARKET order via the algo order API."""
        try:
            position_mode = self.get_position_mode()
            if position_mode is True:
                raise ValueError("closePosition=true TAKE_PROFIT_MARKET is only supported by this helper in one-way mode")

            trigger_price_str = self.format_price_by_precision(trigger_price, symbol)
            if not trigger_price_str or trigger_price_str.strip() == '':
                raise ValueError(f"Formatted trigger_price is empty for {symbol} (original: {trigger_price})")

            trigger_price_validated = float(trigger_price_str)
            if trigger_price_validated <= 0:
                raise ValueError(f"Invalid trigger_price: {trigger_price_validated} (original: {trigger_price})")

            import requests
            from requests.exceptions import Timeout, ConnectionError, RequestException

            url = f"{self.base_url}/fapi/v1/algoOrder"
            params = {
                'algoType': 'CONDITIONAL',
                'symbol': symbol,
                'side': side.upper(),
                'positionSide': 'BOTH',
                'type': 'TAKE_PROFIT_MARKET',
                'triggerPrice': trigger_price_str,
                'closePosition': 'true',
                'workingType': 'CONTRACT_PRICE',
                'timeInForce': 'GTC',
                'timestamp': str(int(time.time() * 1000)),
            }

            _, request_body = self._generate_signed_request_body(params, debug=False)

            max_retries = self.MAX_RETRIES
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    response = requests.post(
                        url,
                        headers=self.default_headers,
                        data=request_body,
                        proxies=self.proxy_config,
                        timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT)
                    )

                    if response.status_code == 200:
                        result = response.json()
                        if attempt > 0:
                            logger.info(f"✅ 下全仓止盈单成功 (重试 {attempt} 次后): {symbol}")
                        if result is None:
                            return {
                                'error': True,
                                'error_type': 'NoneResult',
                                'error_message': 'close-all TAKE_PROFIT_MARKET algo order returned no JSON body',
                                'symbol': symbol,
                                'side': side,
                            }
                        return result

                    error_text = response.text
                    if response.status_code in [500, 502, 503, 504]:
                        logger.warning(f"⚠️ 下全仓止盈单失败 (服务器错误, status={response.status_code}, attempt={attempt + 1}/{max_retries + 1}): {error_text}")
                        if attempt < max_retries:
                            wait_time = 2 ** attempt
                            logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                            time.sleep(wait_time)
                            continue

                    try:
                        error_json = response.json()
                        error_msg = error_json.get('msg', error_text)
                        error_code = error_json.get('code', 'UNKNOWN')
                        raise Exception(f"APIError(code={error_code}): {error_msg}")
                    except Exception:
                        raise Exception(f"HTTP {response.status_code}: {error_text}")

                except (Timeout, ConnectionError, RequestException) as exc:
                    last_exception = exc
                    logger.warning(f"⚠️ 下全仓止盈单失败 (网络/请求异常, attempt={attempt + 1}/{max_retries + 1}): {str(exc)}")
                    if attempt < max_retries:
                        wait_time = 2 ** attempt
                        logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                    raise

            if last_exception:
                raise last_exception
        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__
            logger.debug(f"Failed to place close-all take profit order for {symbol}: [{error_type}] {error_msg}")
            if 'params' in locals():
                logger.debug(f"  请求参数: {params}")
            logger.exception("下全仓止盈单异常详情")
            return {
                'error': True,
                'error_type': error_type,
                'error_message': error_msg,
                'symbol': symbol,
                'side': side,
            }
    
    def place_take_profit_limit_order(self, symbol: str, side: str, stop_price: float, price: float, 
                                      quantity: float, position_side: str = None) -> Optional[dict]:
        """Place a take-profit limit order (基础订单限价止盈)
        
        使用普通限价订单 API 来实现止盈。
        通过反向限价挂单相同数量来达到平仓的效果。
        注意：stop_price 参数保留用于兼容性，但不会传递给API。
        
        Args:
            symbol: Trading pair symbol
            side: Order side (BUY/SELL) - 平仓方向
            stop_price: 保留用于兼容性（不使用）
            price: Limit price - 限价挂单价格
            quantity: Order quantity
            position_side: Position side (LONG/SHORT) for hedge mode
            
        Returns:
            Order result dictionary or None if failed
        """
        # 实现时间戳错误重试机制
        for attempt in range(self.MAX_TIMESTAMP_RETRIES + 1):
            try:
                # 在每次尝试前同步时间戳
                if attempt > 0:
                    logger.warning(f"⏰ 重试下限价止盈单 (attempt {attempt + 1}/{self.MAX_TIMESTAMP_RETRIES + 1}): {symbol}")
                    self.set_timestamp_offset(force=True)
                    time.sleep(0.1)
                else:
                    # 首次尝试：智能同步（仅在需要时）
                    if self._should_resync_time():
                        self.set_timestamp_offset(force=True)
                
                # Format price according to symbol's precision requirements
                price_str = self.format_price_by_precision(price, symbol)
                
                # Validate formatted string
                if not price_str or price_str.strip() == '':
                    raise ValueError(f"Formatted price is empty for {symbol} (original: {price})")
                
                # Convert to float for validation
                price_validated = float(price_str)
                
                if price_validated <= 0:
                    raise ValueError(f"Invalid price: {price_validated}")
                
                # Format quantity according to symbol's precision
                quantity_str = self.format_quantity_by_precision(quantity, symbol)
                quantity_validated = float(quantity_str)
                
                # Check position mode
                position_mode = self.get_position_mode()
                
                # 使用普通订单 API (不是 Algo Order API)
                url = f"{self.base_url}/fapi/v1/order"
                
                # Prepare order parameters - 普通限价单
                params = {
                    'symbol': symbol,
                    'side': side.upper(),
                    'type': 'LIMIT',  # 基础订单类型：LIMIT（数据库中标记为TAKE_PROFIT）
                    'quantity': quantity_str,
                    'price': price_str,  # 限价挂单价格
                    'timeInForce': 'GTC'  # Good Till Cancel
                }
                
                # Add positionSide based on position mode
                if position_mode is True:
                    # Hedge Mode: MUST add positionSide
                    if position_side:
                        params['positionSide'] = position_side
                    else:
                        # Default: opposite side for take profit
                        params['positionSide'] = 'SHORT' if side.upper() == 'BUY' else 'LONG'
                        params['reduceOnly'] = 'true'  # Add reduceOnly for hedge mode to ensure it reduces the position
                    # Hedge mode: do NOT add reduceOnly
                else:
                    # One-way Mode: use BOTH for positionSide
                    params['positionSide'] = 'BOTH'
                    # Add reduceOnly for one-way mode
                    params['reduceOnly'] = 'true'
                
                # 打印下单参数用于调试
                import json
                import requests
                logger.debug(f"📤 准备下限价止盈单: {symbol}, side={side}, quantity={quantity_str}, "
                           f"price={price_str}, position_side={params.get('positionSide', 'N/A')}")
                logger.debug(f"   📋 完整订单参数: {json.dumps(params, indent=2, default=str)}")
                
                # 使用内部方法生成签名和请求体
                signature, request_body = self._generate_signed_request_body(params, debug=False)
                
                # ✅ 使用重试机制发送请求
                from requests.exceptions import Timeout, ConnectionError, RequestException
                
                max_retries = self.MAX_RETRIES
                last_exception = None
                for req_attempt in range(max_retries + 1):
                    try:
                        # Make request using default headers and proxy config
                        response = requests.post(
                            url,
                            headers=self.default_headers,
                            data=request_body,
                            proxies=self.proxy_config,
                            timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT)
                        )
                        
                        if response.status_code == 200:
                            result = response.json()
                            if req_attempt > 0:
                                logger.info(f"✅ 下限价止盈单成功 (重试 {req_attempt} 次后): {symbol}")
                            
                            # 打印API返回结果
                            logger.debug(f"📥 限价止盈单API返回结果:")
                            if result is None:
                                logger.debug(f"   ❌ 返回结果: None")
                            elif isinstance(result, dict):
                                logger.debug(f"   ✅ 返回字典: orderId={result.get('orderId')}")
                            else:
                                logger.debug(f"   ⚠️  返回类型: {type(result)}")
                            
                            # 检查返回结果
                            if result is None:
                                error_msg = "API返回None（无订单信息）"
                                logger.debug(f"❌ {error_msg} for {symbol}")
                                return {
                                    'error': True,
                                    'error_type': 'NoneResult',
                                    'error_message': error_msg,
                                    'symbol': symbol,
                                    'side': side
                                }
                            
                            # 检查是否有orderId
                            if not result.get('orderId'):
                                error_msg = f"限价止盈单返回结果异常：缺少orderId，返回结果={result}"
                                logger.debug(f"❌ {error_msg} for {symbol}")
                                return {
                                    'error': True,
                                    'error_type': 'InvalidResult',
                                    'error_message': error_msg,
                                    'symbol': symbol,
                                    'side': side,
                                    'raw_result': str(result)
                                }
                            
                            logger.debug(f"✅ 限价止盈单下单成功: {symbol}, order_id={result.get('orderId')}, "
                                       f"price={price_str}, quantity={quantity_str}")
                            return result
                            
                        elif response.status_code in [500, 502, 503, 504]:
                            # 服务器错误，可以重试
                            error_text = response.text
                            logger.warning(f"⚠️ 下限价止盈单失败 (服务器错误, status={response.status_code}, "
                                         f"attempt={req_attempt + 1}/{max_retries + 1}): {error_text}")
                            if req_attempt < max_retries:
                                wait_time = 2 ** req_attempt
                                logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                                time.sleep(wait_time)
                                continue
                            else:
                                error_text = response.text
                                try:
                                    error_json = response.json()
                                    error_msg = error_json.get('msg', error_text)
                                    error_code = error_json.get('code', 'UNKNOWN')
                                    raise Exception(f"APIError(code={error_code}): {error_msg}")
                                except:
                                    raise Exception(f"HTTP {response.status_code}: {error_text}")
                        else:
                            # 其他HTTP错误，不重试
                            error_text = response.text
                            logger.debug(f"❌ 限价止盈单API错误 (status={response.status_code}): {error_text}")
                            try:
                                error_json = response.json()
                                error_msg = error_json.get('msg', error_text)
                                error_code = error_json.get('code', 'UNKNOWN')
                                raise Exception(f"APIError(code={error_code}): {error_msg}")
                            except:
                                raise Exception(f"HTTP {response.status_code}: {error_text}")
                                
                    except (Timeout, ConnectionError) as e:
                        # 网络超时或连接错误，可以重试
                        last_exception = e
                        logger.warning(f"⚠️ 下限价止盈单失败 (网络错误, attempt={req_attempt + 1}/{max_retries + 1}): {str(e)}")
                        if req_attempt < max_retries:
                            wait_time = 2 ** req_attempt
                            logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                            time.sleep(wait_time)
                            continue
                        else:
                            raise
                    except RequestException as e:
                        # 其他请求异常，可以重试
                        last_exception = e
                        logger.warning(f"⚠️ 下限价止盈单失败 (请求异常, attempt={req_attempt + 1}/{max_retries + 1}): {str(e)}")
                        if req_attempt < max_retries:
                            wait_time = 2 ** req_attempt
                            logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                            time.sleep(wait_time)
                            continue
                        else:
                            raise
                
                # 如果所有重试都失败
                if last_exception:
                    raise last_exception
                    
            except Exception as e:
                # 检查是否是时间戳错误，如果是且还有重试次数，则继续重试
                if self._is_timestamp_error_exception(e) and attempt < self.MAX_TIMESTAMP_RETRIES:
                    logger.warning(f"⏰ 检测到时间戳错误: {str(e)}")
                    continue
                
                # 其他错误或达到最大重试次数，记录并返回错误
                error_msg = str(e)
                error_type = type(e).__name__
                logger.debug(f"❌ Failed to place take profit limit order for {symbol}: [{error_type}] {error_msg}")
                logger.exception(f"下限价止盈单异常详情")
                
                # 返回包含错误信息的字典
                return {
                    'error': True,
                    'error_type': error_type,
                    'error_message': error_msg,
                    'symbol': symbol,
                    'side': side
                }
        
        # 如果所有重试都失败（正常不应该到这里）
        return {
            'error': True,
            'error_type': 'MaxRetriesExceeded',
            'error_message': f'达到最大重试次数 ({self.MAX_TIMESTAMP_RETRIES + 1})，下单失败',
            'symbol': symbol,
            'side': side
        }
    
    def get_algo_order(self, algo_id: Optional[int] = None, client_algo_id: Optional[str] = None, recv_window: Optional[int] = None, max_retries: int = None) -> Optional[dict]:
        """
        查询算法订单状态 (Query Algo Order)
        
        根据Binance API文档: https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Query-Algo-Order
        
        API端点: GET /fapi/v1/algoOrder
        请求权重: 1
        
        注意:
        - algoId 或 clientAlgoId 必须提供其中一个
        - 以下订单将无法找到:
          * 订单状态为 CANCELED 或 EXPIRED 且没有成交记录 且 创建时间 + 3天 < 当前时间
          * 订单创建时间 + 90天 < 当前时间
        
        Args:
            algo_id: 算法订单ID (LONG类型，自增ID，针对特定symbol)
            client_algo_id: 客户端算法订单ID (STRING类型)
            recv_window: 接收窗口时间（毫秒），可选，默认5000
            max_retries: 最大重试次数，默认使用类常量 MAX_RETRIES (2次)
            
        Returns:
            算法订单数据字典，包含以下字段:
            - algoId: 算法订单ID
            - clientAlgoId: 客户端算法订单ID
            - algoType: 算法类型 (如 'CONDITIONAL')
            - orderType: 订单类型 (如 'TAKE_PROFIT', 'STOP_MARKET')
            - symbol: 交易对符号
            - side: 方向 (BUY/SELL)
            - positionSide: 持仓方向 (BOTH/LONG/SHORT)
            - algoStatus: 算法订单状态 (如 'CANCELED', 'FILLED', 'NEW'等)
            - actualOrderId: 实际订单ID（如果已触发）
            - actualPrice: 实际成交价格
            - triggerPrice: 触发价格
            - price: 订单价格
            - quantity: 数量
            - slTriggerPrice: 止损触发价格
            - slPrice: 止损价格
            - tpTriggerPrice: 止盈触发价格
            - tpPrice: 止盈价格
            - createTime: 创建时间戳
            - updateTime: 更新时间戳
            - 以及其他字段...
            
            如果查询失败或订单不存在，返回 None
        """
        if not algo_id and not client_algo_id:
            raise ValueError("Either algo_id or client_algo_id must be provided")
        
        # ✅ 如果未指定 max_retries，使用类常量
        if max_retries is None:
            max_retries = self.MAX_RETRIES
        
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        import time
        
        # 配置重试策略（用于服务器错误）
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1,  # 重试间隔：1秒, 2秒, 4秒...
            status_forcelist=[500, 502, 503, 504],  # 服务器错误时重试
            allowed_methods=["GET"],  # 只对GET请求重试
        )
        
        # 创建带重试的会话
        session = requests.Session()
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        
        try:
            # ✅ 改进：智能时间同步检查（如果距离上次同步超过间隔，才重新同步）
            if self._should_resync_time():
                self.set_timestamp_offset(force=True)
            
            # ✅ 改进：设置默认 recvWindow（如果未指定）
            if recv_window is None:
                recv_window = self.DEFAULT_RECV_WINDOW
            
            url = f"{self.base_url}/fapi/v1/algoOrder"
            
            # 使用 SDK 的时间戳机制（已同步时间戳）
            # SDK 会自动使用 time.time() * 1000 + timestamp_offset
            import time as time_module
            timestamp = int(time_module.time() * 1000)
            if hasattr(self.client, 'timestamp_offset') and self.client.timestamp_offset:
                timestamp += self.client.timestamp_offset
            
            # Prepare parameters for signature
            params_for_signature = {'timestamp': str(timestamp)}
            if algo_id:
                params_for_signature['algoId'] = str(algo_id)
            if client_algo_id:
                params_for_signature['clientAlgoId'] = client_algo_id
            # ✅ 改进：始终设置 recvWindow（使用默认值或指定值）
            params_for_signature['recvWindow'] = str(recv_window)
            
            # Generate signature using the same method as POST requests
            # This returns (signature, request_body) where request_body includes signature
            signature, request_body = self._generate_signed_request_body(params_for_signature, debug=False)
            
            # For GET requests, use the request_body directly as query string
            # The request_body already has parameters in the correct sorted order with signature
            # This ensures the query string order matches the signature calculation
            full_url = f"{url}?{request_body}"
            
            # 手动重试机制（处理超时、连接错误和时间戳错误）
            last_exception = None
            
            # 定义请求函数（用于时间戳错误重试，需要重新生成时间戳和签名）
            def make_request():
                # 重新生成时间戳（时间已重新同步）
                import time as time_module
                timestamp = int(time_module.time() * 1000)
                if hasattr(self.client, 'timestamp_offset') and self.client.timestamp_offset:
                    timestamp += self.client.timestamp_offset
                
                # 重新准备签名参数
                params_for_signature_retry = {'timestamp': str(timestamp)}
                if algo_id:
                    params_for_signature_retry['algoId'] = str(algo_id)
                if client_algo_id:
                    params_for_signature_retry['clientAlgoId'] = client_algo_id
                params_for_signature_retry['recvWindow'] = str(recv_window)
                
                # 重新生成签名
                signature_retry, request_body_retry = self._generate_signed_request_body(params_for_signature_retry, debug=False)
                full_url_retry = f"{url}?{request_body_retry}"
                
                return session.get(
                    full_url_retry,
                    headers={'X-MBX-APIKEY': self.api_key},
                    proxies=self.proxy_config,
                    timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT)
                )
            
            for attempt in range(max_retries + 1):  # 总共 max_retries + 1 次尝试（初始1次 + 重试max_retries次）
                try:
                    # ✅ 使用统一的超时配置
                    response = make_request()
                    
                    if response.status_code == 200:
                        result = response.json()
                        return result
                    elif response.status_code in [500, 502, 503, 504]:
                        # 服务器错误，会由重试策略自动处理，但我们也手动记录
                        error_text = response.text
                        logger.debug(f"❌ 查询算法订单失败 (status={response.status_code}, attempt={attempt + 1}/{max_retries + 1}): {error_text}")
                        if attempt < max_retries:
                            wait_time = 2 ** attempt  # 指数退避：1秒, 2秒, 4秒...
                            logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                            time.sleep(wait_time)
                            continue
                        else:
                            # 所有重试都失败
                            return None
                    elif self._is_timestamp_error(response):
                        # ✅ 改进：处理时间戳错误，重新同步时间并重试
                        retry_result = self._handle_timestamp_error(response, make_request, attempt)
                        if retry_result is not None:
                            if retry_result.status_code == 200:
                                return retry_result.json()
                            # 如果重试后仍然是时间戳错误，继续外层循环
                            if self._is_timestamp_error(retry_result):
                                if attempt < max_retries:
                                    continue
                                else:
                                    error_text = retry_result.text
                                    logger.error(f"❌ 时间戳错误重试后仍失败 (status={retry_result.status_code}): {error_text}")
                                    return None
                            else:
                                error_text = retry_result.text
                                logger.debug(f"❌ 查询算法订单失败 (status={retry_result.status_code}): {error_text}")
                                return None
                        else:
                            # 重试失败，返回 None
                            return None
                    else:
                        # 其他HTTP错误（如400, 401, 404等），不重试
                        error_text = response.text
                        logger.debug(f"❌ 查询算法订单失败 (status={response.status_code}): {error_text}")
                        
                        # ✅ 检测订单不存在的错误码 -2013
                        if response.status_code == 400:
                            try:
                                error_json = response.json()
                                if error_json.get('code') == -2013 or 'Order does not exist' in error_json.get('msg', ''):
                                    # 返回特殊标记表示订单不存在（已被系统删除）
                                    return {'_order_not_found': True, 'code': -2013, 'msg': error_json.get('msg', 'Order does not exist')}
                            except:
                                pass
                        
                        return None
                        
                except (requests.exceptions.Timeout, requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout) as e:
                    last_exception = e
                    logger.debug(f"⏱️ 查询算法订单超时 (attempt={attempt + 1}/{max_retries + 1}): {e}")
                    if attempt < max_retries:
                        wait_time = 2 ** attempt  # 指数退避
                        logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.warning(f"❌ 查询算法订单超时，已重试 {max_retries + 1} 次仍失败")
                        
                except (requests.exceptions.ConnectionError, requests.exceptions.SSLError) as e:
                    last_exception = e
                    logger.debug(f"🔌 查询算法订单连接错误 (attempt={attempt + 1}/{max_retries + 1}): {e}")
                    if attempt < max_retries:
                        wait_time = 2 ** attempt  # 指数退避
                        logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.warning(f"❌ 查询算法订单连接错误，已重试 {max_retries + 1} 次仍失败")
                        
                except Exception as e:
                    # 其他异常（如签名错误等），不重试
                    logger.debug(f"❌ 查询算法订单异常: {e}")
                    logger.exception(f"查询算法订单异常详情")
                    return None
            
            # 所有重试都失败
            if last_exception:
                logger.error(f"❌ 查询算法订单最终失败，已重试 {max_retries + 1} 次: {last_exception}")
            return None
            
        except Exception as e:
            logger.debug(f"❌ 查询算法订单异常: {e}")
            logger.exception(f"查询算法订单异常详情")
            return None
    
    def get_all_algo_orders(self, symbol: Optional[str] = None) -> List[dict]:
        """Query all algo orders (open orders)
        
        Args:
            symbol: Optional symbol to filter orders
            
        Returns:
            List of algo orders
        """
        try:
            import requests
            import time
            from urllib.parse import urlencode
            
            # Use the Current All Algo Open Orders endpoint
            # Correct endpoint: /fapi/v1/openAlgoOrders (not /fapi/v1/algoOrders)
            url = f"{self.base_url}/fapi/v1/openAlgoOrders"
            
            # Get server time
            try:
                server_time = self.get_server_time()
                if server_time:
                    timestamp = int(server_time.timestamp() * 1000) + 100
                else:
                    timestamp = int(time.time() * 1000)
            except Exception:
                timestamp = int(time.time() * 1000)
            
            # Prepare parameters for signature
            params_for_signature = {'timestamp': str(timestamp), 'recvWindow': str(self.DEFAULT_RECV_WINDOW)}
            if symbol:
                params_for_signature['symbol'] = symbol
            
            # Generate signature using the same method as POST requests
            # This returns (signature, request_body) where request_body includes signature
            signature, request_body = self._generate_signed_request_body(params_for_signature, debug=False)
            
            # For GET requests, use the request_body directly as query string
            # The request_body already has parameters in the correct sorted order with signature
            # This ensures the query string order matches the signature calculation
            full_url = f"{url}?{request_body}"
            response = requests.get(
                full_url,
                headers={'X-MBX-APIKEY': self.api_key},
                proxies=self.proxy_config,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                # API returns {"total": N, "orders": [...]} or a bare list
                if isinstance(result, list):
                    return result
                if isinstance(result, dict):
                    return result.get("orders") or []
                return []
            else:
                logger.warning(f"Failed to get algo orders: {response.status_code} - {response.text}")
                return []
        except Exception as e:
            logger.warning(f"Failed to get all algo orders: {e}")
            logger.exception(f"获取算法订单异常详情")
            return []
        
    
    
    def cancel_algo_order(self, algo_id: Optional[int] = None, client_algo_id: Optional[str] = None, 
                          symbol: Optional[str] = None, max_retries: int = None) -> Optional[dict]:
        """Cancel an algo order by algoId or clientAlgoId
        
        Args:
            algo_id: Algo order ID
            client_algo_id: Client algo order ID
            symbol: Optional symbol (required by some endpoints)
            max_retries: Maximum number of retry attempts (default: use class constant MAX_RETRIES)
            
        Returns:
            Cancellation result or None if failed
        """
        if not algo_id and not client_algo_id:
            raise ValueError("Either algo_id or client_algo_id must be provided")
        
        if max_retries is None:
            max_retries = self.MAX_RETRIES
        
        import requests
        from urllib.parse import urlencode
        from requests.exceptions import Timeout, ConnectionError, RequestException
        
        url = f"{self.base_url}/fapi/v1/algoOrder"
        operation_name = f"取消算法订单 (algo_id={algo_id or client_algo_id})"
        
        last_exception = None
        for attempt in range(max_retries + 1):  # 总共 max_retries + 1 次尝试
            try:
                # Get server time
                try:
                    server_time = self.get_server_time()
                    if server_time:
                        timestamp = int(server_time.timestamp() * 1000) + 100
                    else:
                        timestamp = int(time.time() * 1000)
                except Exception:
                    timestamp = int(time.time() * 1000)
                
                # Prepare parameters for signature
                params_for_signature = {'timestamp': str(timestamp)}
                if algo_id:
                    params_for_signature['algoId'] = str(algo_id)
                if client_algo_id:
                    params_for_signature['clientAlgoId'] = client_algo_id
                if symbol:
                    params_for_signature['symbol'] = symbol
                
                # Generate signature
                signature, request_body = self._generate_signed_request_body(params_for_signature, debug=False)
                
                # For DELETE requests, use query string
                full_url = f"{url}?{request_body}"
                
                # ✅ 增加超时时间：连接超时5秒，读取超时20秒
                response = requests.delete(
                    full_url,
                    headers={'X-MBX-APIKEY': self.api_key},
                    proxies=self.proxy_config,
                    timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT)
                )
                
                if response.status_code == 200:
                    result = response.json()
                    if attempt > 0:
                        logger.info(f"✅ {operation_name}成功 (重试 {attempt} 次后)")
                    return result
                elif response.status_code in [500, 502, 503, 504]:
                    # ✅ 服务器错误，可以重试
                    error_text = response.text
                    logger.warning(f"⚠️ {operation_name}失败 (服务器错误, status={response.status_code}, attempt={attempt + 1}/{max_retries + 1}): {error_text}")
                    if attempt < max_retries:
                        wait_time = 2 ** attempt  # 指数退避：1秒, 2秒, 4秒...
                        logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                    else:
                        # 所有重试都失败
                        logger.error(f"❌ {operation_name}失败 (所有重试都失败)")
                        return None
                else:
                    # ✅ 其他HTTP错误（如400, 401, 404等），不重试
                    error_text = response.text
                    logger.warning(f"⚠️ {operation_name}失败 (HTTP错误, status={response.status_code}): {error_text}")
                    return None
                    
            except (Timeout, ConnectionError) as e:
                # ✅ 网络超时或连接错误，可以重试
                last_exception = e
                logger.warning(f"⚠️ {operation_name}失败 (网络错误, attempt={attempt + 1}/{max_retries + 1}): {str(e)}")
                if attempt < max_retries:
                    wait_time = 2 ** attempt  # 指数退避：1秒, 2秒, 4秒...
                    logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                    continue
                else:
                    # 所有重试都失败
                    logger.error(f"❌ {operation_name}失败 (网络错误, 所有重试都失败): {str(e)}")
                    return None
            except RequestException as e:
                # ✅ 其他请求异常，可以重试
                last_exception = e
                logger.warning(f"⚠️ {operation_name}失败 (请求异常, attempt={attempt + 1}/{max_retries + 1}): {str(e)}")
                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    logger.debug(f"⏳ 等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"❌ {operation_name}失败 (请求异常, 所有重试都失败): {str(e)}")
                    return None
            except Exception as e:
                # ✅ 其他异常，不重试（可能是参数错误等）
                logger.error(f"❌ {operation_name}失败 (未知错误): {str(e)}")
                return None
        
        # 如果所有重试都失败
        if last_exception:
            logger.error(f"❌ {operation_name}失败 (最终失败): {str(last_exception)}")
        return None
    
    def get_order_status(self, symbol: str, order_id: str) -> Optional[dict]:
        """Get order status from Binance (for regular orders, not algo orders)"""
        for attempt in range(self.MAX_TIMESTAMP_RETRIES + 1):
            try:
                # 在请求前同步时间戳
                if attempt > 0:
                    self.set_timestamp_offset(force=True)
                else:
                    self.set_timestamp_offset()
                
                order = self.client.futures_get_order(symbol=symbol, orderId=int(order_id))
                return order
            except Exception as e:
                # 如果是时间戳错误且还有重试次数，则重试
                if self._is_timestamp_error_exception(e) and attempt < self.MAX_TIMESTAMP_RETRIES:
                    logger.warning(f"⏰ 检测到时间戳错误，重新同步时间并重试 (attempt {attempt + 1}/{self.MAX_TIMESTAMP_RETRIES})")
                    import time
                    time.sleep(0.1)
                    continue
                # 其他错误或达到最大重试次数，返回 None
                logger.debug(f"Failed to get order status for {symbol} order {order_id}: {e}")
                return None
    
    def cancel_order(self, symbol: str, order_id: str) -> Optional[dict]:
        """Cancel a regular order (not algo order)
        
        Args:
            symbol: Trading symbol (e.g., 'BTCUSDT')
            order_id: Order ID from Binance
            
        Returns:
            Cancellation result or None if failed
        """
        for attempt in range(self.MAX_TIMESTAMP_RETRIES + 1):
            try:
                # 在请求前同步时间戳
                if attempt > 0:
                    self.set_timestamp_offset(force=True)
                else:
                    self.set_timestamp_offset(force=True)
                
                result = self.client.futures_cancel_order(symbol=symbol, orderId=int(order_id))
                return result
            except Exception as e:
                # 如果是时间戳错误且还有重试次数，则重试
                if self._is_timestamp_error_exception(e) and attempt < self.MAX_TIMESTAMP_RETRIES:
                    logger.warning(f"⏰ 检测到时间戳错误，重新同步时间并重试 (attempt {attempt + 1}/{self.MAX_TIMESTAMP_RETRIES})")
                    import time
                    time.sleep(0.1)
                    continue
                # 其他错误或达到最大重试次数
                logger.debug(f"Failed to cancel order {order_id} for {symbol}: {e}")
                return {'error': True, 'error_message': str(e)}
    
    def is_order_valid(self, symbol: str, order_id: str) -> bool:
        """Check if an order is still valid (exists and not filled/cancelled)"""
        try:
            order = self.get_order_status(symbol, order_id)
            if order:
                status = order.get('status', '').upper()
                # Valid statuses: NEW, PARTIALLY_FILLED
                # Invalid statuses: FILLED, CANCELED, EXPIRED, REJECTED, NEW_INSURANCE, NEW_ADL
                return status in ['NEW', 'PARTIALLY_FILLED']
            return False
        except Exception as e:
            logger.debug(f"Error checking order validity for {symbol} order {order_id}: {e}")
            return False
    
    def get_position(self, symbol: str) -> Optional[dict]:
        """Get current position for a symbol"""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            if positions and len(positions) > 0:
                position = positions[0]
                position_amt = float(position.get('positionAmt', 0))
                if abs(position_amt) > 0:  # Has position
                    return {
                        'symbol': symbol,
                        'position_amt': position_amt,
                        'entry_price': float(position.get('entryPrice', 0)),
                        'side': 'LONG' if position_amt > 0 else 'SHORT'
                    }
            return None
        except Exception as e:
            logger.debug(f"Failed to get position for {symbol}: {e}")
            return None
    
    # Class-level flag: once we get a 404 for algoOrder, stop calling it
    _algo_order_unavailable: bool = False

    def get_open_algo_orders(self, symbol: str | None = None) -> List[dict]:
        """Get open conditional (algo) orders from /fapi/v1/algoOrder/openOrders."""
        if BinanceClient._algo_order_unavailable:
            return []
        try:
            import requests as _req
            params: dict[str, object] = {}
            if symbol:
                params["symbol"] = symbol
            _, query = self._generate_signed_request_body(params, debug=False)
            url = f"{self.base_url}/fapi/v1/algoOrder/openOrders"
            resp = _req.get(
                f"{url}?{query}",
                headers={"X-MBX-APIKEY": self.api_key},
                proxies=self.proxy_config,
                timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT),
            )
            if resp.status_code == 404:
                BinanceClient._algo_order_unavailable = True
                logger.info("Algo Order API not available for this account (404), will skip future calls")
                return []
            resp.raise_for_status()
            data = resp.json()
            # Response: {"total": N, "orders": [...]}
            return data.get("orders") or []
        except Exception as e:
            logger.debug(f"Failed to get open algo orders: {e}")
            return []

    def get_open_orders(self, symbol: str) -> List[dict]:
        """Get all open orders for a symbol
        
        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')
            
        Returns:
            List of open orders, each containing:
            - orderId: Order ID
            - symbol: Trading pair
            - side: BUY or SELL
            - positionSide: LONG or SHORT (for hedge mode)
            - type: Order type (LIMIT, MARKET, etc.)
            - status: Order status (NEW, PARTIALLY_FILLED, etc.)
        """
        try:
            orders = self.client.futures_get_open_orders(symbol=symbol)
            return orders if orders else []
        except Exception as e:
            logger.debug(f"Failed to get open orders for {symbol}: {e}")
            return []

    def get_order_history(self, symbol: str, limit: int = 100) -> List[dict]:
        """Get historical orders enriched with actual commission (fee) per order.

        Fetches orders and account trades in two calls, then merges commission by orderId.
        Each returned dict gains a 'fee' key (float, USDT or native asset value).
        """
        try:
            orders = self.client.futures_get_all_orders(symbol=symbol, limit=limit)
            orders = orders if orders else []
        except Exception as e:
            logger.debug(f"Failed to get order history for {symbol}: {e}")
            return []

        # Build commission index: orderId → total commission
        try:
            self.set_timestamp_offset()
            trades = self.client.futures_account_trades(symbol=symbol, limit=limit) or []
            fee_by_order: dict[int, float] = {}
            for tr in trades:
                oid = tr.get("orderId")
                if oid is not None:
                    fee_by_order[oid] = fee_by_order.get(oid, 0.0) + float(tr.get("commission", 0) or 0)
        except Exception as e:
            logger.debug(f"Failed to get trade fills for fee enrichment ({symbol}): {e}")
            fee_by_order = {}

        for o in orders:
            oid = o.get("orderId")
            o["fee"] = fee_by_order.get(oid, 0.0)

        return orders

    def get_positions(self, symbol: str | None = None) -> List[dict]:
        """Get current open positions. Filters out zero-quantity entries.

        Prefer /fapi/v2/positionRisk because it includes leverage and marginType.
        """
        try:
            import requests

            # Query v2 positionRisk to get leverage + marginType fields.
            url = f"{self.base_url}/fapi/v2/positionRisk"
            params: dict[str, str] = {}
            if symbol:
                params["symbol"] = symbol
            _, query = self._generate_signed_request_body(params, debug=False)

            response = requests.get(
                f"{url}?{query}",
                headers={'X-MBX-APIKEY': self.api_key},
                proxies=self.proxy_config,
                timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT),
            )
            response.raise_for_status()
            rows = response.json() or []

            filtered: List[dict] = []
            for r in rows:
                if float(r.get("positionAmt", 0) or 0) == 0:
                    continue
                row = dict(r)
                # UI uses lower-camel key; keep both to avoid regressions.
                if "unrealizedProfit" not in row and "unRealizedProfit" in row:
                    row["unrealizedProfit"] = row.get("unRealizedProfit")
                # Expose openTime field for UI. Binance positionRisk provides updateTime.
                row["openTime"] = row.get("updateTime", 0)
                filtered.append(row)
            return filtered
        except Exception as e:
            logger.debug(f"Failed to get positions from v2 positionRisk: {e}")
            # Fallback to SDK response if v2 query fails.
            try:
                if symbol:
                    rows = self.client.futures_position_information(symbol=symbol)
                else:
                    rows = self.client.futures_position_information()
                filtered = []
                for r in (rows or []):
                    if float(r.get("positionAmt", 0) or 0) == 0:
                        continue
                    row = dict(r)
                    if "unrealizedProfit" not in row and "unRealizedProfit" in row:
                        row["unrealizedProfit"] = row.get("unRealizedProfit")
                    row["openTime"] = row.get("updateTime", 0)
                    filtered.append(row)
                return filtered
            except Exception as fallback_e:
                logger.debug(f"Failed to get positions (fallback): {fallback_e}")
                return []

    def get_trade_fills(self, symbol: str, order_id: str) -> List[dict]:
        """Return per-fill records for a completed order, including commission.

        Uses GET /fapi/v1/userTrades (futures_account_trades) filtered by orderId.
        Each returned dict contains: price, qty, commission, commissionAsset, realizedPnl.
        Returns [] on error.
        """
        try:
            self.set_timestamp_offset()
            trades = self.client.futures_account_trades(
                symbol=symbol, orderId=int(order_id)
            )
            return trades if trades else []
        except Exception as e:
            logger.debug(f"Failed to get trade fills for {symbol} orderId={order_id}: {e}")
            return []

    def get_income_history(self, symbol: str | None = None, limit: int = 50) -> List[dict]:
        """Get closed-position records with entry_price, exit_price, size, fee.

        Uses futures_account_trades filtered to closing trades (realizedPnl != 0).
        """
        try:
            kwargs: dict = {"limit": limit}
            if symbol:
                kwargs["symbol"] = symbol
            trades = self.client.futures_account_trades(**kwargs)
            result = []
            for t in (trades or []):
                rpnl = float(t.get("realizedPnl", 0) or 0)
                if rpnl == 0.0:
                    continue
                qty = float(t.get("qty", 0) or 0)
                exit_price = float(t.get("price", 0) or 0)
                # entry_price derived from realizedPnl:
                #   SELL (close LONG): pnl = (exit - entry) * qty
                #   BUY  (close SHORT): pnl = (entry - exit) * qty
                if qty != 0:
                    side = t.get("side", "SELL")
                    entry_price = exit_price - rpnl / qty if side == "SELL" else exit_price + rpnl / qty
                else:
                    entry_price = 0.0
                commission = float(t.get("commission", 0) or 0)
                commission_asset = t.get("commissionAsset", "USDT")
                result.append({
                    "symbol": t.get("symbol", ""),
                    "income": rpnl,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "size": qty,
                    "fee": commission,
                    "fee_asset": commission_asset,
                    "time": t.get("time", 0),
                })
            return result
        except Exception as e:
            logger.debug(f"Failed to get income history: {e}")
            return []

    def close_position(self, symbol: str, quantity: float, current_side: str) -> Optional[dict]:
        """Close current position using market order
        
        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')
            quantity: Quantity to close
            current_side: Position side to close ('LONG' or 'SHORT'). 
            
        Returns:
            Order result dict if successful, None otherwise
        """
        try:
            
            if quantity == 0:
                logger.debug(f"No position to close for {symbol}")
                return None            
            
            # For closing: LONG position needs SELL, SHORT position needs BUY
            order_side = 'SELL' if current_side == 'LONG' else 'BUY'
            
            # Check position mode to determine which parameters to use
            # 默认使用对冲模式（双向持仓），因为 Binance 账户设置的就是双向持仓模式
            # 即使 get_position_mode() 因时间戳等错误失败，也不会错误地以单向模式平仓（导致开反向仓）
            position_mode = self.get_position_mode()
            if position_mode is None:
                logger.warning(f"⚠️ get_position_mode() 失败，默认使用对冲模式（双向持仓）: symbol={symbol}, side={current_side}")
                position_mode = True  # 默认对冲模式
            
            # Use market order to close position
            if position_mode is True:
                # Hedge Mode: Use position_side, do NOT use reduceOnly
                return self.place_market_order(
                    symbol=symbol,
                    side=order_side,
                    quantity=quantity,
                    position_side=current_side,  # Required for hedge mode
                    reduce_only=False  # Not supported in hedge mode
                )
            else:
                # One-way Mode: Use reduceOnly, do NOT use position_side
                return self.place_market_order(
                    symbol=symbol,
                    side=order_side,
                    quantity=quantity,
                    position_side=None,  # Not needed in one-way mode
                    reduce_only=True  # Required for one-way mode to close position
                )
        except Exception as e:
            logger.debug(f"Failed to close position for {symbol}: {e}")
            logger.exception(f"平仓异常详情")
            return None
    
    def get_account_margin_ratio(self) -> Optional[float]:
        """Calculate Account Margin Ratio = Account Maintenance Margin / Account Equity"""
        try:
            account = self.client.futures_account(recvWindow=5000)
            
            # Get positions to calculate total maintenance margin
            positions = self.client.futures_position_information(recvWindow=5000)
            total_maint_margin = 0.0
            for pos in positions:
                maint_margin = float(pos.get('maintMargin', 0) or 0)
                total_maint_margin += maint_margin
            
            # Total equity = total wallet balance
            total_wallet = float(account.get('totalWalletBalance', 0))
            equity = total_wallet
            
            if equity > 0:
                margin_ratio = total_maint_margin / equity
                return margin_ratio
            return None
        except Exception as e:
            logger.debug(f"Failed to calculate account margin ratio: {e}")
            return None
    
    def estimate_margin_after_order(self, symbol: str, quantity: float, price: float, leverage: int) -> Optional[float]:
        """Estimate maintenance margin for a new order"""
        try:
            # Get current account margin ratio
            current_margin_ratio = self.get_account_margin_ratio()
            if current_margin_ratio is None:
                return None
            
            # Get current total maintenance margin
            account = self.client.futures_account()
            positions = self.client.futures_position_information()
            current_total_maint_margin = 0.0
            for pos in positions:
                maint_margin = float(pos.get('maintMargin', 0) or 0)
                current_total_maint_margin += maint_margin
            
            # Estimate new position's maintenance margin
            # Binance maintenance margin rate varies by symbol and tier (usually 0.4% - 2.5%)
            # For simplicity, we'll use a conservative estimate of 1%
            notional_value = quantity * price
            estimated_maint_margin_rate = 0.01  # 1% conservative estimate
            new_position_maint_margin = notional_value * estimated_maint_margin_rate
            
            # Total maintenance margin after order
            total_maint_margin_after = current_total_maint_margin + new_position_maint_margin
            
            # Equity should remain roughly the same (might slightly change with fees)
            equity = float(account.get('totalWalletBalance', 0))
            
            if equity > 0:
                new_margin_ratio = total_maint_margin_after / equity
                return new_margin_ratio
            return None
        except Exception as e:
            logger.debug(f"Failed to estimate margin ratio after order: {e}")
            return None
    
    def get_user_trades(self, symbol: str = None, start_time: Optional[datetime] = None, 
                       end_time: Optional[datetime] = None, limit: int = 1000) -> List[dict]:
        """Get user's trade history from Binance Futures API
        API: GET /fapi/v1/userTrades
        
        Args:
            symbol: Trading pair symbol (optional, if None returns all symbols)
            start_time: Start time (optional)
            end_time: End time (optional)
            limit: Maximum number of trades to return (default 1000, max 1000)
        
        Returns:
            List of trade dictionaries
        """
        try:
            params = {}
            if symbol:
                params['symbol'] = symbol
            if start_time:
                params['startTime'] = int(start_time.timestamp() * 1000)
            if end_time:
                params['endTime'] = int(end_time.timestamp() * 1000)
            if limit:
                params['limit'] = min(limit, 1000)  # Max 1000 per request
            
            trades = self.client.futures_account_trades(**params)
            return trades
        except Exception as e:
            logger.debug(f"Failed to get user trades: {e}")
            return []
    
    def get_income_history(self, symbol: str = None, income_type: str = 'REALIZED_PNL',
                          start_time: Optional[datetime] = None, 
                          end_time: Optional[datetime] = None, limit: int = 1000) -> List[dict]:
        """Get income history from Binance Futures API
        API: GET /fapi/v1/income
        
        This can be used to get realized PNL for closed positions.
        
        Args:
            symbol: Trading pair symbol (optional)
            income_type: Type of income (REALIZED_PNL, COMMISSION, etc.)
            start_time: Start time (optional)
            end_time: End time (optional)
            limit: Maximum number of records (default 1000, max 1000)
        
        Returns:
            List of income dictionaries
        """
        try:
            params = {}
            if symbol:
                params['symbol'] = symbol
            if income_type:
                params['incomeType'] = income_type
            if start_time:
                params['startTime'] = int(start_time.timestamp() * 1000)
            if end_time:
                params['endTime'] = int(end_time.timestamp() * 1000)
            if limit:
                params['limit'] = min(limit, 1000)
            
            income = self.client.futures_income_history(**params)
            return income
        except Exception as e:
            logger.debug(f"Failed to get income history: {e}")
            return []
    
    def get_funding_rate(self, symbol: str) -> Optional[float]:
        """Get current funding rate for a symbol
        API: GET /fapi/v1/premiumIndex
        
        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')
        
        Returns:
            Funding rate as a decimal (e.g., 0.0001 for 0.01%)
            Returns None if failed to get funding rate
        """
        try:
            # Use direct API call since python-binance SDK doesn't have futures_premium_index method
            data = self._request('GET', '/fapi/v1/premiumIndex', {'symbol': symbol})
            funding_rate = float(data.get('lastFundingRate', 0))
            return funding_rate
        except Exception as e:
            logger.debug(f"Failed to get funding rate for {symbol}: {e}")
            return None
    
    def get_funding_rate_info(self, symbol: str) -> Optional[dict]:
        """Get funding rate information for a symbol including next funding time
        API: GET /fapi/v1/premiumIndex
        
        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')
        
        Returns:
            Dictionary with funding rate information:
            {
                'fundingRate': float,  # Current funding rate
                'nextFundingTime': int,  # Next funding time in milliseconds
                'markPrice': float,  # Mark price
                'indexPrice': float  # Index price
            }
            Returns None if failed to get funding rate info
        """
        try:
            # Use direct API call since python-binance SDK doesn't have futures_premium_index method
            data = self._request('GET', '/fapi/v1/premiumIndex', {'symbol': symbol})
            return {
                'fundingRate': float(data.get('lastFundingRate', 0)),
                'nextFundingTime': int(data.get('nextFundingTime', 0)),
                'markPrice': float(data.get('markPrice', 0)),
                'indexPrice': float(data.get('indexPrice', 0))
            }
        except Exception as e:
            logger.debug(f"Failed to get funding rate info for {symbol}: {e}")
            return None
    
    def start_user_data_stream(self) -> Optional[str]:
        """
        启动User Data Stream并获取listenKey
        
        Returns:
            str: listenKey，如果失败返回None
        """
        try:
            import requests
            url = f"{self.base_url}/fapi/v1/listenKey"
            
            response = requests.post(
                url,
                headers=self.default_headers,
                proxies=self.proxy_config,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                listen_key = result.get('listenKey')
                logger.debug(f"✅ User Data Stream listenKey获取成功: {listen_key[:20] if listen_key else 'N/A'}...")
                return listen_key
            else:
                logger.debug(f"❌ 获取listenKey失败: {response.status_code}, {response.text}")
                return None
        except Exception as e:
            logger.debug(f"❌ 获取listenKey异常: {str(e)}")
            logger.exception(f"获取listenKey异常详情")
            return None
    
    def keepalive_user_data_stream(self, listen_key: str) -> bool:
        """
        保持User Data Stream活跃
        
        Args:
            listen_key: listenKey
            
        Returns:
            bool: 是否成功
        """
        try:
            import requests
            import time
            url = f"{self.base_url}/fapi/v1/listenKey"
            
            # Get server time
            try:
                server_time = self.get_server_time()
                if server_time:
                    timestamp = int(server_time.timestamp() * 1000) + 100
                else:
                    timestamp = int(time.time() * 1000)
            except Exception:
                timestamp = int(time.time() * 1000)
            
            # Prepare parameters for signature
            params = {
                'listenKey': listen_key,
                'timestamp': str(timestamp)
            }
            
            # Generate signature
            signature, request_body = self._generate_signed_request_body(params, debug=False)
            
            response = requests.put(
                url,
                headers=self.default_headers,
                data=request_body,
                proxies=self.proxy_config,
                timeout=10
            )
            
            if response.status_code == 200:
                logger.debug(f"✅ User Data Stream keepalive成功")
                return True
            else:
                logger.debug(f"❌ keepalive失败: {response.status_code}, {response.text}")
                return False
        except Exception as e:
            logger.debug(f"❌ keepalive异常: {str(e)}")
            logger.exception(f"keepalive异常详情")
            return False
    
    def close_user_data_stream(self, listen_key: str) -> bool:
        """
        关闭User Data Stream
        
        Args:
            listen_key: listenKey
            
        Returns:
            bool: 是否成功
        """
        try:
            import requests
            import time
            url = f"{self.base_url}/fapi/v1/listenKey"
            
            # Get server time
            try:
                server_time = self.get_server_time()
                if server_time:
                    timestamp = int(server_time.timestamp() * 1000) + 100
                else:
                    timestamp = int(time.time() * 1000)
            except Exception:
                timestamp = int(time.time() * 1000)
            
            # Prepare parameters for signature
            params = {
                'listenKey': listen_key,
                'timestamp': str(timestamp)
            }
            
            # Generate signature
            signature, request_body = self._generate_signed_request_body(params, debug=False)
            
            # For DELETE requests, use query string
            full_url = f"{url}?{request_body}"
            response = requests.delete(
                full_url,
                headers={'X-MBX-APIKEY': self.api_key},
                proxies=self.proxy_config,
                timeout=10
            )
            
            if response.status_code == 200:
                logger.debug(f"✅ User Data Stream关闭成功")
                return True
            else:
                logger.debug(f"❌ 关闭失败: {response.status_code}, {response.text}")
                return False
        except Exception as e:
            logger.debug(f"❌ 关闭异常: {str(e)}")
            logger.exception(f"关闭异常详情")
            return False

