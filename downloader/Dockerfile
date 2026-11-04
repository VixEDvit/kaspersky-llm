FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 10001 loader && \
    useradd --uid 10001 --gid 10001 --create-home loader
COPY download.py .
USER 10001:10001
ENV HOME=/home/loader

ENTRYPOINT ["python", "/app/download.py"]
