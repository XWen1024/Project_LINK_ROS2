@echo off
title Project LINK - Start Qwen Realtime
ssh orin /home/wte/wheeltec_robot/scripts/standalone/start_qwen_realtime.sh
if errorlevel 1 echo Qwen Realtime startup failed. Review the status above.
pause
