from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from .auth import router as auth_router
from .conversations import router as conversation_router

@asynccontextmanager
async def lifespan(app):
    from .db import engine
    from .init_db import ensure_schema
    from .ai_review import recover_interrupted
    ensure_schema(engine)
    recover_interrupted()
    yield


app = FastAPI(title='花窗伞客服', version='0.2.0', lifespan=lifespan)
app.include_router(auth_router)
app.include_router(conversation_router)


@app.middleware('http')
async def private_responses(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.exception_handler(SQLAlchemyError)
async def database_failure(request, exc):
    return JSONResponse(status_code=503, content={'detail': '数据库暂不可用，请稍后重试。'}, headers={'Cache-Control': 'no-store'})


@app.get('/api/health')
def health():
    from .config import AI_REVIEW_MODE
    return {'status': 'ok', 'stage': 'merchant-reviewed-ai', 'ai_mode': AI_REVIEW_MODE}
