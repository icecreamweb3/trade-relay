#!/usr/bin/env python3
"""
订单监控管理器

功能：
1. 订阅Binance User Data Stream监听订单成交消息
2. 维护订单监控列表（包含订单ID和止盈止损订单预提交数据）
3. 当订单成交时，自动取消旧的止盈止损订单并重新提交
"""

import json
import time
import threading
import logging
import websocket
import ssl
import platform
import copy
from typing import Dict, Optional, Callable
from datetime import datetime
from urllib.parse import urlencode


logger = logging.getLogger(__name__)


def get_default_proxy_port() -> int:
    """
    根据操作系统获取默认代理端口
    
    Returns:
        int: 默认代理端口
        - macOS: 1087
        - Linux/Ubuntu: 10809
        - 其他系统: 10809 (默认)
    """
    system = platform.system().lower()
    if system == 'darwin':  # macOS
        return 1087
    elif system == 'linux':  # Linux/Ubuntu
        return 10809
    else:
        # 其他系统默认使用Linux端口
        return 10809


def get_proxy_config() -> tuple:
    """
    获取代理配置
    
    Returns:
        tuple: (use_proxy: bool, proxy_host: str, proxy_port: int)
        - 如果 PROXY 环境变量存在且有效，返回 (True, host, port)
        - 否则返回 (False, None, None)
    """
    import os
    from trade_relay.env_loader import load_env
    load_env()
    
    # 从环境变量读取 PROXY 配置
    proxy = os.getenv('PROXY', '').strip()
    
    if not proxy:
        return False, None, None
    
    # 解析代理配置，格式如：http://127.0.0.1:10809
    try:
        # 移除协议前缀
        if '://' in proxy:
            proxy = proxy.split('://', 1)[1]
        
        # 分离主机和端口
        if ':' in proxy:
            host, port_str = proxy.rsplit(':', 1)
            port = int(port_str)
            return True, host, port
        else:
            # 只有主机没有端口，使用默认端口
            return True, proxy, get_default_proxy_port()
    except Exception as e:
        logger.warning(f"⚠️ 解析PROXY配置失败: {e}, 将不使用代理")
        return False, None, None


class OrdersMonitor:
    """
    订单监控管理器
    
    功能：
    1. 订阅Binance User Data Stream监听订单成交消息
    2. 维护订单监控列表（包含订单ID和止盈止损订单预提交数据）
    3. 当订单成交时，自动取消旧的止盈止损订单并重新提交
    """
    
    # Binance Futures User Data Stream WebSocket URL
    WS_USER_DATA_STREAM_URL = "wss://fstream.binance.com/private/ws"
    PRIVATE_WS_EVENTS = ('ORDER_TRADE_UPDATE', 'ACCOUNT_UPDATE', 'ALGO_UPDATE', 'listenKeyExpired')
    
    def __init__(self, binance_client, on_order_filled_callback: Optional[Callable] = None, live_trading_manager=None):
        """
        初始化订单监控管理器
        
        Args:
            binance_client: BinanceClient实例
            on_order_filled_callback: 订单成交回调函数，接收 (order_data) 参数
            live_trading_manager: LiveTradingManager实例，用于创建或更新持仓记录
        """
        self.binance_client = binance_client
        self.on_order_filled_callback = on_order_filled_callback
        self.live_trading_manager = live_trading_manager  # ✅ 添加 live_trading_manager 引用
        
        # 订单监控列表：{order_id: {订单信息, 止盈止损预提交数据}}
        self.order_monitor_list: Dict[str, dict] = {}
        self.lock = threading.Lock()
        
        # ✅ 新增：订单状态缓存，用于存储从WebSocket接收到的订单最新状态
        # 解决Market订单中发现的一种Binance的bug，Market订单通过websocket接收到订单filled状态，但是通过订单号查询不到订单状态，
        # 因此先缓存所有的订单最新的状态变化信息，在添加到监控列表前，先查看缓存，如果缓存中没有该信息，在查询订单接口，如果不是filled状态，则不处理，
        # 继续等待websocket消息推送，如果是filled状态，则立即处理。如果是部分成交，则继续等待websocket消息推送，直到完全成交。
        # 格式：{order_id: {order_data, timestamp, order_status, executed_qty, avg_price, order_type}}
        self.order_status_cache: Dict[str, dict] = {}
        
        # ✅ 新增：止盈止损订单监控列表
        # 用于监控已创建的止盈止损订单状态，通过WebSocket接收ALGO_UPDATE事件更新状态
        # 格式：{algo_order_id: {order_id, order_type, symbol, strategy_id, signal_order_id, status, ...}}
        # order_type: 'STOP_LOSS' 或 'TAKE_PROFIT'
        # 最终状态（FINISHED, EXPIRED, CANCELED）时从列表中删除
        self.sl_tp_order_monitor_list: Dict[str, dict] = {}
        
        # WebSocket相关
        self.ws = None
        self.ws_thread = None
        self.running = False
        self.listen_key = None
        self.keepalive_thread = None
        self.health_check_thread = None  # ✅ 添加健康检查线程
        self.keepalive_interval = 30 * 60  # 30分钟（Binance要求60分钟内至少keepalive一次）
        
        # 代理配置（从环境变量PROXY读取）
        self.use_proxy, self.proxy_host, self.proxy_port = get_proxy_config()
        
        if self.use_proxy:
            logger.info(f"🌐 WebSocket将使用代理: {self.proxy_host}:{self.proxy_port}")
        else:
            logger.info("🔗 WebSocket将直接连接（未配置PROXY或配置无效）")
        
        # 重连相关
        self.reconnect_interval = 5
        self.reconnect_count = 0
        self.max_reconnect_attempts = 10
        self.reconnecting = False
        self.last_pong_time = None
        # ✅ 连接健康检查：如果超过此时间（秒）没有收到任何websocket消息，触发重连
        self.connection_timeout = 5 * 60  # 5分钟（保守设置，如果5分钟没收到任何消息就认为连接有问题）
        # WebSocket PING/PONG 配置（用于快速检测连接状态）
        self.ping_interval = 20  # 每20秒发送一次PING帧
        self.ping_timeout = 10  # PONG响应超时10秒
    
    def _get_db_manager(self):
        """
        获取数据库管理器（通过 live_trading_manager）
        
        Returns:
            DatabaseManager实例，如果不可用则返回None
        """
        if not self.live_trading_manager:
            return None
        
        # 优先使用 _get_db_manager 方法（适配器模式）
        if hasattr(self.live_trading_manager, '_get_db_manager'):
            return self.live_trading_manager._get_db_manager()
        
        # 向后兼容：通过 main_window 获取
        if hasattr(self.live_trading_manager, 'main_window'):
            main_window = self.live_trading_manager.main_window
            if main_window and hasattr(main_window, 'db_manager'):
                return main_window.db_manager
        
        return None
    
    def add_order_to_monitor(self, order_id: str, limit_order_info: dict, sl_tp_data: dict):
        """
        添加订单到监控列表
        
        Args:
            order_id: 订单ID
            limit_order_info: 订单信息（包含symbol, side, quantity, price等）
            sl_tp_data: 止盈止损订单预提交数据
                {
                    'stop_loss': {
                        'price_param': float,
                        'price_type': str,  # '差值' or '百分比'
                        'side': str,  # 'BUY' or 'SELL'
                        'position_side': str  # 'LONG' or 'SHORT'
                    },
                    'take_profit': {
                        'price_param': float,
                        'price_type': str,
                        'side': str,
                        'position_side': str
                    },
                    'strategy_id': int,
                    'strategy_name': str,
                    'signal_type': str,  # 'LONG' or 'SHORT'
                    'signal_kline_index': int
                }
        """
        # ✅ 先检查缓存中是否有该订单的最新状态
        cached_status = None
        with self.lock:
            if order_id in self.order_status_cache:
                cached_status = self.order_status_cache[order_id]
                logger.info(f"🔍 在缓存中找到订单状态: order_id={order_id}, status={cached_status.get('order_status')}, executed_qty={cached_status.get('executed_qty')}")
        
        # ✅ 如果缓存中有已成交的状态，立即处理
        if cached_status:
            cached_order_status = cached_status.get('order_status', '')
            cached_executed_qty = cached_status.get('executed_qty', 0)
            cached_avg_price = cached_status.get('avg_price', 0)
            cached_order_type = cached_status.get('order_type', 'MARKET')
            cached_order_data = cached_status.get('order_data', {})
            
            # ✅ 如果订单已成交（完全成交或部分成交），立即处理
            if (cached_order_status == 'FILLED' or cached_order_status == 'PARTIALLY_FILLED') and cached_executed_qty > 0:
                status_text = '完全成交' if cached_order_status == 'FILLED' else '部分成交'
                logger.warning(f"⚠️ 订单在添加到监控列表前已通过WebSocket{status_text}，使用缓存状态立即处理: order_id={order_id}, executed_qty={cached_executed_qty}, avg_price={cached_avg_price}")
                
                # 先添加到监控列表
                with self.lock:
                    self.order_monitor_list[order_id] = {
                        'limit_order_info': limit_order_info,
                        'sl_tp_data': sl_tp_data,
                        'stop_loss_order_id': None,
                        'take_profit_order_id': None,
                        'added_at': datetime.now(),
                        'processed': False,
                        'last_processed_qty': 0.0
                    }
                    monitor_entry = self.order_monitor_list[order_id]
                    
                    # 更新已处理的成交数量
                    monitor_entry['last_processed_qty'] = cached_executed_qty
                    
                    # ✅ 只有完全成交时才标记为已处理
                    if cached_order_status == 'FILLED':
                        monitor_entry['processed'] = True
                    
                    # ✅ 在锁内使用深拷贝，避免竞争条件
                    monitor_data = copy.deepcopy(monitor_entry)
                
                # 立即处理订单成交（完全成交或部分成交）
                self._process_order_filled(
                    order_id=order_id,
                    order_data=cached_order_data,
                    executed_qty=cached_executed_qty,
                    avg_price=cached_avg_price,
                    order_type=cached_order_type,
                    monitor_data=monitor_data
                )
                
                # ✅ 只有完全成交时才从缓存中移除，部分成交保留缓存以便后续继续处理
                if cached_order_status == 'FILLED':
                    with self.lock:
                        if order_id in self.order_status_cache:
                            del self.order_status_cache[order_id]
                
                return
        
        # 如果缓存中没有已成交状态，正常添加到监控列表
        with self.lock:
            self.order_monitor_list[order_id] = {
                'limit_order_info': limit_order_info,
                'sl_tp_data': sl_tp_data,
                'stop_loss_order_id': None,  # 已提交的止损订单ID
                'take_profit_order_id': None,  # 已提交的止盈订单ID
                'added_at': datetime.now(),
                'processed': False,  # 标记订单是否已完全处理（FILLED状态）
                'last_processed_qty': 0.0  # ✅ 已处理的成交数量（用于跟踪多次成交）
            }
            logger.info(f"📋 添加订单到监控列表: order_id={order_id}, symbol={limit_order_info.get('symbol')}")
        
        # ✅ 修复竞态条件：添加订单后立即检查订单状态
        # 如果订单在添加到监控列表之前就已经FILLED，立即处理
        try:
            symbol = limit_order_info.get('symbol', '')
            if symbol and self.binance_client:
                order_status = self.binance_client.get_order_status(symbol, order_id)
                if order_status:
                    status = order_status.get('status', '').upper()
                    executed_qty = float(order_status.get('executedQty', 0))
                    avg_price = float(order_status.get('avgPrice', 0)) if order_status.get('avgPrice') else 0
                    order_type = order_status.get('type', 'LIMIT')  # 从order_status中获取订单类型
                    
                    # 如果订单已经FILLED，立即处理
                    if status == 'FILLED' and executed_qty > 0:
                        logger.warning(f"⚠️ 订单在添加到监控列表前已FILLED，立即处理: order_id={order_id}, executed_qty={executed_qty}, avg_price={avg_price}")
                        
                        # 构造 order_data 结构（模拟 WebSocket 消息格式）
                        order_data = {
                            'i': int(order_id),
                            'X': 'FILLED',
                            'x': 'TRADE',
                            'z': str(executed_qty),
                            'ap': str(avg_price) if avg_price > 0 else '0',
                            'o': order_type
                        }
                        
                        # 获取监控数据并更新已处理数量
                        with self.lock:
                            if order_id in self.order_monitor_list:
                                monitor_entry = self.order_monitor_list[order_id]
                                # ✅ 检查是否已经处理过（FILLED状态且已处理过）
                                if monitor_entry.get('processed', False) and status == 'FILLED':
                                    logger.info(f"ℹ️ 订单已完全成交并处理过，跳过: order_id={order_id}")
                                    return
                                
                                # ✅ 更新已处理的成交数量
                                last_processed_qty = monitor_entry.get('last_processed_qty', 0.0)
                                new_executed_qty = executed_qty - last_processed_qty
                                
                                if new_executed_qty <= 0:
                                    logger.debug(f"ℹ️ 没有新增成交数量，跳过处理: order_id={order_id}, executed_qty={executed_qty}, last_processed_qty={last_processed_qty}")
                                    return
                                
                                # 更新已处理的成交数量
                                monitor_entry['last_processed_qty'] = executed_qty
                                
                                # 如果订单完全成交，标记为已处理
                                if status == 'FILLED':
                                    monitor_entry['processed'] = True
                                
                                # ✅ 在锁内使用深拷贝，避免竞争条件
                                monitor_data = copy.deepcopy(monitor_entry)
                            else:
                                logger.error(f"❌ 订单不在监控列表中: order_id={order_id}")
                                return
                        
                        # 立即处理FILLED订单
                        order_type = order_data.get('o', 'LIMIT')  # 从order_data中获取订单类型
                        self._process_order_filled(
                            order_id=order_id,
                            order_data=order_data,
                            executed_qty=executed_qty,
                            avg_price=avg_price,
                            order_type=order_type,
                            monitor_data=monitor_data
                        )
                else:
                    # ✅ 查询失败，但缓存中可能有最新状态
                    logger.debug(f"⚠️ 无法查询订单状态: order_id={order_id}，等待WebSocket消息或使用缓存状态")
        except Exception as e:
            logger.error(f"❌ 检查订单状态时出错: order_id={order_id}, error={e}")
            import traceback
            traceback.print_exc()
    
    def remove_limit_order(self, order_id: str):
        """从监控列表移除订单"""
        with self.lock:
            if order_id in self.order_monitor_list:
                del self.order_monitor_list[order_id]
                logger.info(f"📋 从监控列表移除订单: order_id={order_id}")
    
    def start(self):
        """启动WebSocket连接"""
        if self.running:
            logger.warning("⚠️  订单监控已在运行中")
            return
        
        # 获取listenKey
        self.listen_key = self.binance_client.start_user_data_stream()
        if not self.listen_key:
            logger.error("❌ 无法获取User Data Stream listenKey")
            return
        
        logger.info(f"✅ 获取listenKey成功: {self.listen_key[:20]}...")
        
        # 构建WebSocket URL并保存
        self.user_data_stream_url = f"{self.WS_USER_DATA_STREAM_URL}?{urlencode({'listenKey': self.listen_key, 'events': '/'.join(self.PRIVATE_WS_EVENTS)})}"
        
        logger.info(f"🔗 连接User Data Stream WebSocket: {self.user_data_stream_url[:50]}...")
        
        # 创建WebSocket连接
        self.ws = websocket.WebSocketApp(
            self.user_data_stream_url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open
        )
        
        # 启动WebSocket线程
        self.running = True
        self.reconnecting = False
        self.reconnect_count = 0
        self.ws_thread = threading.Thread(target=self._run_websocket, daemon=True)
        self.ws_thread.start()
        
        # 启动keepalive线程
        self.keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self.keepalive_thread.start()
        
        # ✅ 启动连接健康检查线程
        self.health_check_thread = threading.Thread(target=self._health_check_loop, daemon=True)
        self.health_check_thread.start()
        
        logger.info("✅ 订单监控WebSocket已启动")
    
    def stop(self):
        """停止WebSocket连接"""
        logger.info("🛑 停止订单监控...")
        self.running = False
        
        # 关闭WebSocket
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
            self.ws = None
        
        # 关闭User Data Stream
        if self.listen_key:
            try:
                self.binance_client.close_user_data_stream(self.listen_key)
            except:
                pass
            self.listen_key = None
        
        # 等待线程结束
        if self.ws_thread:
            self.ws_thread.join(timeout=2)
            self.ws_thread = None
        
        if self.keepalive_thread:
            self.keepalive_thread.join(timeout=2)
            self.keepalive_thread = None
        
        if hasattr(self, 'health_check_thread') and self.health_check_thread:
            self.health_check_thread.join(timeout=2)
            self.health_check_thread = None
        
        logger.info("✅ 订单监控已停止")
    
    def _on_message(self, ws, message):
        """处理WebSocket消息"""
        try:
            # 解析JSON消息
            try:
                data = json.loads(message)
                logger.info(f"🔍 OrdersMonitor 收到User Data Stream消息: {data}")
            except json.JSONDecodeError as e:
                logger.error(f"⚠️  JSON解析失败: {e}, 消息: {message[:100]}")
                return
            
            # ✅ 只在收到实际业务消息时更新最后消息时间（不包括ping/pong等协议消息）
            # 业务消息必须包含事件类型 'e' 字段
            event_type = data.get('e') if isinstance(data, dict) else None
            if event_type:
                # 收到实际业务消息，更新时间戳
                self.last_pong_time = datetime.now()
                logger.debug(f"💚 收到业务消息，更新健康检查时间戳: event_type={event_type}")
            
            # 处理ORDER_TRADE_UPDATE事件（普通订单）
            if isinstance(data, dict) and data.get('e') == 'ORDER_TRADE_UPDATE':
                order_data = data.get('o', {})
                if order_data:
                    # ✅ 添加 INFO 级别日志，方便用户查看
                    order_id = str(order_data.get('i', ''))
                    order_status = order_data.get('X', '')
                    logger.info(f"📨 [WebSocket] 收到订单更新: order_id={order_id}, status={order_status}")
                    self._handle_order_update(order_data)
            # ✅ 新增：处理ALGO_UPDATE事件（算法订单：止盈止损订单）
            elif isinstance(data, dict) and data.get('e') == 'ALGO_UPDATE':
                algo_order_data = data.get('o', {})
                if algo_order_data:
                    # ✅ 添加 INFO 级别日志，方便用户查看
                    algo_id = algo_order_data.get('aid')
                    algo_status = algo_order_data.get('X', '')
                    logger.info(f"📨 [WebSocket] 收到算法订单更新: algo_id={algo_id}, status={algo_status}")
                    self._handle_algo_order_update(algo_order_data)
            else:
                # 处理其他事件（可选）
                if event_type:
                    # ✅ listenKeyExpired 等重要事件使用 INFO 级别
                    if event_type in ['listenKeyExpired', 'ACCOUNT_UPDATE']:
                        logger.info(f"📨 [WebSocket] 收到事件: {event_type}")
                    else:
                        logger.debug(f"🔍 收到其他业务事件: {event_type}")
            
        except Exception as e:
            logger.error(f"❌ 处理WebSocket消息失败: {e}")
            import traceback
            traceback.print_exc()
    
    def _handle_order_update(self, order_data: dict):
        """
        处理订单更新事件
        
        Args:
            order_data: 订单数据（参考Binance文档）
        """
        order_id = str(order_data.get('i', ''))
        order_status = order_data.get('X', '')  # Order Status
        execution_type = order_data.get('x', '')  # Execution Type
        executed_qty = float(order_data.get('z', 0))  # Order Filled Accumulated Quantity
        avg_price = float(order_data.get('ap', 0))  # Average Price
        order_type = order_data.get('o', '')  # Order Type
        position_side = order_data.get('ps', '')  # Position Side
        logger.debug(f"📋 收到订单更新: order_id={order_id}, status={order_status}, type={order_type}, position_side={position_side}, executed_qty={executed_qty}, avg_price={avg_price}")
        
        # ✅ 合并锁块：无论订单是否在监控列表中，都更新缓存，并检查监控列表
        with self.lock:
            # 更新缓存（使用深拷贝）
            self.order_status_cache[order_id] = {
                'order_data': copy.deepcopy(order_data),  # ✅ 使用深拷贝保存完整的订单数据
                'timestamp': datetime.now(),  # 记录时间戳
                'order_status': order_status,
                'executed_qty': executed_qty,
                'avg_price': avg_price,
                'order_type': order_type
            }
            logger.debug(f"💾 已更新订单状态缓存: order_id={order_id}, status={order_status}, executed_qty={executed_qty}")
            
            # 检查是否在监控列表中
            if order_id not in self.order_monitor_list:
                # ✅ 如果订单不在监控列表中，但已成交，记录警告
                # 这可能是因为订单在添加到监控列表之前就已经成交了
                if order_status == 'FILLED' and executed_qty > 0:
                    logger.warning(f"⚠️ 收到已成交订单的WebSocket消息，但订单不在监控列表中: order_id={order_id} (已缓存状态，等待添加到监控列表)")
                return
            
            monitor_entry = self.order_monitor_list[order_id]
            
            # ✅ 修改：检查订单是否已完全处理（FILLED状态且已处理过）
            if monitor_entry.get('processed', False) and order_status == 'FILLED':
                logger.debug(f"ℹ️ 订单已完全成交并处理过，跳过WebSocket消息: order_id={order_id}")
                return
            
            # ✅ 修改：跟踪已处理的成交数量
            last_processed_qty = monitor_entry.get('last_processed_qty', 0.0)
            
            # 计算新增成交数量
            new_executed_qty = executed_qty - last_processed_qty
            
            # 如果没有新增成交数量，跳过处理
            if new_executed_qty <= 0:
                logger.debug(f"ℹ️ 没有新增成交数量，跳过处理: order_id={order_id}, executed_qty={executed_qty}, last_processed_qty={last_processed_qty}")
                return
            
            # 更新已处理的成交数量
            monitor_entry['last_processed_qty'] = executed_qty
            
            # 如果订单完全成交，标记为已处理
            if order_status == 'FILLED':
                monitor_entry['processed'] = True
            
            # ✅ 在锁内使用深拷贝，避免竞争条件
            monitor_data = copy.deepcopy(monitor_entry)
        
        # 处理订单成交或部分成交
        if execution_type == 'TRADE' and (order_status == 'FILLED' or order_status == 'PARTIALLY_FILLED'):
            if executed_qty > 0:
                self._process_order_filled(
                    order_id=order_id,
                    order_data=order_data,
                    executed_qty=executed_qty,  # ✅ 使用总成交数量
                    avg_price=avg_price,
                    order_type=order_type,
                    monitor_data=monitor_data
                )
    
    def _add_sl_tp_order_to_monitor(self, algo_order_id: str, client_algo_id: Optional[str], 
                                    order_type: str, symbol: str, strategy_id: int, 
                                    signal_order_id: str, price: float, quantity: float):
        """
        添加止盈止损订单到监控列表
        
        Args:
            algo_order_id: 算法订单ID（algoId）
            client_algo_id: 客户端算法订单ID（clientAlgoId），可选
            order_type: 订单类型（'STOP_LOSS' 或 'TAKE_PROFIT'）
            symbol: 交易对符号
            strategy_id: 策略ID
            signal_order_id: 触发信号订单ID
            price: 订单价格
            quantity: 订单数量
        """
        with self.lock:
            # ✅ 使用algo_order_id作为key（如果存在），否则使用client_algo_id
            # 优先使用algo_order_id，因为它是Binance的主要标识符
            monitor_key = algo_order_id if algo_order_id else client_algo_id
            if not monitor_key:
                logger.warning(f"⚠️ 无法添加止盈止损订单到监控列表：缺少algo_order_id和client_algo_id")
                return
            
            self.sl_tp_order_monitor_list[monitor_key] = {
                'algo_order_id': algo_order_id,  # 可能为None
                'client_algo_id': client_algo_id,  # 可能为None
                'order_type': order_type,  # 'STOP_LOSS' 或 'TAKE_PROFIT'
                'symbol': symbol,
                'strategy_id': strategy_id,
                'signal_order_id': signal_order_id,
                'price': price,
                'quantity': quantity,
                'status': 'NEW',  # 初始状态
                'created_at': datetime.now()
            }
        logger.debug(f"📋 止盈止损订单已添加到监控列表: monitor_key={monitor_key}, algo_order_id={algo_order_id}, client_algo_id={client_algo_id}, order_type={order_type}, signal_order_id={signal_order_id}")
    
    def _handle_algo_order_update(self, algo_order_data: dict):
        """
        处理算法订单更新事件（ALGO_UPDATE）
        
        Args:
            algo_order_data: 算法订单数据（参考Binance文档）
        """
        try:
            # 获取算法订单ID（可能是algoId或clientAlgoId）
            algo_id = algo_order_data.get('aid')  # algoId
            client_algo_id = algo_order_data.get('caid')  # clientAlgoId
            algo_status = algo_order_data.get('X', '')  # 算法订单状态
            algo_type = algo_order_data.get('at', '')  # 算法类型（如 'CONDITIONAL'）
            order_type = algo_order_data.get('o', '')  # 订单类型（如 'STOP_MARKET', 'TAKE_PROFIT_MARKET'）
            symbol = algo_order_data.get('s', '')  # 交易对符号
            quantity = float(algo_order_data.get('q', 0))  # 数量
            trigger_price = float(algo_order_data.get('tp', 0))  # 触发价格
            actual_price = float(algo_order_data.get('ap', 0))  # 实际成交价格（如果已触发）
            actual_order_id = algo_order_data.get('ai', '')  # 实际订单ID（如果已触发）
            
            logger.debug(f"📋 收到算法订单更新: algo_id={algo_id}, client_algo_id={client_algo_id}, status={algo_status}, order_type={order_type}, symbol={symbol}")
            
            # ✅ 查找监控列表中的订单（支持通过algoId或clientAlgoId查找）
            monitor_key = None
            with self.lock:
                # 先尝试通过algoId查找
                if algo_id:
                    algo_id_str = str(algo_id)
                    if algo_id_str in self.sl_tp_order_monitor_list:
                        monitor_key = algo_id_str
                
                # 如果没找到，尝试通过clientAlgoId查找
                if not monitor_key and client_algo_id:
                    client_algo_id_str = str(client_algo_id)
                    # 遍历监控列表，查找匹配的clientAlgoId
                    for key, entry in self.sl_tp_order_monitor_list.items():
                        if entry.get('client_algo_id') == client_algo_id_str:
                            monitor_key = key
                            # ✅ 如果监控列表中使用的是clientAlgoId作为key，但消息中有algoId，更新key为algoId
                            if algo_id and key != str(algo_id):
                                # 使用algoId作为新的key
                                new_key = str(algo_id)
                                entry['algo_order_id'] = new_key
                                self.sl_tp_order_monitor_list[new_key] = entry
                                del self.sl_tp_order_monitor_list[key]
                                monitor_key = new_key
                            break
                
                if not monitor_key:
                    # 如果不在监控列表中，记录日志但不处理
                    logger.debug(f"ℹ️ 算法订单不在监控列表中: algo_id={algo_id}, client_algo_id={client_algo_id}, status={algo_status}")
                    return
                
                monitor_entry = self.sl_tp_order_monitor_list[monitor_key]
                old_status = monitor_entry.get('status', '')
                
                # 更新订单状态
                monitor_entry['status'] = algo_status
                monitor_entry['last_update'] = datetime.now()
                
                # 如果已触发，更新实际成交信息
                if actual_order_id:
                    monitor_entry['actual_order_id'] = str(actual_order_id)
                if actual_price > 0:
                    monitor_entry['actual_price'] = actual_price
                if trigger_price > 0:
                    monitor_entry['trigger_price'] = trigger_price
                
                # 使用深拷贝避免竞争条件
                monitor_data = copy.deepcopy(monitor_entry)
            
            # 更新数据库订单状态
            self._update_sl_tp_order_status_in_db(monitor_data, algo_status, actual_price, actual_order_id)
            
            # 检查是否为最终状态，如果是则从监控列表删除
            final_statuses = ['FINISHED', 'CANCELED', 'EXPIRED', 'REJECTED']
            if algo_status in final_statuses:
                with self.lock:
                    if monitor_key in self.sl_tp_order_monitor_list:
                        del self.sl_tp_order_monitor_list[monitor_key]
                        logger.info(f"✅ 算法订单已达到最终状态，已从监控列表移除: algo_id={monitor_key}, status={algo_status}")
            
        except Exception as e:
            logger.error(f"❌ 处理算法订单更新失败: {e}")
            import traceback
            traceback.print_exc()
    
    def _update_sl_tp_order_status_in_db(self, monitor_data: dict, algo_status: str, 
                                         actual_price: float = 0, actual_order_id: str = ''):
        """
        更新止盈止损订单状态到数据库
        
        Args:
            monitor_data: 监控数据
            algo_status: 算法订单状态
            actual_price: 实际成交价格（如果已触发）
            actual_order_id: 实际订单ID（如果已触发）
        """
        db_manager = self._get_db_manager()
        if not db_manager:
            return
        
        try:
            # ✅ 优先使用algo_order_id，如果没有则使用client_algo_id
            algo_order_id = monitor_data.get('algo_order_id')
            if not algo_order_id:
                algo_order_id = monitor_data.get('client_algo_id')
            
            if not algo_order_id:
                logger.warning(f"⚠️ 无法更新算法订单状态：缺少algo_order_id和client_algo_id")
                return
            
            strategy_id = monitor_data.get('strategy_id')
            
            # 映射Binance算法订单状态到数据库状态
            status_mapping = {
                'NEW': 'NEW',
                'WORKING': 'NEW',     # 挂单中、未触发，等价于 NEW，不污染 DB 状态
                'TRIGGERING': 'NEW',  # 触发中，仍视为NEW
                'TRIGGERED': 'NEW',  # 已触发但未成交，仍视为NEW
                'FINISHED': 'FINISHED',  # 已触发并成交
                'CANCELED': 'CANCELED',
                'EXPIRED': 'EXPIRED',
                'REJECTED': 'REJECTED'
            }
            db_status = status_mapping.get(algo_status, algo_status)
            
            # 如果已触发（FINISHED），更新成交信息
            filled_price = None
            filled_quantity = None
            if algo_status == 'FINISHED':
                if actual_price > 0:
                    filled_price = actual_price
                elif monitor_data.get('trigger_price', 0) > 0:
                    filled_price = monitor_data.get('trigger_price')
                
                if monitor_data.get('quantity', 0) > 0:
                    filled_quantity = monitor_data.get('quantity')
            
            # 更新数据库（使用algo_order_id，因为数据库中使用的是algoId）
            success = db_manager.update_order_by_binance_id(
                binance_order_id=str(algo_order_id),
                status=db_status,
                filled_quantity=filled_quantity,
                filled_price=filled_price
            )
            
            if success:
                logger.info(f"✅ 算法订单状态已更新到数据库: algo_order_id={algo_order_id}, status={db_status}, filled_price={filled_price}")
            else:
                logger.debug(f"⚠️ 更新算法订单状态失败: algo_order_id={algo_order_id} (可能数据库中不存在该订单)")
                
        except Exception as e:
            logger.error(f"❌ 更新算法订单状态到数据库异常: algo_order_id={monitor_data.get('algo_order_id')}, error={e}")
            import traceback
            traceback.print_exc()
    
    def _cancel_old_order(self, order_id: str, symbol: str, order_type: str = '订单', strategy_id: int = None):
        """
        取消旧的普通订单（基础订单，如限价止盈订单）
        
        Args:
            order_id: 订单ID
            symbol: 交易对符号
            order_type: 订单类型（用于日志显示）
            strategy_id: 策略ID（用于更新数据库）
        
        Returns:
            bool: 是否取消成功
        """
        if not order_id:
            return False
        
        try:
            # 调用 binance_client 的取消订单方法
            result = self.binance_client.cancel_order(
                symbol=symbol,
                order_id=order_id
            )
            
            if result and not result.get('error'):
                logger.info(f"✅ 取消旧{order_type}订单成功: {order_id}")
                
                # ✅ 更新数据库订单状态为 CANCELED
                if strategy_id:
                    db_manager = self._get_db_manager()
                    if db_manager:
                        try:
                            success = db_manager.update_order_by_binance_id(
                                binance_order_id=str(order_id),
                                status='CANCELED'
                            )
                            if success:
                                logger.info(f"✅ {order_type}订单状态已更新到数据库: order_id={order_id}, status=CANCELED")
                            else:
                                logger.debug(f"⚠️ 更新{order_type}订单状态失败: order_id={order_id} (可能数据库中不存在该订单)")
                        except Exception as e:
                            logger.error(f"❌ 更新{order_type}订单状态异常: order_id={order_id}, error={e}")
                
                return True
            else:
                logger.warning(f"⚠️  取消旧{order_type}订单失败: {order_id}")
                return False
        except Exception as e:
            logger.error(f"❌ 取消旧{order_type}订单异常: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _cancel_old_algo_order(self, algo_order_id: str, symbol: str, order_type: str = '止盈止损', strategy_id: int = None):
        """
        取消旧的算法订单（止盈或止损）
        
        Args:
            algo_order_id: 算法订单ID
            symbol: 交易对符号
            order_type: 订单类型（用于日志显示）
            strategy_id: 策略ID（用于更新数据库）
        
        Returns:
            bool: 是否取消成功
        """
        if not algo_order_id:
            return False
        
        try:
            # 尝试将algo_id转换为int（Binance API要求）
            try:
                algo_id_int = int(algo_order_id)
            except (ValueError, TypeError):
                algo_id_int = None
                client_algo_id = str(algo_order_id)
            
            if algo_id_int:
                result = self.binance_client.cancel_algo_order(
                    algo_id=algo_id_int,
                    symbol=symbol
                )
            else:
                result = self.binance_client.cancel_algo_order(
                    client_algo_id=client_algo_id,
                    symbol=symbol
                )
            
            if result and not result.get('error'):
                logger.info(f"✅ 取消旧{order_type}订单成功: {algo_order_id}")
                
                # ✅ 从止盈止损订单监控列表中删除（支持通过algoId或clientAlgoId查找）
                with self.lock:
                    algo_order_id_str = str(algo_order_id)
                    removed = False
                    
                    # 先尝试直接通过algoId查找
                    if algo_order_id_str in self.sl_tp_order_monitor_list:
                        del self.sl_tp_order_monitor_list[algo_order_id_str]
                        removed = True
                    else:
                        # 如果没找到，遍历列表查找匹配的clientAlgoId或algoId
                        for key, entry in list(self.sl_tp_order_monitor_list.items()):
                            if (entry.get('algo_order_id') == algo_order_id_str or 
                                entry.get('client_algo_id') == algo_order_id_str):
                                del self.sl_tp_order_monitor_list[key]
                                removed = True
                                break
                    
                    if removed:
                        logger.debug(f"✅ 已从止盈止损订单监控列表移除: algo_order_id={algo_order_id}")
                    else:
                        logger.debug(f"ℹ️ 订单不在止盈止损监控列表中: algo_order_id={algo_order_id}")
                
                # ✅ 更新数据库订单状态为 CANCELED
                if strategy_id and self.live_trading_manager and hasattr(self.live_trading_manager, 'main_window'):
                    db_manager = self._get_db_manager()
                    if db_manager:
                        try:
                            success = db_manager.update_order_by_binance_id(
                                binance_order_id=str(algo_order_id),
                                status='CANCELED'
                            )
                            if success:
                                logger.info(f"✅ {order_type}订单状态已更新到数据库: order_id={algo_order_id}, status=CANCELED")
                            else:
                                logger.debug(f"⚠️ 更新{order_type}订单状态失败: order_id={algo_order_id} (可能数据库中不存在该订单)")
                        except Exception as e:
                            logger.error(f"❌ 更新{order_type}订单状态异常: order_id={algo_order_id}, error={e}")
                
                return True
            else:
                logger.warning(f"⚠️  取消旧{order_type}订单失败: {algo_order_id}")
                return False
        except Exception as e:
            logger.error(f"❌ 取消旧{order_type}订单异常: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _calculate_stop_loss_price(self, signal_type: str, avg_price: float, price_param: float, price_type: str) -> float:
        """
        计算止损价格
        
        Args:
            signal_type: 信号类型（'LONG' 或 'SHORT'）
            avg_price: 平均成交价格
            price_param: 价格参数
            price_type: 价格类型（'差值' 或 '百分比'）
        
        Returns:
            float: 止损价格
        """
        if signal_type == 'LONG':
            return avg_price * (1 - price_param / 100) if price_type == '百分比' else avg_price - price_param
        else:  # SHORT
            return avg_price * (1 + price_param / 100) if price_type == '百分比' else avg_price + price_param
    
    def _calculate_take_profit_price(self, signal_type: str, avg_price: float, price_param: float, price_type: str) -> float:
        """
        计算止盈价格
        
        Args:
            signal_type: 信号类型（'LONG' 或 'SHORT'）
            avg_price: 平均成交价格
            price_param: 价格参数
            price_type: 价格类型（'差值' 或 '百分比'）
        
        Returns:
            float: 止盈价格
        """
        if signal_type == 'LONG':
            return avg_price * (1 + price_param / 100) if price_type == '百分比' else avg_price + price_param
        else:  # SHORT
            return avg_price * (1 - price_param / 100) if price_type == '百分比' else avg_price - price_param
    
    def _place_stop_loss_order(self, order_id: str, symbol: str, signal_type: str, 
                               executed_qty: float, avg_price: float, sl_tp_data: dict) -> str:
        """
        提交止损单
        
        Args:
            order_id: 限价单ID
            symbol: 交易对符号
            signal_type: 信号类型
            executed_qty: 已成交数量
            avg_price: 平均成交价格
            sl_tp_data: 止盈止损数据
        
        Returns:
            str: 止损订单ID，失败返回None
        """
        if 'stop_loss' not in sl_tp_data or not sl_tp_data['stop_loss']:
            return None
        
        stop_loss_info = sl_tp_data['stop_loss']
        price_param = stop_loss_info.get('price_param', 0)
        price_type = stop_loss_info.get('price_type', '差值')
        stop_side = stop_loss_info.get('side', 'SELL' if signal_type == 'LONG' else 'BUY')
        position_side = stop_loss_info.get('position_side', signal_type)
        
        # 计算止损价格
        stop_loss_price = self._calculate_stop_loss_price(signal_type, avg_price, price_param, price_type)
        
        try:
            stop_loss_result = self.binance_client.place_stop_loss_order(
                symbol=symbol,
                side=stop_side,
                stop_price=stop_loss_price,
                quantity=executed_qty,
                position_side=position_side
            )
            
                # ✅ 检查下单结果
            if stop_loss_result is not None and not stop_loss_result.get('error'):
                stop_loss_order_id = stop_loss_result.get('algoId')
                client_algo_id = stop_loss_result.get('clientAlgoId')
                # 更新监控数据
                with self.lock:
                    if order_id in self.order_monitor_list:
                        self.order_monitor_list[order_id]['stop_loss_order_id'] = stop_loss_order_id
                
                # ✅ 新增：将止损订单添加到止盈止损订单监控列表
                self._add_sl_tp_order_to_monitor(
                    algo_order_id=str(stop_loss_order_id),
                    client_algo_id=str(client_algo_id) if client_algo_id else None,
                    order_type='STOP_LOSS',
                    symbol=symbol,
                    strategy_id=sl_tp_data.get('strategy_id'),
                    signal_order_id=str(order_id),
                    price=stop_loss_price,
                    quantity=executed_qty
                )
                
                logger.info(f"✅ 止损单已提交: order_id={stop_loss_order_id}, price={stop_loss_price:.2f}, quantity={executed_qty}, 触发信号订单ID: {order_id}")
                
                # ✅ 创建数据库订单记录
                strategy_id = sl_tp_data.get('strategy_id')
                if strategy_id:
                    db_manager = self._get_db_manager()
                    if db_manager:
                        try:
                            order_data = {
                                'order_id': str(stop_loss_order_id),
                                'client_order_id': str(client_algo_id) if client_algo_id else str(stop_loss_order_id),
                                'symbol': symbol,
                                'side': stop_side,
                                'position_side': position_side,
                                'order_type': 'STOP_MARKET',  # 止损单类型
                                'quantity': executed_qty,
                                'price': stop_loss_price,
                                'status': 'NEW',
                                'signal_kline_index': sl_tp_data.get('signal_kline_index'),
                                'signal_type': signal_type,
                                'expired_at': None,
                                'use_type': 'SL_CLOSE',
                                'signal_order_id': str(order_id)  # ✅ 设置触发信号订单的 Binance order_id
                            }
                            db_order_id = db_manager.create_order(strategy_id, order_data)
                            logger.info(f"✅ 止损订单已保存到数据库: order_id={stop_loss_order_id}, DB ID={db_order_id}, signal_order_id={order_id}")
                        except Exception as e:
                            logger.error(f"❌ 保存止损订单到数据库异常: order_id={stop_loss_order_id}, error={e}")
                            import traceback
                            traceback.print_exc()
                
                return stop_loss_order_id
            else:
                # ✅ 下单失败，记录警告
                if stop_loss_result is None:
                    logger.warning(f"⚠️ 下止损单失败: 返回 None (可能网络超时或API错误)")
                else:
                    error_msg = stop_loss_result.get('error_message', '未知错误')
                    logger.warning(f"⚠️ 下止损单失败: {error_msg}")
                error_msg = stop_loss_result.get('error_message', '未知错误') if stop_loss_result else '返回结果为None'
                logger.error(f"❌ 止损单提交失败: {error_msg}")
                return None
        except Exception as e:
            logger.error(f"❌ 止损单提交异常: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _place_take_profit_order(self, order_id: str, symbol: str, signal_type: str, 
                                executed_qty: float, avg_price: float, sl_tp_data: dict) -> str:
        """
        提交止盈单（使用基础订单限价止盈）
        
        Args:
            order_id: 限价单ID
            symbol: 交易对符号
            signal_type: 信号类型
            executed_qty: 已成交数量
            avg_price: 平均成交价格
            sl_tp_data: 止盈止损数据
        
        Returns:
            str: 止盈订单ID，失败返回None
        """
        if 'take_profit' not in sl_tp_data or not sl_tp_data['take_profit']:
            return None
        
        take_profit_info = sl_tp_data['take_profit']
        price_param = take_profit_info.get('price_param', 0)
        price_type = take_profit_info.get('price_type', '差值')
        take_profit_side = take_profit_info.get('side', 'SELL' if signal_type == 'LONG' else 'BUY')
        position_side = take_profit_info.get('position_side', signal_type)
        
        # 计算止盈触发价格
        take_profit_trigger_price = self._calculate_take_profit_price(signal_type, avg_price, price_param, price_type)
        
        # 计算限价（确保能成交）
        # 多头平仓（SELL）：限价略低于触发价格
        # 空头平仓（BUY）：限价略高于触发价格
        # if signal_type == 'LONG':  # 多头平仓，SELL
        #     # 限价设置为触发价格的 99.9%，确保能成交
        #     take_profit_limit_price = take_profit_trigger_price * 0.999
        # else:  # 空头平仓，BUY
        #     # 限价设置为触发价格的 100.1%，确保能成交
        #     take_profit_limit_price = take_profit_trigger_price * 1.001
        
        take_profit_limit_price = take_profit_trigger_price  # ✅ 直接使用触发价格作为限价，这里我们使用基础订单方式，保证了触达即成交，不需要设置偏移价了
        try:
            # ✅ 使用新的基础订单限价止盈接口
            take_profit_result = self.binance_client.place_take_profit_limit_order(
                symbol=symbol,
                side=take_profit_side,
                stop_price=take_profit_trigger_price,  # 触发价格
                price=take_profit_limit_price,  # 限价
                quantity=executed_qty,
                position_side=position_side
            )
            
            # ✅ 检查下单结果
            if take_profit_result is not None and not take_profit_result.get('error'):
                # ✅ 基础订单返回 orderId（不是 algoId）
                take_profit_order_id = take_profit_result.get('orderId')
                client_order_id = take_profit_result.get('clientOrderId')
                
                # 更新监控数据
                with self.lock:
                    if order_id in self.order_monitor_list:
                        self.order_monitor_list[order_id]['take_profit_order_id'] = take_profit_order_id
                
                # ✅ 注意：基础订单（TAKE_PROFIT）不需要添加到 sl_tp_order_monitor_list
                # sl_tp_order_monitor_list 专门用于监控 Algo Order (ALGO_UPDATE 事件)
                # 基础订单会通过普通的 ORDER_TRADE_UPDATE 事件更新状态
                
                logger.info(f"✅ 限价止盈单已提交: order_id={take_profit_order_id}, trigger_price={take_profit_trigger_price:.2f}, limit_price={take_profit_limit_price:.2f}, quantity={executed_qty}, 触发信号订单ID: {order_id}")
                
                # ✅ 创建数据库订单记录
                strategy_id = sl_tp_data.get('strategy_id')
                if strategy_id:
                    db_manager = self._get_db_manager()
                    if db_manager:
                        try:
                            order_data = {
                                'order_id': str(take_profit_order_id),
                                'client_order_id': str(client_order_id) if client_order_id else str(take_profit_order_id),
                                'symbol': symbol,
                                'side': take_profit_side,
                                'position_side': position_side,
                                'order_type': 'TAKE_PROFIT',  # ✅ 基础订单类型：TAKE_PROFIT（限价止盈）
                                'quantity': executed_qty,
                                'price': take_profit_trigger_price,  # 使用触发价格作为订单价格
                                'status': 'NEW',
                                'signal_kline_index': sl_tp_data.get('signal_kline_index'),
                                'signal_type': signal_type,
                                'expired_at': None,
                                'use_type': 'TP_CLOSE',
                                'signal_order_id': str(order_id)  # ✅ 设置触发信号订单的 Binance order_id
                            }
                            db_order_id = db_manager.create_order(strategy_id, order_data)
                            logger.info(f"✅ 止盈订单已保存到数据库: order_id={take_profit_order_id}, DB ID={db_order_id}, signal_order_id={order_id}")
                        except Exception as e:
                            logger.error(f"❌ 保存止盈订单到数据库异常: order_id={take_profit_order_id}, error={e}")
                            import traceback
                            traceback.print_exc()
                
                return take_profit_order_id
            else:
                # ✅ 下单失败，记录警告
                if take_profit_result is None:
                    logger.warning(f"⚠️ 下限价止盈单失败: 返回 None (可能网络超时或API错误)")
                else:
                    error_msg = take_profit_result.get('error_message', '未知错误')
                    logger.warning(f"⚠️ 下限价止盈单失败: {error_msg}")
                error_msg = take_profit_result.get('error_message', '未知错误') if take_profit_result else '返回结果为None'
                logger.error(f"❌ 限价止盈单提交失败: {error_msg}")
                return None
        except Exception as e:
            logger.error(f"❌ 限价止盈单提交异常: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _create_or_update_position_record(self, order_id: str, symbol: str, signal_type: str,
                                         executed_qty: float, avg_price: float, sl_tp_data: dict,
                                         stop_loss_order_id: str, take_profit_order_id: str, order_type: str):
        """
        创建或更新持仓记录
        
        Args:
            order_id: 限价单ID
            symbol: 交易对符号
            signal_type: 信号类型
            executed_qty: 已成交数量
            avg_price: 平均成交价格
            sl_tp_data: 止盈止损数据
            stop_loss_order_id: 止损订单ID
            take_profit_order_id: 止盈订单ID
        """
        if not self.live_trading_manager:
            return
        
        try:
            strategy_id = sl_tp_data.get('strategy_id')
            strategy_name = sl_tp_data.get('strategy_name', f"策略_{strategy_id}")
            
            if not strategy_id:
                logger.warning(f"⚠️ 无法创建持仓记录: strategy_id 未找到")
                return
            
            db_manager = self._get_db_manager()
            if not db_manager:
                logger.warning(f"⚠️ 无法获取数据库管理器: order_id={order_id}")
                return
            
            order_db = db_manager.get_order_by_binance_id(str(order_id))
            if order_db:
                signal_kline_index = order_db.get('signal_kline_index')
            else:
                logger.warning(f"⚠️ 无法获取订单记录: order_id={order_id}")
                signal_kline_index = None
            
            # 计算止损和止盈价格（用于保存到数据库）
            stop_loss_price = None
            take_profit_price = None
            
            if 'stop_loss' in sl_tp_data and sl_tp_data['stop_loss']:
                stop_loss_info = sl_tp_data['stop_loss']
                price_param = stop_loss_info.get('price_param', 0)
                price_type = stop_loss_info.get('price_type', '差值')
                stop_loss_price = self._calculate_stop_loss_price(signal_type, avg_price, price_param, price_type)
            
            if 'take_profit' in sl_tp_data and sl_tp_data['take_profit']:
                take_profit_info = sl_tp_data['take_profit']
                price_param = take_profit_info.get('price_param', 0)
                price_type = take_profit_info.get('price_type', '差值')
                take_profit_price = self._calculate_take_profit_price(signal_type, avg_price, price_param, price_type)


            # 市价单的entry_kline_index为signal_kline_index
            if order_type == 'MARKET':
                entry_kline_index = signal_kline_index
            else:
                entry_kline_index = None
                
            # 调用创建或更新持仓记录
            self.live_trading_manager._create_or_update_position(
                strategy_id=strategy_id,
                trading_symbol=symbol,
                side=signal_type,  # 'LONG' 或 'SHORT'
                quantity=executed_qty,
                entry_price=avg_price,
                strategy_name=strategy_name,
                order_id=str(order_id),
                stop_loss_order_id=str(stop_loss_order_id) if stop_loss_order_id else None,
                stop_loss_price=stop_loss_price,  # 修正参数名：stop_lose_price -> stop_loss_price
                take_profit_order_id=str(take_profit_order_id) if take_profit_order_id else None,
                take_profit_price=take_profit_price,
                entry_kline_index=entry_kline_index,
                exit_kline_index=None,
                signal_kline_index=signal_kline_index
            )
            logger.info(f"✅ 持仓记录已创建或更新: strategy_id={strategy_id}, order_id={order_id}, entry_price={avg_price:.2f}")
        except Exception as e:
            logger.error(f"❌ 创建或更新持仓记录失败: {e}")
            import traceback
            traceback.print_exc()
    
    def _invoke_order_filled_callback(self, order_id: str, executed_qty: float, avg_price: float,
                                     stop_loss_order_id: str, take_profit_order_id: str, sl_tp_data: dict):
        """
        调用订单成交回调函数
        
        Args:
            order_id: 订单ID
            executed_qty: 已成交数量
            avg_price: 平均成交价格
            stop_loss_order_id: 止损订单ID
            take_profit_order_id: 止盈订单ID
            sl_tp_data: 止盈止损数据
        """
        if not self.on_order_filled_callback:
            return
        
        try:
            self.on_order_filled_callback({
                'order_id': order_id,
                'executed_qty': executed_qty,
                'avg_price': avg_price,
                'stop_loss_order_id': stop_loss_order_id,
                'take_profit_order_id': take_profit_order_id,
                'strategy_id': sl_tp_data.get('strategy_id'),
                'strategy_name': sl_tp_data.get('strategy_name')
            })
        except Exception as e:
            logger.error(f"❌ 回调函数执行失败: {e}")
            import traceback
            traceback.print_exc()
    
    def process_filled_order_with_sl_tp(self, order_id: str, executed_qty: float, 
                                        avg_price: float, order_type: str, 
                                        symbol: str, signal_type: str, 
                                        sl_tp_data: dict,
                                        old_stop_loss_order_id: str = None,
                                        old_take_profit_order_id: str = None,
                                        order_status: str = 'FILLED',
                                        place_stop_loss: bool = True,
                                        place_take_profit: bool = True):
        """
        处理已成交订单的止盈止损逻辑（公共方法，可被外部调用）
        
        功能：
        1. 更新数据库中的订单状态
        2. 取消旧的止盈止损订单（如果存在）
        3. 提交新的止损单和止盈单
        4. 创建或更新持仓记录
        5. 调用回调函数
        
        Args:
            order_id: 订单ID
            executed_qty: 已成交数量
            avg_price: 平均成交价格
            order_type: 订单类型（'MARKET' 或 'LIMIT'）
            symbol: 交易对符号
            signal_type: 信号类型（'LONG' 或 'SHORT'）
            sl_tp_data: 止盈止损数据字典，包含策略ID和止盈止损参数
            old_stop_loss_order_id: 旧的止损订单ID（需要取消，补充场景传 None）
            old_take_profit_order_id: 旧的止盈订单ID（需要取消，补充场景传 None）
            order_status: 订单状态（默认'FILLED'）
            place_stop_loss: 是否下新止损单（补充场景下传 not has_stop_loss）
            place_take_profit: 是否下新止盈单（补充场景下传 not has_take_profit）
        """
        logger.info(f"📋 处理已成交订单止盈止损: order_id={order_id}, executed_qty={executed_qty}, "
                   f"order_type={order_type}, avg_price={avg_price}, order_status={order_status}")
        
        # 1. 更新数据库中的订单状态
        db_manager = self._get_db_manager()
        if db_manager:
            try:
                success = db_manager.update_order_by_binance_id(
                    binance_order_id=str(order_id),
                    status=order_status,
                    filled_quantity=executed_qty if executed_qty > 0 else None,
                    filled_price=avg_price if avg_price > 0 else None
                )
                if success:
                    logger.info(f"✅ 订单状态已更新到数据库: order_id={order_id}, status={order_status}, "
                              f"filled_quantity={executed_qty}, filled_price={avg_price}")
                else:
                    logger.warning(f"⚠️ 更新订单状态失败: order_id={order_id} (可能数据库中不存在该订单)")
            except Exception as e:
                logger.error(f"❌ 更新订单状态异常: order_id={order_id}, error={e}")
                import traceback
                traceback.print_exc()
        
        strategy_id = sl_tp_data.get('strategy_id')
        
        # 2. 取消旧的止盈止损订单（如果存在）
        # ✅ 止损订单仍然是 Algo Order，使用 cancel_algo_order
        self._cancel_old_algo_order(old_stop_loss_order_id, symbol, '止损', strategy_id)
        # ✅ 止盈订单现在是基础订单（TAKE_PROFIT），使用 cancel_order
        self._cancel_old_order(old_take_profit_order_id, symbol, '止盈', strategy_id)
        
        # 3. 提交新的止损单和止盈单（仅下缺失的订单）
        stop_loss_order_id = self._place_stop_loss_order(
            order_id, symbol, signal_type, executed_qty, avg_price, sl_tp_data
        ) if place_stop_loss else None
        take_profit_order_id = self._place_take_profit_order(
            order_id, symbol, signal_type, executed_qty, avg_price, sl_tp_data
        ) if place_take_profit else None
        
        # 4. 创建或更新持仓记录
        self._create_or_update_position_record(
            order_id, symbol, signal_type, executed_qty, avg_price, sl_tp_data,
            stop_loss_order_id, take_profit_order_id, order_type
        )
        
        # 5. 调用回调函数
        self._invoke_order_filled_callback(
            order_id, executed_qty, avg_price, stop_loss_order_id, take_profit_order_id, sl_tp_data
        )
        
        return stop_loss_order_id, take_profit_order_id
    
    def _process_order_filled(self, order_id: str, order_data: dict, executed_qty: float, 
                            avg_price: float, order_type: str, monitor_data: dict):
        """
        处理订单成交逻辑（主流程）
        
        Args:
            order_id: 订单ID
            order_data: 订单数据
            executed_qty: 已成交数量
            avg_price: 平均成交价格
            monitor_data: 监控数据
        """
        execution_type = order_data.get('x', '')
        order_status = order_data.get('X', '')
        
        logger.info(f"📋 订单状态Update: order_id={order_id}, executed_qty={executed_qty}, order_type={order_type}, avg_price={avg_price}, execution_type={execution_type}, order_status={order_status}")
        
        # 提取监控数据
        limit_order_info = monitor_data['limit_order_info']
        sl_tp_data = monitor_data['sl_tp_data']
        symbol = limit_order_info.get('symbol', '')
        signal_type = sl_tp_data.get('signal_type', 'LONG')
        old_stop_loss_order_id = monitor_data.get('stop_loss_order_id')
        old_take_profit_order_id = monitor_data.get('take_profit_order_id')
        
        # 调用公共方法处理止盈止损逻辑
        self.process_filled_order_with_sl_tp(
            order_id=order_id,
            executed_qty=executed_qty,
            avg_price=avg_price,
            order_type=order_type,
            symbol=symbol,
            signal_type=signal_type,
            sl_tp_data=sl_tp_data,
            old_stop_loss_order_id=old_stop_loss_order_id,
            old_take_profit_order_id=old_take_profit_order_id,
            order_status=order_status
        )
    
    def _on_error(self, ws, error):
        """处理WebSocket错误"""
        logger.error(f"❌ WebSocket错误: {error}")
        # 触发重连
        if self.running:
            self.reconnecting = True
    
    def _on_close(self, ws, close_status_code, close_msg):
        """处理WebSocket关闭"""
        logger.warning(f"⚠️  WebSocket连接已关闭: status={close_status_code}, msg={close_msg}")
        self.ws = None
        
        # 如果还在运行，设置重连标志，让主线程处理重连
        if self.running:
            self.reconnecting = True  # 设置重连标志，主线程会处理重连
    
    def _on_open(self, ws):
        """处理WebSocket打开"""
        logger.info("✅ User Data Stream WebSocket连接已建立")
        self.last_pong_time = datetime.now()
        logger.info(f"✅ 已重置健康检查时间戳: {self.last_pong_time.strftime('%Y-%m-%d %H:%M:%S')}")
        # ✅ 成功连接后重置重连计数器
        if self.reconnect_count > 0:
            logger.info(f"✅ WebSocket重连成功 (之前尝试了 {self.reconnect_count} 次)")
        self.reconnect_count = 0
    
    def _run_websocket(self):
        """运行WebSocket连接"""
        try:
            self.running = True
            
            while self.running:
                try:
                    # 如果需要重连，处理重连逻辑
                    if self.reconnecting:
                        # ✅ 先检查是否超过最大重连次数
                        if self.reconnect_count >= self.max_reconnect_attempts:
                            logger.error(f"❌ 已达到最大重连次数 ({self.max_reconnect_attempts})，停止重连")
                            self.running = False
                            break
                        
                        self.reconnect_count += 1
                        logger.info(f"🔄 尝试重连User Data Stream (第 {self.reconnect_count}/{self.max_reconnect_attempts} 次)...")
                        self.ws = None
                        
                        # 等待后重新连接
                        time.sleep(self.reconnect_interval)
                        
                        # 重新获取listenKey
                        old_listen_key = self.listen_key
                        try:
                            self.listen_key = self.binance_client.start_user_data_stream()
                            if not self.listen_key:
                                logger.error("❌ 获取新listenKey失败")
                                self.reconnecting = True
                                continue
                            
                            # ✅ 更新 WebSocket URL（使用新的 listenKey）
                            self.user_data_stream_url = f"{self.WS_USER_DATA_STREAM_URL}?{urlencode({'listenKey': self.listen_key, 'events': '/'.join(self.PRIVATE_WS_EVENTS)})}"
                            
                            # 判断是否获取到新的listenKey
                            is_same = (old_listen_key == self.listen_key)
                            if is_same:
                                logger.warning(f"⚠️  获取到的listenKey与旧的相同: {self.listen_key[:30]}... (可能旧Key仍有效)")
                            else:
                                logger.info(f"✅ 已更新WebSocket URL (新listenKey: {self.listen_key[:30]}...)")
                            
                            # 关闭旧的listenKey（只有不同时才关闭）
                            if old_listen_key and not is_same:
                                try:
                                    self.binance_client.close_user_data_stream(old_listen_key)
                                    logger.info(f"✅ 已关闭旧listenKey: {old_listen_key[:30]}...")
                                except:
                                    pass
                        except Exception as e:
                            logger.error(f"❌ 重新获取listenKey失败: {e}")
                            self.reconnecting = True
                            continue
                        
                        # 创建新的WebSocket连接
                        try:
                            self.ws = websocket.WebSocketApp(
                                self.user_data_stream_url,  # ✅ 现在使用更新后的URL
                                on_message=self._on_message,
                                on_error=self._on_error,
                                on_close=self._on_close,
                                on_open=self._on_open
                            )
                            self.reconnecting = False
                            logger.info("✅ WebSocket重新连接准备就绪")
                        except Exception as e:
                            logger.error(f"❌ 创建新WebSocket连接失败: {e}")
                            self.reconnecting = True
                            continue
                    
                    # 运行WebSocket连接
                    if self.ws and not self.reconnecting:
                        # 根据配置决定是否使用代理
                        if self.use_proxy and self.proxy_host and self.proxy_port:
                            self.ws.run_forever(
                                sslopt={"cert_reqs": ssl.CERT_NONE},
                                http_proxy_host=self.proxy_host,
                                http_proxy_port=self.proxy_port,
                                proxy_type="http",
                                ping_interval=self.ping_interval,  # 每20秒发送PING帧
                                ping_timeout=self.ping_timeout  # PONG响应超时10秒
                            )
                        else:
                            # 不使用代理，直接连接
                            self.ws.run_forever(
                                sslopt={"cert_reqs": ssl.CERT_NONE},
                                ping_interval=self.ping_interval,
                                ping_timeout=self.ping_timeout
                            )
                        # WebSocket 连接结束，如果还在运行状态，触发重连
                        if self.running:
                            logger.warning("⚠️ WebSocket连接已断开，触发重连...")
                            self.reconnecting = True
                        else:
                            break
                        
                except websocket.WebSocketException as e:
                    logger.error(f"WebSocket run error: {str(e)}")
                    if self.running:
                        self.reconnecting = True
                    else:
                        break
                except Exception as e:
                    logger.error(f"WebSocket运行异常: {e}")
                    if self.running:
                        self.reconnecting = True
                    else:
                        break
                    
                time.sleep(1)
                        
        except Exception as e:
            logger.error(f"WebSocket线程错误: {e}")
        finally:
            self.running = False
            self.reconnecting = False
            logger.info("WebSocket thread stopped")
    
    def _reconnect(self):
        """设置重连标志，由WebSocket线程处理实际重连逻辑"""
        logger.info("⚠️ 触发重连流程...")
        self.reconnecting = True
        # ✅ 立即重置last_pong_time，避免重连期间健康检查再次触发重连
        self.last_pong_time = datetime.now()
        logger.info(f"✅ 已临时重置健康检查时间戳（重连中）: {self.last_pong_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # ✅ 主动关闭当前WebSocket连接，让run_forever()退出
        if self.ws:
            try:
                logger.info("🔌 主动关闭当前WebSocket连接以触发重连...")
                self.ws.close()
            except Exception as e:
                logger.warning(f"⚠️ 关闭WebSocket连接时出错: {e}")
        
        # ✅ 移除此处的累加，避免与_run_websocket()中的累加重复
    
    def _keepalive_loop(self):
        """保持User Data Stream活跃的循环"""
        while self.running:
            try:
                time.sleep(self.keepalive_interval)
                if self.running and self.listen_key:
                    success = self.binance_client.keepalive_user_data_stream(self.listen_key)
                    if not success:
                        logger.warning("⚠️  keepalive失败，尝试重连WebSocket")
                        # 触发重连
                        self._reconnect()
                    else:
                        logger.info(f"✅ keepalive成功: {self.listen_key}")
                
                # ✅ 定期清理过期的缓存（每小时清理一次）
                if self.running:
                    self._cleanup_expired_cache(max_age_seconds=3600)
            except Exception as e:
                logger.error(f"❌ keepalive循环异常: {e}")
                if self.running:
                    time.sleep(60)  # 出错后等待1分钟再重试
    
    def _cleanup_expired_cache(self, max_age_seconds: int = 3600):
        """
        清理过期的订单状态缓存
        
        Args:
            max_age_seconds: 缓存最大保留时间（秒），默认1小时
        """
        current_time = datetime.now()
        expired_order_ids = []
        
        with self.lock:
            for order_id, cache_entry in self.order_status_cache.items():
                cache_time = cache_entry.get('timestamp')
                if cache_time:
                    age_seconds = (current_time - cache_time).total_seconds()
                    if age_seconds > max_age_seconds:
                        expired_order_ids.append(order_id)
            
            # 删除过期的缓存
            for order_id in expired_order_ids:
                del self.order_status_cache[order_id]
                logger.debug(f"🗑️ 清理过期缓存: order_id={order_id}")
    
    def _health_check_loop(self):
        """
        连接健康检查循环
        
        定期检查：
        1. 是否长时间未收到任何WebSocket消息（包括ORDER_TRADE_UPDATE、ALGO_UPDATE等实际业务消息）
        2. 如果超过 connection_timeout 时间未收到消息，触发重连
        
        注意：ping/pong是底层WebSocket协议的keepalive机制，不算作业务消息
        """
        check_interval = 60  # 每60秒检查一次
        
        while self.running:
            try:
                time.sleep(check_interval)
                
                if not self.running:
                    break
                
                # 检查最后一次收到消息的时间
                if self.last_pong_time:
                    elapsed_seconds = (datetime.now() - self.last_pong_time).total_seconds()
                    
                    # 如果超过阈值时间没有收到任何消息，触发重连
                    if elapsed_seconds > self.connection_timeout:
                        logger.warning(
                            f"⚠️ User Data Stream连接可能失效：已{elapsed_seconds:.1f}秒未收到任何消息 "
                            f"(超时阈值: {self.connection_timeout}秒)，触发重连..."
                        )
                        self._reconnect()
                    else:
                        logger.debug(
                            f"💚 User Data Stream连接健康检查通过：最后收到消息时间 {elapsed_seconds:.1f}秒前"
                        )
                else:
                    logger.debug("💚 User Data Stream连接健康检查：尚未收到第一条消息")
                    
            except Exception as e:
                logger.error(f"❌ 连接健康检查异常: {e}")
                if self.running:
                    time.sleep(60)  # 出错后等待1分钟再重试
        
        logger.info("💤 健康检查线程已退出")