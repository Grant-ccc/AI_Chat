from fastapi import FastAPI

app = FastAPI(title='花窗伞人工接待', version='0.1.0')


@app.get('/api/health')
def health():
    return {'status': 'ok', 'stage': 'human-only'}
