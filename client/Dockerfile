FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY client.py .

RUN mkdir -p /uploads /downloads

CMD ["python", "client.py"]