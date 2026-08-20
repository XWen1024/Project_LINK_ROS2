@echo off
title Project LINK - Start Fall Response
ssh orin /home/wte/wheeltec_robot/scripts/standalone/start_fall_response.sh
if errorlevel 1 echo Fall-response startup failed. Review the status above.
pause
