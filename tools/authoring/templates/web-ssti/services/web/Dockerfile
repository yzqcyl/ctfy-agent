FROM python:3.12-slim-bookworm

# TUNA 软件源在比赛允许清单内；版本钉死保证 smoke 可复现。
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
        flask==3.0.3

WORKDIR /app
COPY app /app

EXPOSE 8000
CMD ["python", "app.py"]