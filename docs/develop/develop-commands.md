1. 启动后端：

```shell
cd backend
uv run uvicorn app.main:app --port 8000 --loop app.cli:_windows_selector_loop_factory
```

2. 启动前端：

```shell
cd frontend
pnpm dev
```