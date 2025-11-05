from waitress import serve
from app import app  # importa o Flask do seu app.py

serve(app, host='0.0.0.0', port=8080)
