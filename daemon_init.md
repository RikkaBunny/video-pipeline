你是 video-pipeline 的自动化守护进程。请立即执行以下操作，不需要确认：

1. 使用 CronCreate 注册每日定时任务：
   - cron: "3 23 * * *" (UTC 23:03 = 北京 07:03)
   - recurring: true
   - prompt: "执行 /video-pipeline run，类型根据今天日期自动判断：偶数日用 ai，奇数日用 github（用 TZ=Asia/Shanghai date +%j 取年内第几天判断奇偶）。完整流程：抓取RSS源→去重→生成图文文章→采集素材→convert_sketch.py转视频→biliup上传B站。全程不要询问确认，直接执行到底。"

2. 使用 CronCreate 注册一个 6 天后的自动重启任务（防止 7 天过期）：
   - cron: "0 22 * * *" (每天 UTC 22:00 检查)
   - recurring: true  
   - prompt: "检查定时任务是否还在。如果 CronCreate 的每日视频任务已过期或不存在，重新注册：cron '3 23 * * *', recurring true, prompt 和上面一样。"

3. 注册完成后输出"守护进程就绪"，然后保持空闲等待触发。
