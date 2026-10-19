@echo off
rem Publica o proximo post da fila e guarda o resultado em log-posts.txt
cd /d "%~dp0"
echo ==== %date% %time% >> log-posts.txt
py postar.py proximo >> log-posts.txt 2>&1
