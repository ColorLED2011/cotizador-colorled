@echo off
echo Instalando dependencias...
pip install -r requirements.txt --quiet
echo.
echo Iniciando Cotizador COLOR LED...
echo Abre tu navegador en: http://localhost:5000
echo.
python app.py
pause
