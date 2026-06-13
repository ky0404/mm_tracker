"""
MMTracker 基础 HTTP 客户端

提供统一的 HTTP 请求方法，包含：
- 重试机制（指数退避）
- 超时控制
- 统一的请求头（User-Agent）
- 错误处理与日志记录
"""

import time
import logging
from typing import Optional, Dict, Any
from urllib.parse import urljoin

import requests

from config import config

# 配置日志
logger = logging.getLogger(__name__)


class HTTPError(Exception):
    """HTTP 请求错误"""
    def __init__(self, message: str, status_code: Optional[int] = None, response_text: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class RetryableError(Exception):
    """可重试的错误（临时性错误）"""
    pass


class NonRetryableError(Exception):
    """不可重试的错误（永久性错误，如 4xx 状态码）"""
    pass


class BaseFetcher:
    """
    基础数据获取器
    
    提供标准化的 HTTP 请求方法，子类只需实现具体的 API 调用逻辑
    
    Attributes:
        base_url: API 基础 URL
        timeout: 请求超时时间（秒）
        max_retries: 最大重试次数
    """
    
    def __init__(
        self,
        base_url: str,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
    ):
        """
        初始化基础获取器
        
        Args:
            base_url: API 基础 URL
            timeout: 请求超时时间（秒），默认读取配置
            max_retries: 最大重试次数，默认读取配置
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout or config.http.TIMEOUT
        self.max_retries = max_retries or config.http.MAX_RETRIES
        self.retry_delay = config.http.RETRY_DELAY
        self.retry_backoff = config.http.RETRY_BACKOFF
        self.request_delay = config.http.REQUEST_DELAY
        
        # 会话对象（可复用连接）
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.http.USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        })
        
        # 请求计数器（用于限流）
        self._request_count = 0
        self._last_request_time = 0
    
    def _build_url(self, endpoint: str) -> str:
        """
        构建完整 URL
        
        Args:
            endpoint: API 端点路径
            
        Returns:
            完整 URL
        """
        return urljoin(self.base_url + "/", endpoint.lstrip("/"))
    
    def _should_retry(self, status_code: int) -> bool:
        """
        判断是否应该重试
        
        Args:
            status_code: HTTP 状态码
            
        Returns:
            True 表示可重试，False 表示不应重试
        """
        # 5xx 服务器错误：可重试
        if 500 <= status_code < 600:
            return True
        
        # 429 速率限制：可重试
        if status_code == 429:
            return True
        
        # 408 请求超时：可重试
        if status_code == 408:
            return True
        
        # 其他 4xx 客户端错误：通常不重试（除非是特殊状态码）
        if 400 <= status_code < 500:
            # 408, 429, 499(客户端关闭请求) 可重试
            if status_code in [408, 429, 499]:
                return True
            return False
        
        return False
    
    def _wait_for_rate_limit(self):
        """等待以避免触发速率限制"""
        current_time = time.time()
        elapsed = current_time - self._last_request_time
        
        if elapsed < self.request_delay:
            sleep_time = self.request_delay - elapsed
            logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        
        self._last_request_time = time.time()
    
    def _request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        retry: bool = True,
    ) -> requests.Response:
        """
        发送 HTTP 请求（带重试机制）
        
        Args:
            method: HTTP 方法
            url: 请求 URL
            params: URL 查询参数
            data: 表单数据
            json: JSON 请求体
            headers: 额外请求头
            timeout: 超时时间（秒），默认使用实例配置
            retry: 是否启用重试机制
            
        Returns:
            Response 对象
            
        Raises:
            HTTPError: 请求失败
            RetryableError: 所有重试次数用尽，但仍属于可重试错误
            NonRetryableError: 永久性错误，不应重试
        """
        timeout = timeout or self.timeout
        
        # 合并请求头
        request_headers = self.session.headers.copy()
        if headers:
            request_headers.update(headers)
        
        last_exception = None
        retry_count = 0
        
        while retry_count <= self.max_retries:
            try:
                # 速率限制
                self._wait_for_rate_limit()
                
                # 发送请求
                logger.debug(f"Request {method} {url} (attempt {retry_count + 1})")
                self._request_count += 1
                
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    data=data,
                    json=json,
                    headers=request_headers,
                    timeout=timeout,
                )
                
                # 检查状态码
                if response.status_code == 200:
                    return response
                
                # 判断是否可重试
                if retry and self._should_retry(response.status_code):
                    wait_time = self.retry_delay * (self.retry_backoff ** retry_count)
                    logger.warning(
                        f"Request failed with {response.status_code}, "
                        f"retrying in {wait_time:.1f}s (attempt {retry_count + 1})"
                    )
                    time.sleep(wait_time)
                    retry_count += 1
                    continue
                
                # 不可重试的错误
                error_msg = f"HTTP {response.status_code}: {response.reason}"
                logger.error(f"Request failed: {error_msg}")
                raise HTTPError(
                    message=error_msg,
                    status_code=response.status_code,
                    response_text=response.text[:500] if response.text else None,
                )
                
            except requests.exceptions.Timeout as e:
                last_exception = e
                error_msg = f"Request timeout after {timeout}s"
                logger.warning(f"{error_msg} (attempt {retry_count + 1})")
                
            except requests.exceptions.ConnectionError as e:
                last_exception = e
                error_msg = f"Connection error: {str(e)}"
                logger.warning(f"{error_msg} (attempt {retry_count + 1})")
                
            except requests.exceptions.HTTPError as e:
                last_exception = e
                error_msg = f"HTTP error: {str(e)}"
                logger.warning(f"{error_msg} (attempt {retry_count + 1})")
                
            except requests.exceptions.RequestException as e:
                # 其他请求异常
                last_exception = e
                error_msg = f"Request error: {str(e)}"
                logger.warning(f"{error_msg} (attempt {retry_count + 1})")
            
            # 重试逻辑
            if retry and retry_count < self.max_retries:
                wait_time = self.retry_delay * (self.retry_backoff ** retry_count)
                logger.info(f"Retrying in {wait_time:.1f}s...")
                time.sleep(wait_time)
                retry_count += 1
            else:
                # 所有重试次数用尽
                break
        
        # 所有重试都失败
        final_error = f"Request failed after {self.max_retries + 1} attempts: {last_exception}"
        logger.error(final_error)
        raise HTTPError(message=final_error)
    
    def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        retry: bool = True,
    ) -> requests.Response:
        """
        发送 GET 请求
        
        Args:
            endpoint: API 端点
            params: URL 查询参数
            headers: 额外请求头
            timeout: 超时时间
            retry: 是否重试
            
        Returns:
            Response 对象
        """
        url = self._build_url(endpoint)
        return self._request(
            method="GET",
            url=url,
            params=params,
            headers=headers,
            timeout=timeout,
            retry=retry,
        )
    
    def post(
        self,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        retry: bool = True,
    ) -> requests.Response:
        """
        发送 POST 请求
        
        Args:
            endpoint: API 端点
            data: 表单数据
            json: JSON 请求体
            headers: 额外请求头
            timeout: 超时时间
            retry: 是否重试
            
        Returns:
            Response 对象
        """
        url = self._build_url(endpoint)
        return self._request(
            method="POST",
            url=url,
            data=data,
            json=json,
            headers=headers,
            timeout=timeout,
            retry=retry,
        )
    
    def get_json(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        retry: bool = True,
    ) -> Any:
        """
        发送 GET 请求并返回 JSON 解析结果
        
        Args:
            endpoint: API 端点
            params: URL 查询参数
            headers: 额外请求头
            timeout: 超时时间
            retry: 是否重试
            
        Returns:
            解析后的 JSON 数据
        """
        response = self.get(endpoint, params, headers, timeout, retry)
        
        try:
            return response.json()
        except ValueError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            raise HTTPError(
                message=f"Invalid JSON response: {e}",
                status_code=response.status_code,
                response_text=response.text[:500],
            )
    
    def get_text(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        retry: bool = True,
    ) -> str:
        """
        发送 GET 请求并返回文本内容
        
        Args:
            endpoint: API 端点
            params: URL 查询参数
            headers: 额外请求头
            timeout: 超时时间
            retry: 是否重试
            
        Returns:
            响应文本
        """
        response = self.get(endpoint, params, headers, timeout, retry)
        return response.text
    
    def close(self):
        """关闭会话"""
        self.session.close()
    
    def __enter__(self):
        """上下文管理器入口"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出"""
        self.close()
    
    @property
    def request_count(self) -> int:
        """获取请求计数器"""
        return self._request_count