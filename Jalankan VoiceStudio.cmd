@echo off
setlocal
cd /d "%~dp0voicestudio"
set "PATH=%CD%\.local-tools\node_modules\bun\bin;%PATH%"
set "OMNIVOICE_TORCH_VARIANT=cpu"
bun run dev:desktop
