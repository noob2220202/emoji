// pm2로 봇을 실행하기 위한 설정.
// 사용법: pm2 start ecosystem.config.js
module.exports = {
  apps: [
    {
      name: "emoji-bot",
      script: "run.py",
      interpreter: "python3",
      cwd: __dirname,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 20,
      watch: false,
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
