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
        // bot/__init__.py에서도 설정하지만, 혹시 다른 방식으로 실행될 때도 안전하게
        // numba JIT을 꺼서 rembg 임포트가 느려지거나 멈추는 것을 막는다.
        NUMBA_DISABLE_JIT: "1",
      },
    },
  ],
};
