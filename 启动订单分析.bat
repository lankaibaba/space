@echo off
chcp 65001 >nul
echo ========================================
echo   零担订单分析系统 - 启动脚本
echo ========================================
echo.
echo 正在启动服务器...
echo 访问地址: http://localhost:5001
echo.
echo 按 Ctrl+C 停止服务器
echo ========================================
echo.
python order_analysis.py
pause
