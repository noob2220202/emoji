"""pm2 등 외부 프로세스 매니저에서 `python3 -m`을 쓰지 않고도 바로 실행할 수 있게 해주는
얇은 진입점. bot 패키지의 상대 임포트가 정상 동작하려면 python -m bot.main처럼
패키지로 실행하거나, 이 스크립트처럼 저장소 루트에서 일반 스크립트로 실행해야 한다."""

from bot.main import main

if __name__ == "__main__":
    main()
