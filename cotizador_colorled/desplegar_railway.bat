@echo off
echo ============================================
echo   DESPLIEGUE COTIZADOR COLOR LED - RAILWAY
echo ============================================
echo.

where railway >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Instalando Railway CLI...
    npm install -g @railway/cli
)

echo Iniciando sesion en Railway...
echo (Se abrira el navegador para confirmar)
railway login

echo.
echo Creando proyecto en Railway...
railway init --name cotizador-colorled

echo.
echo Configurando variables de entorno...
railway variables set ODOO_URL=https://gfgroup.odoo.com
railway variables set ODOO_DB=gfgroup
railway variables set ODOO_USER=colorlednaguanagua@gmail.com
railway variables set ODOO_PASS=GFgroup

echo.
echo Desplegando aplicacion...
railway up --detach

echo.
echo Obteniendo URL publica...
railway domain

echo.
echo ============================================
echo  LISTO! Comparte esa URL con tus vendedores
echo ============================================
pause
