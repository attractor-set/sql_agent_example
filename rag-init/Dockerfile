FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir -U pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rag_setup.py .

ENV PYTHONUNBUFFERED=1

CMD ["python", "rag_setup.py"]
