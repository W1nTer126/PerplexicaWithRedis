from flask import Flask, request, jsonify
from flask_cors import CORS
import redis
import requests
import json
import logging
from datetime import datetime
import os
from dotenv import load_dotenv
import toml
import time

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Redis配置
def get_redis_client():
    max_retries = 5
    retry_delay = 2  # 秒
    
    for attempt in range(max_retries):
        try:
            client = redis.Redis(
                host=os.getenv('REDIS_HOST', 'localhost'),
                port=int(os.getenv('REDIS_PORT', 6379)),
                db=0,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5
            )
            # 测试连接
            client.ping()
            logger.info("Redis连接成功")
            return client
        except redis.ConnectionError as e:
            if attempt < max_retries - 1:
                logger.warning(f"Redis连接失败，{retry_delay}秒后重试: {str(e)}")
                time.sleep(retry_delay)
            else:
                logger.error("Redis连接失败，已达到最大重试次数")
                raise
        except Exception as e:
            logger.error(f"Redis连接发生未知错误: {str(e)}")
            raise

try:
    redis_client = get_redis_client()
except Exception as e:
    logger.error(f"初始化Redis客户端失败: {str(e)}")
    redis_client = None

# 加载Perplexica配置
def load_perplexica_config():
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.toml')
    if not os.path.exists(config_path):
        logger.warning(f"配置文件不存在: {config_path}，使用默认配置")
        return {
            'API_ENDPOINTS': {'SEARXNG': None},
            'MODELS': {
                'OLLAMA': {
                    'API_URL': 'http://localhost:11434',
                    'DEFAULT_MODEL': 'llama2'
                }
            }
        }
    
    try:
        with open(config_path, 'r') as f: 
            return toml.load(f)
    except Exception as e:
        logger.error(f"加载配置文件失败: {str(e)}")
        return {
            'API_ENDPOINTS': {'SEARXNG': None},
            'MODELS': {
                'OLLAMA': {
                    'API_URL': 'http://localhost:11434',
                    'DEFAULT_MODEL': 'llama2'
                }
            }
        }

config = load_perplexica_config()
SEARXNG_URL = config.get('API_ENDPOINTS', {}).get('SEARXNG')
OLLAMA_URL = config.get('MODELS', {}).get('OLLAMA', {}).get('API_URL', 'http://localhost:11434')
OLLAMA_DEFAULT_MODEL = config.get('MODELS', {}).get('OLLAMA', {}).get('DEFAULT_MODEL', 'deepseek-r1:1.5b')
CACHE_EXPIRY = 300  # 5分钟缓存过期时间

# 检查Ollama连接状态和可用模型
def check_ollama_connection():
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        response.raise_for_status()
        models = response.json().get('models', [])
        logger.info(f"Ollama连接成功，可用模型: {', '.join([m.get('name', '') for m in models])}")
        return True, models
    except Exception as e:
        logger.error(f"Ollama连接失败: {str(e)}")
        return False, []

# 检查模型是否可用
def check_model_availability(model_name: str) -> bool:
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        response.raise_for_status()
        models = response.json().get('models', [])
        return any(m.get('name') == model_name for m in models)
    except:
        return False

# 获取默认可用模型
def get_default_available_model(models: list, preferred_model: str) -> str:
    # 首选模型列表
    preferred_models = [
        preferred_model,
        'llama2',
        'mistral',
        'codellama',
        'neural-chat'
    ]
    
    # 检查首选模型是否可用
    for model in preferred_models:
        if any(m.get('name') == model for m in models):
            logger.info(f"选择默认模型: {model}")
            return model
    
    # 如果没有首选模型可用，使用第一个可用模型
    if models:
        first_model = models[0].get('name', '')
        logger.info(f"没有首选模型可用，使用第一个可用模型: {first_model}")
        return first_model
    
    raise ValueError("没有可用的模型，请至少下载一个模型")

# 初始化时检查Ollama连接和默认模型
OLLAMA_AVAILABLE, AVAILABLE_MODELS = check_ollama_connection()
if OLLAMA_AVAILABLE:
    try:
        OLLAMA_DEFAULT_MODEL = get_default_available_model(AVAILABLE_MODELS, OLLAMA_DEFAULT_MODEL)
    except ValueError as e:
        logger.error(f"初始化默认模型失败: {str(e)}")
        OLLAMA_AVAILABLE = False
        OLLAMA_DEFAULT_MODEL = None

def search_searxng(query: str, options: dict = None) -> dict:
    """
    调用SearxNG API进行搜索
    """
    if not SEARXNG_URL:
        raise ValueError("SearxNG URL未配置")
        
    url = f"{SEARXNG_URL}/search"
    params = {
        'q': query,
        'format': 'json'
    }
    
    if options:
        for key, value in options.items():
            if isinstance(value, list):
                params[key] = ','.join(value)
            else:
                params[key] = str(value)
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"SearxNG搜索错误: {str(e)}")
        raise

def query_ollama(query: str, model: str = None) -> dict:
    """
    调用Ollama API进行对话
    """
    if not OLLAMA_AVAILABLE:
        raise ValueError("Ollama服务不可用")
    
    # 如果没有指定模型，使用默认模型
    if not model:
        model = OLLAMA_DEFAULT_MODEL
        if not model:
            raise ValueError("没有可用的默认模型")
        logger.info(f"使用默认模型: {model}")
        
    try:
        # 首先检查Ollama服务是否运行
        try:
            response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            response.raise_for_status()
        except requests.exceptions.ConnectionError:
            raise ValueError(f"无法连接到Ollama服务，请确保Ollama服务正在运行（地址：{OLLAMA_URL}）")
        except requests.exceptions.RequestException as e:
            raise ValueError(f"Ollama服务检查失败：{str(e)}")

        # 检查模型是否已下载
        if not check_model_availability(model):
            raise ValueError(f"模型 {model} 未下载，请先运行 'ollama pull {model}' 命令下载模型")

        # 发送生成请求
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
                "prompt": query,
                "stream": False
            },
            timeout=30  # 增加超时时间
        )
        response.raise_for_status()
        result = response.json()
        
        return {
            'message': result.get('response', ''),
            'sources': [],  # Ollama没有来源信息
            'model': model  # 添加模型信息
        }
    except ValueError as e:
        logger.error(f"Ollama配置错误: {str(e)}")
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Ollama API请求失败: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Ollama API未知错误: {str(e)}")
        raise

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        query = data.get('query')
        model = data.get('model')  # 从请求中获取模型参数
        
        if not query:
            return jsonify({'error': '查询参数不能为空'}), 400

        # 生成缓存key（包含模型信息）
        cache_key = f"chat:{model or OLLAMA_DEFAULT_MODEL}:{query}"
        
        # 检查Redis缓存
        try:
            if redis_client:
                cached_result = redis_client.get(cache_key)
                if cached_result:
                    logger.info(f"缓存命中 (Cache Hit) - 查询: {query}, 模型: {model or OLLAMA_DEFAULT_MODEL}")
                    return jsonify(json.loads(cached_result))
                
                logger.info(f"缓存未命中 (Cache Miss) - 查询: {query}, 模型: {model or OLLAMA_DEFAULT_MODEL}")
        except redis.RedisError as e:
            logger.error(f"Redis操作失败: {str(e)}")
        
        try:
            # 首先尝试使用SearxNG
            if SEARXNG_URL:
                logger.info("使用SearxNG进行搜索")
                search_result = search_searxng(query, {
                    'language': 'en',
                    'engines': ['google', 'bing', 'wikipedia']
                })
                
                response_data = {
                    'message': search_result.get('results', [{}])[0].get('content', ''),
                    'sources': [
                        {
                            'title': result.get('title', ''),
                            'url': result.get('url', ''),
                            'content': result.get('content', '')
                        }
                        for result in search_result.get('results', [])
                    ],
                    'model': 'searxng'
                }
            else:
                # 如果SearxNG未配置，使用Ollama
                if not OLLAMA_AVAILABLE:
                    return jsonify({
                        'error': 'Ollama服务不可用，请检查Ollama服务是否正常运行',
                        'status': 'ollama_unavailable',
                        'details': '请确保Ollama服务已启动，并且可以通过配置的URL访问'
                    }), 503
                    
                logger.info(f"使用Ollama进行对话，模型: {model or OLLAMA_DEFAULT_MODEL}")
                response_data = query_ollama(query, model)
                
        except ValueError as e:
            logger.error(f"服务配置错误: {str(e)}")
            return jsonify({
                'error': str(e),
                'status': 'configuration_error'
            }), 503
        except requests.exceptions.RequestException as e:
            logger.error(f"服务请求失败: {str(e)}")
            return jsonify({
                'error': f"服务请求失败: {str(e)}",
                'status': 'request_failed'
            }), 503
        except Exception as e:
            logger.error(f"主要服务调用失败: {str(e)}")
            # 如果主要服务失败，尝试使用Ollama作为备选
            if OLLAMA_AVAILABLE:
                logger.info(f"尝试使用Ollama作为备选方案，模型: {model or OLLAMA_DEFAULT_MODEL}")
                try:
                    response_data = query_ollama(query, model)
                except Exception as ollama_error:
                    return jsonify({
                        'error': '所有服务都不可用，请检查服务状态',
                        'status': 'all_services_unavailable',
                        'details': str(ollama_error)
                    }), 503
            else:
                return jsonify({
                    'error': '所有服务都不可用，请检查服务状态',
                    'status': 'all_services_unavailable'
                }), 503
        
        # 存储到Redis缓存
        try:
            if redis_client:
                redis_client.setex(
                    cache_key,
                    CACHE_EXPIRY,
                    json.dumps(response_data)
                )
        except redis.RedisError as e:
            logger.error(f"Redis缓存存储失败: {str(e)}")
        
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"处理请求时发生错误: {str(e)}")
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    """
    获取服务状态
    """
    return jsonify({
        'ollama_available': OLLAMA_AVAILABLE,
        'redis_available': redis_client is not None,
        'searxng_available': bool(SEARXNG_URL),
        'ollama_url': OLLAMA_URL,
        'default_model': OLLAMA_DEFAULT_MODEL,
        'available_models': [m.get('name', '') for m in AVAILABLE_MODELS]
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True) 