FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY datanode.py .

RUN mkdir -p /data/blocks

EXPOSE 5001

CMD ["uvicorn", "datanode:app", "--host", "0.0.0.0", "--port", "5001"]
