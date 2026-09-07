import os

# 배경 제거(rembg)가 내부적으로 끌어오는 pymatting/numba는 처음 임포트될 때
# JIT 컴파일을 시도하는데, CPU 자원이 빠듯한(다른 프로세스와 공유하는) 서버에서는
# 이 컴파일이 수십 초~몇 분씩 걸리거나 사실상 멈춘 것처럼 보일 수 있다. 이 봇은
# numba의 실행 속도 이득이 필요 없으므로 JIT을 꺼서 즉시 순수 파이썬으로 실행되게 한다.
# bot 패키지의 어떤 서브모듈을 가장 먼저 임포트하든 __init__.py가 항상 먼저 실행되므로,
# 이 값을 여기서 설정해야 numba가 임포트되기 전에 확실히 적용된다.
os.environ.setdefault("NUMBA_DISABLE_JIT", "1")
