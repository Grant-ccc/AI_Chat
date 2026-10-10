import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / '.env')

DB_HOST = os.getenv('DB_HOST', '127.0.0.1')
DB_PORT = int(os.getenv('DB_PORT', '3306'))
DB_NAME = os.getenv('DB_NAME', 'umbrella')
DB_USER = os.getenv('DB_USER', 'umbrella')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
APP_ORIGIN = os.getenv('APP_ORIGIN', 'http://localhost:5173').rstrip('/')
COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'false').lower() == 'true'
MERCHANT_USERNAME = os.getenv('MERCHANT_USERNAME', 'merchant')
AI_WEB_MODE = os.getenv('AI_WEB_MODE', 'disabled')
AI_SIMULATION_DATE = os.getenv('AI_SIMULATION_DATE', '2026-05-04')
AI_WEB_MAX_CALLS = int(os.getenv('AI_WEB_MAX_CALLS', '12'))
AI_WEB_BUDGET_CNY = float(os.getenv('AI_WEB_BUDGET_CNY', '1.00'))
AI_PRICE_DATE = os.getenv('AI_PRICE_DATE', '2026-10-10')
