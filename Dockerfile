FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . /app

EXPOSE 5000 8000

CMD ["python", "-u", "manager_app.py", "--config", "cluster_config.docker.json", "--host", "0.0.0.0", "--port", "5000"]
