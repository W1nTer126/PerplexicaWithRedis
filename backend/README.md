# Perplexica 后端服务

这是一个基于 Flask 的后端服务，为 Perplexica 提供搜索和 AI 对话功能，包含 Redis 缓存支持。

## 功能特点

- 提供 `/api/chat` 接口处理搜索和 AI 对话请求
- 使用 Redis 实现查询结果缓存
- 支持 SearxNG 搜索服务集成
- 详细的日志记录，包括缓存命中情况

## 环境要求

- Python 3.9+
- Redis 服务器
- SearxNG 搜索服务

## 安装步骤

1. 创建虚拟环境（推荐）：
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
.\venv\Scripts\activate  # Windows
```

2. 安装依赖：
```bash
pip install -r requirements.txt
```

3. 配置环境变量：
复制 `.env.example` 为 `.env` 并修改相关配置

## 运行服务

```bash
python app.py
```

服务将在 http://localhost:5000 启动

## API 使用说明

### POST /api/chat

请求体格式：
```json
{
    "query": "你的搜索查询"
}
```

响应格式：
```json
{
    "results": [...],
    "cached": true/false
}
```

## 缓存说明

- 缓存过期时间：5分钟
- 缓存键格式：`chat:{query}`
- 日志中会显示缓存命中（Cache Hit）和未命中（Cache Miss）情况 