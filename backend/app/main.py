from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from .auth import router as auth_router

app = FastAPI(title='花窗伞人工接待', version='0.1.0')
app.include_router(auth_router)


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
    return {'status': 'ok', 'stage': 'human-only'}
